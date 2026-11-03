"""Players asking staff to move or call off a match.

Three things carry the design and each has tests here: only a match's own
players may ask, approving *performs* the change through ``MatchService``
rather than blessing it, and deciding needs the same authority that editing the
match needs.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from application.events import EventType, event_bus
from application.services import MatchRescheduleService
from models import (
    Match,
    MatchPlayers,
    MatchRescheduleRequest,
    RescheduleRequestKind,
    RescheduleRequestStatus,
    Role,
    SpeedGamingEpisode,
    Tournament,
    UserRole,
)
from tests.factories import make_user, utc


@pytest.fixture
def captured_events():
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    yield seen
    event_bus.unsubscribe(token)


def _soon(hours: int = 6) -> datetime:
    """A real future instant — the service rejects a proposal in the past."""
    return datetime.now(timezone.utc) + timedelta(hours=hours)


async def _match(*, seated=False, started=False, scheduled=True, sg=False, allow=True):
    t = await Tournament.create(name='T', allow_reschedule_requests=allow)
    m = await Match.create(
        tournament=t,
        scheduled_at=utc(2025, 1, 15, 19, 30) if scheduled else None,
        seated_at=utc(2025, 1, 15, 19, 25) if seated else None,
        started_at=utc(2025, 1, 15, 19, 35) if started else None,
    )
    if sg:
        episode = await SpeedGamingEpisode.create(sg_episode_id='ep-1')
        m.speedgaming_episode_id = episode.id
        await m.save()
    p1 = await make_user(discord_id=9001, username='p1')
    p2 = await make_user(discord_id=9002, username='p2')
    await MatchPlayers.create(match=m, user=p1)
    await MatchPlayers.create(match=m, user=p2)
    return m, p1, p2


async def _staff(discord_id=9100):
    user = await make_user(discord_id=discord_id, username='boss')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _submit(match, actor, **kwargs):
    kwargs.setdefault('reason', 'work clash')
    return await MatchRescheduleService().submit(match.id, actor, **kwargs)


class TestSubmitting:
    async def test_a_player_can_ask_for_their_own_match(self, db):
        m, p1, _ = await _match()

        request = await _submit(m, p1, proposed_at=_soon())

        assert request.status is RescheduleRequestStatus.PENDING
        assert request.requested_by_id == p1.id

    async def test_the_original_time_is_snapshotted(self, db):
        """Approving overwrites scheduled_at, so what it moved *from* is captured now."""
        m, p1, _ = await _match()

        request = await _submit(m, p1, proposed_at=_soon())

        assert request.original_scheduled_at == m.scheduled_at

    async def test_a_non_player_is_refused(self, db):
        m, _, _ = await _match()
        outsider = await make_user(discord_id=9003, username='nosy')

        with pytest.raises(PermissionError, match='players in a match'):
            await _submit(m, outsider, proposed_at=_soon())

    async def test_staff_are_refused_too(self, db):
        """Not an oversight: staff already move matches directly."""
        m, _, _ = await _match()
        boss = await _staff()

        with pytest.raises(PermissionError):
            await _submit(m, boss, proposed_at=_soon())

    async def test_a_match_under_way_is_refused(self, db):
        m, p1, _ = await _match(seated=True)

        with pytest.raises(ValueError, match='under way'):
            await _submit(m, p1, proposed_at=_soon())

    async def test_an_unscheduled_match_is_refused(self, db):
        m, p1, _ = await _match(scheduled=False)

        with pytest.raises(ValueError, match="doesn't have a time"):
            await _submit(m, p1, proposed_at=_soon())

    async def test_a_speedgaming_match_is_refused(self, db):
        """Its time belongs to the next sync, so no one here could approve it."""
        m, p1, _ = await _match(sg=True)

        with pytest.raises(ValueError, match='SpeedGaming'):
            await _submit(m, p1, proposed_at=_soon())

    async def test_a_tournament_can_turn_requests_off(self, db):
        m, p1, _ = await _match(allow=False)

        with pytest.raises(PermissionError, match="doesn't take reschedule requests"):
            await _submit(m, p1, proposed_at=_soon())

    async def test_a_reason_is_required(self, db):
        m, p1, _ = await _match()

        with pytest.raises(ValueError, match='Say why'):
            await _submit(m, p1, reason='   ', proposed_at=_soon())

    async def test_a_past_time_is_refused(self, db):
        m, p1, _ = await _match()

        with pytest.raises(ValueError, match='in the future'):
            await _submit(m, p1, proposed_at=utc(2020, 1, 1, 12, 0))

    async def test_only_one_open_request_per_player(self, db):
        m, p1, _ = await _match()
        await _submit(m, p1, proposed_at=_soon())

        with pytest.raises(ValueError, match='already have a request'):
            await _submit(m, p1, proposed_at=_soon(8))

    async def test_the_opponent_may_still_ask_separately(self, db):
        """The cap is per player, not per match — both may have a live ask."""
        m, p1, p2 = await _match()
        await _submit(m, p1, proposed_at=_soon())

        await _submit(m, p2, proposed_at=_soon(9))

        assert await MatchRescheduleRequest.filter(
            match=m, status=RescheduleRequestStatus.PENDING,
        ).count() == 2

    async def test_a_cancellation_proposes_no_time(self, db):
        m, p1, _ = await _match()

        request = await _submit(
            m, p1, kind=RescheduleRequestKind.CANCEL, proposed_at=_soon(),
        )

        assert request.proposed_at is None

    async def test_it_publishes_a_domain_event(self, db, captured_events):
        m, p1, _ = await _match()

        await _submit(m, p1, proposed_at=_soon())

        assert any(
            e.event_type == EventType.MATCH_RESCHEDULE_REQUESTED
            for e in captured_events
        )


class TestOpponentAgreement:
    async def test_the_other_player_can_agree(self, db):
        m, p1, p2 = await _match()
        request = await _submit(m, p1, proposed_at=_soon())

        await MatchRescheduleService().record_opponent_agreement(request.id, p2)

        await request.refresh_from_db()
        assert request.opponent_agreed_at is not None

    async def test_agreement_does_not_decide_anything(self, db):
        """A signal, not a gate: the request is still waiting on staff."""
        m, p1, p2 = await _match()
        request = await _submit(m, p1, proposed_at=_soon())

        await MatchRescheduleService().record_opponent_agreement(request.id, p2)

        await request.refresh_from_db()
        assert request.status is RescheduleRequestStatus.PENDING

    async def test_the_requester_cannot_agree_with_themselves(self, db):
        m, p1, _ = await _match()
        request = await _submit(m, p1, proposed_at=_soon())

        with pytest.raises(PermissionError, match='your own request'):
            await MatchRescheduleService().record_opponent_agreement(request.id, p1)

    async def test_an_outsider_cannot_agree(self, db):
        m, p1, _ = await _match()
        request = await _submit(m, p1, proposed_at=_soon())
        outsider = await make_user(discord_id=9004, username='nosy')

        with pytest.raises(PermissionError, match='other player'):
            await MatchRescheduleService().record_opponent_agreement(request.id, outsider)


class TestWithdrawing:
    async def test_the_requester_can_take_it_back(self, db):
        m, p1, _ = await _match()
        request = await _submit(m, p1, proposed_at=_soon())

        await MatchRescheduleService().withdraw(request.id, p1)

        await request.refresh_from_db()
        assert request.status is RescheduleRequestStatus.WITHDRAWN

    async def test_nobody_else_can(self, db):
        m, p1, p2 = await _match()
        request = await _submit(m, p1, proposed_at=_soon())

        with pytest.raises(PermissionError, match='who asked'):
            await MatchRescheduleService().withdraw(request.id, p2)


class TestApproving:
    async def test_it_moves_the_match(self, db):
        """The core promise: approving performs the change, not just records it."""
        m, p1, _ = await _match()
        boss = await _staff()
        when = _soon()
        request = await _submit(m, p1, proposed_at=when)

        with patch(
            'application.services.match.match_service.MatchService.update_match',
            new_callable=AsyncMock,
        ) as update:
            await MatchRescheduleService().approve(request.id, boss)

        update.assert_awaited_once()
        assert update.await_args.kwargs['scheduled_at'] == when
        assert update.await_args.kwargs['actor'] == boss

    async def test_staff_can_counter_with_a_different_time(self, db):
        m, p1, _ = await _match()
        boss = await _staff()
        request = await _submit(m, p1, proposed_at=_soon())
        theirs = _soon(20)

        with patch(
            'application.services.match.match_service.MatchService.update_match',
            new_callable=AsyncMock,
        ) as update:
            await MatchRescheduleService().approve(request.id, boss, scheduled_at=theirs)

        assert update.await_args.kwargs['scheduled_at'] == theirs

    async def test_a_cancellation_calls_the_match_off(self, db):
        m, p1, _ = await _match()
        boss = await _staff()
        request = await _submit(m, p1, kind=RescheduleRequestKind.CANCEL)

        with patch(
            'application.services.match.match_service.MatchService.cancel_match',
            new_callable=AsyncMock,
        ) as cancel:
            await MatchRescheduleService().approve(request.id, boss)

        cancel.assert_awaited_once()

    async def test_a_request_with_no_time_needs_one(self, db):
        """"You pick" is a valid ask; approving it blind is not a valid answer."""
        m, p1, _ = await _match()
        boss = await _staff()
        request = await _submit(m, p1)

        with pytest.raises(ValueError, match="didn't name a time"):
            await MatchRescheduleService().approve(request.id, boss)

    async def test_a_player_cannot_approve_their_own_request(self, db):
        m, p1, _ = await _match()
        request = await _submit(m, p1, proposed_at=_soon())

        with pytest.raises(PermissionError):
            await MatchRescheduleService().approve(request.id, p1)

    async def test_the_opponent_cannot_approve_it_either(self, db):
        """Agreement is the opponent's whole power — deciding is not theirs."""
        m, p1, p2 = await _match()
        request = await _submit(m, p1, proposed_at=_soon())

        with pytest.raises(PermissionError):
            await MatchRescheduleService().approve(request.id, p2)

    async def test_a_failed_move_leaves_the_request_pending(self, db):
        """Perform-then-record: never 'approved' over a schedule that did not budge."""
        m, p1, _ = await _match()
        boss = await _staff()
        request = await _submit(m, p1, proposed_at=_soon())

        with patch(
            'application.services.match.match_service.MatchService.update_match',
            new_callable=AsyncMock, side_effect=ValueError('outside tournament hours'),
        ):
            with pytest.raises(ValueError, match='outside tournament hours'):
                await MatchRescheduleService().approve(request.id, boss)

        await request.refresh_from_db()
        assert request.status is RescheduleRequestStatus.PENDING

    async def test_the_other_open_request_is_superseded_not_declined(self, db):
        """Staff refused nobody; the match simply stopped being in question."""
        m, p1, p2 = await _match()
        boss = await _staff()
        mine = await _submit(m, p1, proposed_at=_soon())
        theirs = await _submit(m, p2, proposed_at=_soon(9))

        with patch(
            'application.services.match.match_service.MatchService.update_match',
            new_callable=AsyncMock,
        ):
            await MatchRescheduleService().approve(mine.id, boss)

        await theirs.refresh_from_db()
        assert theirs.status is RescheduleRequestStatus.SUPERSEDED

    async def test_deciding_twice_is_refused(self, db):
        m, p1, _ = await _match()
        boss = await _staff()
        request = await _submit(m, p1, proposed_at=_soon())

        with patch(
            'application.services.match.match_service.MatchService.update_match',
            new_callable=AsyncMock,
        ):
            await MatchRescheduleService().approve(request.id, boss)

            with pytest.raises(ValueError, match='already been decided'):
                await MatchRescheduleService().approve(request.id, boss)


