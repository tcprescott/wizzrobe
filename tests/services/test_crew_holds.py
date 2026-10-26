"""Crew sign up for bracket matchups nobody has booked; booking hands them over."""

from unittest.mock import AsyncMock

import pytest

from application.services import CrewHoldService, bracket_sources
from models import ChallongeMatch, Commentator, Match, Tournament, Tracker
from tests.services.test_challonge_service import make_user
from tests.services.test_placeholder_bookings import _setup

pytestmark = pytest.mark.usefixtures("bypass_auth")


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(
        'application.services.tenant_membership_service.TenantMembershipService.admit',
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr('application.services.crew_service.CrewService._notify_crew_owners_of_signup', AsyncMock())


async def _final(monkeypatch):
    actor, players, tournament, full, service = await _setup(monkeypatch)
    final = await ChallongeMatch.get(challonge_match_id='8003')
    return actor, players, tournament, full, service, final


async def test_every_open_matchup_is_listed_players_known_or_not(db, monkeypatch):
    *_, final = await _final(monkeypatch)
    futures = await CrewHoldService().list_future()
    refs = {f.ref for f in futures}
    semis = await ChallongeMatch.filter(challonge_match_id__in=['8001', '8002'])
    assert refs == {('challonge', final.id), *(('challonge', s.id) for s in semis)}
    assert all(f.hold is None for f in futures)


async def test_a_tournament_without_crew_roles_is_left_out(db, monkeypatch):
    _, _, tournament, *_ = await _final(monkeypatch)
    await Tournament.filter(id=tournament.id).update(required_commentators=0, required_trackers=0)
    assert await CrewHoldService().list_future() == []


async def test_first_signup_makes_a_hold_and_the_next_reuses_it(db, monkeypatch):
    *_, final = await _final(monkeypatch)
    ana = await make_user(50, 'ana')
    bo = await make_user(51, 'bo')
    service = CrewHoldService()

    hold = await service.signup('challonge', final.id, ana, 'commentator')
    assert hold.scheduled_at is None
    assert hold.crew_hold_source == 'challonge' and hold.crew_hold_key == final.id
    assert hold.bracket_placeholders == ['andy or benny', 'cleo or dash']
    again = await service.signup('challonge', final.id, bo, 'tracker')
    assert again.id == hold.id
    assert await Commentator.filter(match_id=hold.id, user=ana).exists()
    assert await Tracker.filter(match_id=hold.id, user=bo).exists()

    # The matchup stays unbooked for players and staff.
    await final.refresh_from_db()
    assert final.match_id is None
    actor = await make_user(99, 'staff2')
    items = await bracket_sources.list_unbooked(actor, include_players_tbd=True)
    assert final.id in {i.key for i in items}


async def test_a_player_through_to_the_matchup_cannot_crew_it(db, monkeypatch):
    _, players, *_ = await _final(monkeypatch)
    semi = await ChallongeMatch.get(challonge_match_id='8001')
    with pytest.raises(ValueError, match="playing in"):
        await CrewHoldService().signup('challonge', semi.id, players['Andy'], 'commentator')
    assert not await Match.filter(crew_hold_source='challonge').exists()


async def test_booking_moves_the_crew_over_and_keeps_approval(db, monkeypatch):
    actor, *_, service, final = await _final(monkeypatch)
    ana = await make_user(50, 'ana')
    bo = await make_user(51, 'bo')
    holds = CrewHoldService()
    hold = await holds.signup('challonge', final.id, ana, 'commentator')
    await holds.signup('challonge', final.id, bo, 'tracker')
    await Commentator.filter(match_id=hold.id).update(approved=True, approved_by_id=actor.id)
    busy = AsyncMock()
    monkeypatch.setattr(CrewHoldService, '_notify_busy', busy)

    match = await bracket_sources.book_ahead('challonge', final.id, '2026-10-24', '18:00', actor)

    assert not await Match.filter(id=hold.id).exists()
    ana_row = await Commentator.get(user=ana)
    assert ana_row.match_id == match.id and ana_row.approved
    assert (await Tracker.get(user=bo)).match_id == match.id
    busy.assert_not_awaited()


async def test_an_approved_member_busy_at_the_booked_time_is_flagged(db, monkeypatch):
    actor, players, tournament, *_, final = await _final(monkeypatch)
    ana = await make_user(50, 'ana')
    holds = CrewHoldService()
    hold = await holds.signup('challonge', final.id, ana, 'commentator')
    await Commentator.filter(match_id=hold.id).update(approved=True)
    monkeypatch.setattr(
        'application.services.crew_service.CrewService.find_scheduling_conflicts',
        AsyncMock(return_value=['Cleo vs Dash at Sat 18:00']),
    )
    busy = AsyncMock()
    monkeypatch.setattr(CrewHoldService, '_notify_busy', busy)

    await bracket_sources.book_ahead('challonge', final.id, '2026-10-24', '18:00', actor)

    busy.assert_awaited_once()
    assert busy.await_args.args[2:] == ('commentator', ['Cleo vs Dash at Sat 18:00'])


async def test_a_signup_from_someone_now_playing_is_dropped(db, monkeypatch):
    from application.repositories import MatchRepository

    actor, players, tournament, *_, final = await _final(monkeypatch)
    # Andy signed up while both sides of the final were still TBD.
    hold = await CrewHoldService().signup('challonge', final.id, players['Andy'], 'commentator')
    match = await Match.create(tenant_id=hold.tenant_id, tournament=tournament)
    await MatchRepository.add_player(match, players['Andy'])

    assert await CrewHoldService()._move(hold, match, actor) == 0
    assert not await Commentator.filter(user=players['Andy']).exists()
    assert not await Match.filter(id=hold.id).exists()


async def test_everyone_withdrawing_turns_the_hold_back_into_a_line(db, monkeypatch):
    *_, final = await _final(monkeypatch)
    ana = await make_user(50, 'ana')
    holds = CrewHoldService()
    hold = await holds.signup('challonge', final.id, ana, 'commentator')
    await Commentator.filter(match_id=hold.id).delete()
    futures = await holds.list_future()
    assert all(f.hold is None for f in futures)
    assert not await Match.filter(id=hold.id).exists()


async def test_a_hold_booked_some_other_way_is_handed_over_by_the_list(db, monkeypatch):
    actor, *_, service, final = await _final(monkeypatch)
    ana = await make_user(50, 'ana')
    holds = CrewHoldService()
    hold = await holds.signup('challonge', final.id, ana, 'commentator')
    # Straight to the source, skipping bracket_sources' hand-over.
    match = await service.book_ahead(final.id, '2026-10-24', '18:00', actor)
    assert await Match.filter(id=hold.id).exists()

    await holds.list_future()
    assert (await Commentator.get(user=ana)).match_id == match.id
    assert not await Match.filter(id=hold.id).exists()


async def test_schedule_rows_get_a_line_per_matchup_and_tag_holds(db, monkeypatch):
    *_, final = await _final(monkeypatch)
    ana = await make_user(50, 'ana')
    holds = CrewHoldService()
    hold = await holds.signup('challonge', final.id, ana, 'commentator')
    rows = [{'id': hold.id, 'crew_hold': True}, {'id': 999, 'crew_hold': False}]
    out = await holds.dress_schedule_rows(rows)

    by_id = {r['id']: r for r in out}
    assert by_id[hold.id]['_future_ref'] == f'challonge:{final.id}'
    assert by_id[hold.id]['_players_tbd'] is True
    assert 999 in by_id
    lines = [r for r in out if str(r['id']).startswith('future:')]
    assert len(lines) == 2  # the two semifinals
    semi = lines[0]
    assert semi['crew_hold'] and semi['state'] == 'Scheduled' and semi['players'] == []
    assert set(semi['_crew_blocked']) <= {'11', '12', '13', '14'} and semi['_crew_blocked']


async def test_a_stale_hold_row_is_dropped_from_the_schedule(db, monkeypatch):
    *_, final = await _final(monkeypatch)
    rows = [{'id': 12345, 'crew_hold': True}]
    out = await CrewHoldService().dress_schedule_rows(rows, tournament_ids=[0])
    assert out == []


async def test_signing_up_on_a_matchup_that_is_gone_says_so(db, monkeypatch):
    await _final(monkeypatch)
    ana = await make_user(50, 'ana')
    with pytest.raises(ValueError, match="isn't open"):
        await CrewHoldService().signup('challonge', 424242, ana, 'commentator')


async def test_crew_dm_parts_name_a_hold_by_its_sides(db, monkeypatch):
    from application.services._crew_signup_dms import crew_dm_parts

    *_, final = await _final(monkeypatch)
    ana = await make_user(50, 'ana')
    hold = await CrewHoldService().signup('challonge', final.id, ana, 'commentator')
    hold = await Match.get(id=hold.id).prefetch_related('stage')
    kwargs, fields = await crew_dm_parts(hold)
    assert kwargs['player_names'] == ['andy or benny', 'cleo or dash']
    assert ('Time', 'Not booked yet', False) in fields
