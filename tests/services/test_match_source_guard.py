"""Tests for the SpeedGaming per-field read-only guard (PR 7).

The unit tests exercise the pure helper; the end-to-end test drives
``MatchService.update_match`` on a sourced match to prove the guard rejects an
ETL-owned edit while leaving Wizzrobe-owned fields (comment) editable.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.services.match.match_source_guard import assert_sg_fields_unchanged


def _sourced(scheduled_at=datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)):
    return SimpleNamespace(speedgaming_episode_id=5, tournament_id=1, scheduled_at=scheduled_at)


def test_guard_noop_for_unsourced_match():
    match = SimpleNamespace()  # no speedgaming_episode_id attribute at all
    assert_sg_fields_unchanged(
        match, tournament_id=99, scheduled_date='2030-01-01', scheduled_time='12:00',
        players_changed=True,
    )  # does not raise


def test_guard_rejects_player_change():
    with pytest.raises(ValueError, match='players'):
        assert_sg_fields_unchanged(
            _sourced(), tournament_id=None, scheduled_date=None,
            scheduled_time=None, players_changed=True,
        )


def test_guard_rejects_tournament_change():
    with pytest.raises(ValueError, match='tournament'):
        assert_sg_fields_unchanged(
            _sourced(), tournament_id=2, scheduled_date=None,
            scheduled_time=None, players_changed=False,
        )


def test_guard_allows_resubmitting_same_schedule():
    # The dialog resubmits the disabled schedule fields unchanged (Eastern of the
    # stored 18:00 UTC is 14:00 EDT); that must NOT be treated as an edit.
    assert_sg_fields_unchanged(
        _sourced(), tournament_id=1, scheduled_date='2026-07-20',
        scheduled_time='14:00', players_changed=False,
    )


def test_guard_rejects_schedule_change():
    with pytest.raises(ValueError, match='scheduled time'):
        assert_sg_fields_unchanged(
            _sourced(), tournament_id=None, scheduled_date='2026-07-25',
            scheduled_time='14:00', players_changed=False,
        )


async def test_update_match_rejects_etl_field_but_allows_comment(db):
    from application.repositories import UserRepository
    from application.services.match.match_service import MatchService
    from application.services.speedgaming_etl_service import SpeedGamingETLService
    from application.utils.clients.speedgaming_client import MockSpeedGamingClient
    from models import Match, Role, SpeedGamingEventLink, Tournament, User, UserRole

    system = await UserRepository.get_or_create_system_user()
    tourn = await Tournament.create(name='T')
    link = await SpeedGamingEventLink.create(tournament=tourn, event_slug='ev')
    etl = SpeedGamingETLService(client=MockSpeedGamingClient([]))
    raw = {'id': 200, 'when': '2026-07-20T18:00:00+00:00', 'title': 'R1',
           'match1': {'players': [{'id': 1, 'discordId': None, 'discordTag': 'p1'}]}}
    await etl.import_episode(link, raw, actor=system)
    match = await Match.filter(speedgaming_episode__sg_episode_id='200').first()

    staff = await User.create(discord_id=999, username='staff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)

    service = MatchService()
    with pytest.raises(ValueError, match='synced from SpeedGaming'):
        await service.update_match(
            match_id=match.id, scheduled_date='2026-08-01', scheduled_time='12:00',
            actor=staff,
        )
    # A Wizzrobe-owned field (comment) still updates.
    updated = await service.update_match(match_id=match.id, comment='caster note', actor=staff)
    assert updated.comment == 'caster note'