class TestDeclining:
    async def test_it_records_staffs_words(self, db):
        m, p1, _ = await _match()
        boss = await _staff()
        request = await _submit(m, p1, proposed_at=_soon())

        await MatchRescheduleService().decline(request.id, boss, 'restream is locked in')

        await request.refresh_from_db()
        assert request.status is RescheduleRequestStatus.DECLINED
        assert request.decision_note == 'restream is locked in'
        assert request.decided_by_id == boss.id

    async def test_a_bare_no_is_refused(self, db):
        """A refusal with no reason is what this feature exists to replace."""
        m, p1, _ = await _match()
        boss = await _staff()
        request = await _submit(m, p1, proposed_at=_soon())

        with pytest.raises(ValueError, match='Say why'):
            await MatchRescheduleService().decline(request.id, boss, '   ')

    async def test_declining_leaves_the_match_alone(self, db):
        m, p1, _ = await _match()
        boss = await _staff()
        before = m.scheduled_at
        request = await _submit(m, p1, proposed_at=_soon())

        await MatchRescheduleService().decline(request.id, boss, 'no room')

        await m.refresh_from_db()
        assert m.scheduled_at == before

    async def test_a_player_cannot_decline(self, db):
        m, p1, p2 = await _match()
        request = await _submit(m, p1, proposed_at=_soon())

        with pytest.raises(PermissionError):
            await MatchRescheduleService().decline(request.id, p2, 'nope')


class TestReads:
    async def test_a_player_sees_their_own_requests(self, db):
        m, p1, p2 = await _match()
        mine = await _submit(m, p1, proposed_at=_soon())
        await _submit(m, p2, proposed_at=_soon(9))

        rows = await MatchRescheduleService().list_mine(p1)

        assert [r.id for r in rows] == [mine.id]

    async def test_the_queue_holds_only_pending_ones(self, db):
        m, p1, p2 = await _match()
        boss = await _staff()
        await _submit(m, p1, proposed_at=_soon())
        decided = await _submit(m, p2, proposed_at=_soon(9))
        await MatchRescheduleService().decline(decided.id, boss, 'no')

        assert await MatchRescheduleService().pending_count() == 1

    async def test_can_request_is_false_once_one_is_open(self, db):
        """What hides the button, so it is never shown only to be refused."""
        m, p1, _ = await _match()
        service = MatchRescheduleService()
        assert await service.can_request(m, p1) is True

        await _submit(m, p1, proposed_at=_soon())

        assert await service.can_request(m, p1) is False

    async def test_can_request_is_false_for_a_non_player(self, db):
        m, _, _ = await _match()
        outsider = await make_user(discord_id=9005, username='nosy')

        assert await MatchRescheduleService().can_request(m, outsider) is False

    async def test_can_request_is_false_when_signed_out(self, db):
        m, _, _ = await _match()

        assert await MatchRescheduleService().can_request(m, None) is False


class TestOverrideRecording:
    """Whether the audit says staff changed the player's proposed time."""

    def test_approving_the_proposed_minute_is_not_an_override(self):
        """The decision dialog always resubmits its pickers, which carry only
        HH:MM. Without a tolerance every plain approval would be recorded as
        staff having overridden the player."""
        from application.services.match_reschedule_service import _is_override

        proposed = utc(2026, 6, 1, 19, 30).replace(second=41)
        chosen = utc(2026, 6, 1, 19, 30)

        assert _is_override(chosen, proposed) is False

    def test_a_different_time_is_an_override(self):
        from application.services.match_reschedule_service import _is_override

        assert _is_override(utc(2026, 6, 1, 21, 0), utc(2026, 6, 1, 19, 30)) is True

    def test_no_chosen_time_is_not_an_override(self):
        from application.services.match_reschedule_service import _is_override

        assert _is_override(None, utc(2026, 6, 1, 19, 30)) is False


class TestBulkGateMatchesTheSingleGate:
    """`list_requestable_match_ids` is a second implementation of `can_request`
    against a different query, and it is the one that decides whether the board
    shows the button. Its docstring claims "same rules" — this asserts it."""

    async def _matrix(self, player):
        """One match per reason a request is or is not possible."""
        from models import SpeedGamingEpisode

        allowed = await Tournament.create(name='Open', allow_reschedule_requests=True)
        refused = await Tournament.create(name='Closed', allow_reschedule_requests=False)

        async def match(tournament, **kwargs):
            m = await Match.create(tournament=tournament, **kwargs)
            await MatchPlayers.create(match=m, user=player)
            return m

        ok = await match(allowed, scheduled_at=utc(2026, 6, 1, 19, 30))
        unscheduled = await match(allowed, scheduled_at=None)
        seated = await match(
            allowed, scheduled_at=utc(2026, 6, 1, 19, 30),
            seated_at=utc(2026, 6, 1, 19, 25),
        )
        confirmed = await match(
            allowed, scheduled_at=utc(2026, 6, 1, 19, 30),
            seated_at=utc(2026, 6, 1, 19, 25), started_at=utc(2026, 6, 1, 19, 35),
            finished_at=utc(2026, 6, 1, 21, 0), confirmed_at=utc(2026, 6, 1, 21, 5),
        )
        toggled_off = await match(refused, scheduled_at=utc(2026, 6, 1, 19, 30))
        episode = await SpeedGamingEpisode.create(sg_episode_id='ep-matrix')
        sourced = await match(allowed, scheduled_at=utc(2026, 6, 1, 19, 30))
        sourced.speedgaming_episode_id = episode.id
        await sourced.save()
        return ok, [unscheduled, seated, confirmed, toggled_off, sourced]

    async def test_the_bulk_gate_agrees_with_can_request_on_every_case(self, db):
        player = await make_user(discord_id=9500, username='matrix')
        ok, blocked = await self._matrix(player)
        service = MatchRescheduleService()

        bulk = await service.list_requestable_match_ids(player)

        for m in [ok, *blocked]:
            fresh = await Match.get(id=m.id).prefetch_related('players')
            assert (m.id in bulk) is await service.can_request(fresh, player), (
                f'match {m.id} disagrees between the bulk gate and can_request'
            )

    async def test_the_bulk_gate_drops_a_match_once_asked_about(self, db):
        player = await make_user(discord_id=9501, username='matrix2')
        ok, _ = await self._matrix(player)
        service = MatchRescheduleService()
        assert ok.id in await service.list_requestable_match_ids(player)

        await service.submit(ok.id, player, reason='clash', proposed_at=_soon())

        assert ok.id not in await service.list_requestable_match_ids(player)

    async def test_signed_out_gets_nothing(self, db):
        assert await MatchRescheduleService().list_requestable_match_ids(None) == set()


class TestReadAuthorization:
    """A request carries the player's own words about why they need the change.
    That is not schedule data, and it is nobody else's to read."""

    async def test_the_queue_refuses_a_plain_member(self, db):
        m, p1, p2 = await _match()
        await _submit(m, p1, proposed_at=_soon())

        with pytest.raises(PermissionError, match='Only staff'):
            await MatchRescheduleService().list_pending(p2)

    async def test_the_queue_admits_staff(self, db):
        m, p1, _ = await _match()
        boss = await _staff()
        await _submit(m, p1, proposed_at=_soon())

        assert len(await MatchRescheduleService().list_pending(boss)) == 1

    async def test_the_queue_narrows_to_the_named_tournaments(self, db):
        """A tournament admin shown someone else's request gets a dialog whose
        Approve is refused."""
        mine, p1, _ = await _match()
        boss = await _staff()
        await _submit(mine, p1, proposed_at=_soon())

        service = MatchRescheduleService()
        assert await service.list_pending(boss, tournament_ids=[mine.tournament_id]) != []
        assert await service.list_pending(boss, tournament_ids=[]) == []

    async def test_a_match_listing_admits_its_own_players(self, db):
        m, p1, p2 = await _match()
        await _submit(m, p1, proposed_at=_soon())

        rows = await MatchRescheduleService().list_for_match(m.id, p2)

        assert len(rows) == 1

    async def test_a_match_listing_refuses_an_outsider(self, db):
        m, p1, _ = await _match()
        await _submit(m, p1, proposed_at=_soon())
        outsider = await make_user(discord_id=9600, username='nosy')

        with pytest.raises(PermissionError, match="can't read"):
            await MatchRescheduleService().list_for_match(m.id, outsider)


class TestSupersessionIsRecorded:
    async def test_the_approval_audit_names_what_it_closed(self, db):
        """Otherwise the trail shows one approval and silently-terminated
        siblings, with no way to tell who closed the second player's request."""
        from models import AuditLog

        m, p1, p2 = await _match()
        boss = await _staff()
        mine = await _submit(m, p1, proposed_at=_soon())
        theirs = await _submit(m, p2, proposed_at=_soon(9))

        with patch(
            'application.services.match.match_service.MatchService.update_match',
            new_callable=AsyncMock,
        ):
            await MatchRescheduleService().approve(mine.id, boss)

        row = await AuditLog.filter(action='match.reschedule_approved').first()
        assert json.loads(row.details)['superseded_request_ids'] == [theirs.id]

    async def test_the_approval_audit_keeps_the_players_words(self, db):
        """An approved cancellation deletes the match and the request cascades
        with it, so the audit row is the only surviving record."""
        from models import AuditLog

        m, p1, _ = await _match()
        boss = await _staff()
        request = await _submit(
            m, p1, kind=RescheduleRequestKind.CANCEL, reason='both forfeited',
        )

        with patch(
            'application.services.match.match_service.MatchService.cancel_match',
            new_callable=AsyncMock,
        ):
            await MatchRescheduleService().approve(request.id, boss, note='agreed')

        details = json.loads(
            (await AuditLog.filter(action='match.reschedule_approved').first()).details
        )
        assert details['reason'] == 'both forfeited'
        assert details['note'] == 'agreed'


class TestLiveViewNudges:
    """Staff already sitting on the schedule board should watch the queue strip
    grow, not discover the request on their next page load."""

    @staticmethod
    def _capture(monkeypatch):
        seen = []
        from application.events import match_live
        monkeypatch.setattr(
            'application.services.match_reschedule_service.match_live.publish',
            lambda match_id, change_type=match_live.CHANGED: seen.append(
                (match_id, change_type)
            ),
        )
        return seen

    async def test_submitting_nudges_open_views(self, db, monkeypatch):
        m, p1, _ = await _match()
        seen = self._capture(monkeypatch)

        await _submit(m, p1, proposed_at=_soon())

        assert (m.id, 'changed') in seen

    async def test_withdrawing_nudges_open_views(self, db, monkeypatch):
        m, p1, _ = await _match()
        request = await _submit(m, p1, proposed_at=_soon())
        seen = self._capture(monkeypatch)

        await MatchRescheduleService().withdraw(request.id, p1)

        assert (m.id, 'changed') in seen

    async def test_declining_nudges_open_views(self, db, monkeypatch):
        """Approving reaches match_live through update_match / cancel_match; a
        decline touches no match, so it has to publish its own."""
        m, p1, _ = await _match()
        boss = await _staff()
        request = await _submit(m, p1, proposed_at=_soon())
        seen = self._capture(monkeypatch)

        await MatchRescheduleService().decline(request.id, boss, 'no room')

        assert (m.id, 'changed') in seen

    async def test_opponent_agreement_nudges_open_views(self, db, monkeypatch):
        m, p1, p2 = await _match()
        request = await _submit(m, p1, proposed_at=_soon())
        seen = self._capture(monkeypatch)

        await MatchRescheduleService().record_opponent_agreement(request.id, p2)

        assert (m.id, 'changed') in seen

    async def test_a_repeat_agreement_does_not_re_nudge(self, db, monkeypatch):
        """The second press is a no-op on the row, so it is a no-op on screen."""
        m, p1, p2 = await _match()
        request = await _submit(m, p1, proposed_at=_soon())
        await MatchRescheduleService().record_opponent_agreement(request.id, p2)
        seen = self._capture(monkeypatch)

        await MatchRescheduleService().record_opponent_agreement(request.id, p2)

        assert seen == []


class TestAgreeingOnTheWeb:
    """The opponent DM asks for Agree; My Schedule is where the web does it."""

    async def test_the_opponent_sees_the_request(self, db):
        m, p1, p2 = await _match()
        request = await _submit(m, p1, proposed_at=_soon())

        rows = await MatchRescheduleService().list_awaiting_agreement(p2)

        assert [r.id for r in rows] == [request.id]

    async def test_the_requester_does_not(self, db):
        m, p1, _ = await _match()
        await _submit(m, p1, proposed_at=_soon())

        assert await MatchRescheduleService().list_awaiting_agreement(p1) == []

    async def test_an_agreed_request_stays_listed_until_decided(self, db):
        """So the player can see their answer landed while staff decide."""
        m, p1, p2 = await _match()
        boss = await _staff()
        request = await _submit(m, p1, proposed_at=_soon())
        service = MatchRescheduleService()
        await service.record_opponent_agreement(request.id, p2)

        rows = await service.list_awaiting_agreement(p2)
        assert rows and rows[0].opponent_agreed_at is not None

        await service.decline(request.id, boss, 'no room')
        assert await service.list_awaiting_agreement(p2) == []

    async def test_a_three_player_match_offers_no_agreement(self, db):
        """One column records agreement; in a bigger match it would read as everyone's."""
        m, p1, p2 = await _match()
        await MatchPlayers.create(match=m, user=await make_user(discord_id=9003, username='p3'))
        await _submit(m, p1, proposed_at=_soon())

        assert await MatchRescheduleService().list_awaiting_agreement(p2) == []

    async def test_the_link_state_follows_the_request(self, db):
        from application.services.match_reschedule_service import (
            AGREE_DECIDED,
            AGREE_DONE,
            AGREE_NOT_YOURS,
            AGREE_OPEN,
        )

        m, p1, p2 = await _match()
        outsider = await make_user(discord_id=9005, username='nosy')
        request = await _submit(m, p1, proposed_at=_soon())
        service = MatchRescheduleService()

        assert await service.agreement_link_state(request.id, p2) == AGREE_OPEN
        assert await service.agreement_link_state(request.id, p1) == AGREE_NOT_YOURS
        assert await service.agreement_link_state(request.id, outsider) == AGREE_NOT_YOURS
        assert await service.agreement_link_state(request.id, None) == AGREE_NOT_YOURS
        assert await service.agreement_link_state(999999, p2) == AGREE_NOT_YOURS

        await service.record_opponent_agreement(request.id, p2)
        assert await service.agreement_link_state(request.id, p2) == AGREE_DONE

        await service.withdraw(request.id, p1)
        assert await service.agreement_link_state(request.id, p2) == AGREE_DECIDED

    async def test_the_opponent_dm_opens_the_request_not_the_board(
        self, db, stub_discord_queue, monkeypatch,
    ):
        sent = []

        async def _send(user_id, message, request_id, **kwargs):
            sent.append((user_id, request_id, kwargs))
            return True, 'ok'

        monkeypatch.setattr(
            'application.services.discord.discord_service.DiscordService'
            '.send_dm_with_reschedule_agree_button',
            AsyncMock(side_effect=_send),
        )
        monkeypatch.setattr(
            'application.services.discord.discord_service.DiscordService.send_dm',
            AsyncMock(return_value=(True, 'ok')),
        )
        m, p1, _ = await _match()
        request = await _submit(m, p1, proposed_at=_soon())
        for coro in list(stub_discord_queue):
            await coro
        stub_discord_queue.clear()

        assert len(sent) == 1
        user_id, request_id, kwargs = sent[0]
        assert (user_id, request_id) == (9002, request.id)
        assert kwargs['link'].url.endswith(f'/home/player?agree={request.id}')


class TestAgreementNeedsTwoPlayers:
    """One column records agreement; in a bigger match it would read as everyone's."""

    async def test_a_third_player_cannot_stamp_agreement(self, db):
        m, p1, p2 = await _match()
        await MatchPlayers.create(match=m, user=await make_user(discord_id=9006, username='p3'))
        request = await _submit(m, p1, proposed_at=_soon())

        with pytest.raises(ValueError, match='two-player'):
            await MatchRescheduleService().record_opponent_agreement(request.id, p2)

        await request.refresh_from_db()
        assert request.opponent_agreed_at is None

    async def test_the_link_says_it_is_not_theirs(self, db):
        from application.services.match_reschedule_service import AGREE_NOT_YOURS

        m, p1, p2 = await _match()
        await MatchPlayers.create(match=m, user=await make_user(discord_id=9007, username='p3'))
        request = await _submit(m, p1, proposed_at=_soon())

        assert await MatchRescheduleService().agreement_link_state(request.id, p2) == AGREE_NOT_YOURS
