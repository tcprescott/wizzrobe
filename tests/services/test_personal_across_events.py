"""Personal pages span every live event: what they read, and how a row names its event."""

from datetime import datetime, timedelta, timezone

import pytest

from application.event_context import event_scope
from application.services import TournamentService
from application.services.event_service import EventService, with_event_name
from application.services.match.match_display_service import MatchDisplayService
from models import Event, EventStatus, Match, MatchPlayers, Tournament, TournamentPlayers, User


@pytest.fixture
async def events(db):
    main = await Event.get(tenant_id=1, is_default=True)
    winter = await Event.create(tenant_id=1, name='Winter', slug='winter', status=EventStatus.LIVE)
    past = await Event.create(tenant_id=1, name='Last Year', slug='past', status=EventStatus.ARCHIVED)
    return main, winter, past


@pytest.fixture
async def player(db) -> User:
    return await User.create(discord_id=8100, username='player')


async def _tournament_with_match(event: Event, name: str, player: User) -> Tournament:
    tournament = await Tournament.create(tenant_id=1, event=event, name=name, is_active=True)
    await TournamentPlayers.create(tenant_id=1, tournament=tournament, user=player)
    match = await Match.create(
        tenant_id=1, event=event, tournament=tournament, title=f'{name} R1',
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    await MatchPlayers.create(tenant_id=1, match=match, user=player)
    return tournament


def test_a_row_is_named_only_when_its_event_is_in_the_map():
    assert with_event_name('ALttPR', 1, {1: 'SGL 2026'}) == 'SGL 2026 · ALttPR'
    assert with_event_name('ALttPR', 2, {1: 'SGL 2026'}) == 'ALttPR'
    assert with_event_name('', 1, {1: 'SGL 2026'}) == 'SGL 2026'


async def test_one_event_needs_no_names(events):
    main, _winter, _past = events
    assert await EventService.names_if_mixed([main.id, main.id, None]) == {}


async def test_two_events_name_both(events):
    main, winter, _past = events
    assert await EventService.names_if_mixed([main.id, winter.id]) == {
        main.id: main.name, winter.id: 'Winter',
    }


async def test_my_schedule_spans_live_events_and_names_them(events, player):
    main, winter, past = events
    await _tournament_with_match(main, 'ALttPR', player)
    await _tournament_with_match(winter, 'Winter Cup', player)
    await _tournament_with_match(past, 'Old Cup', player)
    with event_scope(main.id):
        rows = await MatchDisplayService().get_matches_for_display(user_discord_id=str(player.discord_id))
    assert sorted(r['tournament'] for r in rows) == [f'{main.name} · ALttPR', 'Winter · Winter Cup']


async def test_a_single_event_schedule_reads_as_before(events, player):
    main, _winter, _past = events
    await _tournament_with_match(main, 'ALttPR', player)
    with event_scope(main.id):
        rows = await MatchDisplayService().get_matches_for_display(user_discord_id=str(player.discord_id))
    assert [r['tournament'] for r in rows] == ['ALttPR']


async def test_enrolments_leave_out_archived_events(events, player):
    main, winter, past = events
    for event, name in ((main, 'A'), (winter, 'B'), (past, 'C')):
        await _tournament_with_match(event, name, player)
    with event_scope(main.id):
        names = {e.tournament.name for e in await TournamentService().get_enrolled_players_by_user(player)}
    assert names == {'A', 'B'}


async def test_home_tournaments_lists_every_live_event(events, player):
    main, winter, past = events
    for event, name in ((main, 'A'), (winter, 'B'), (past, 'C')):
        await Tournament.create(tenant_id=1, event=event, name=name, is_active=True)
    with event_scope(main.id):
        spanning = {c.tournament.name for c in await TournamentService().list_signup_cards(player, live_events=True)}
        own = {c.tournament.name for c in await TournamentService().list_signup_cards(player)}
    assert spanning == {'A', 'B'}
    assert own == {'A'}


async def test_bracket_site_rows_leave_out_events_not_live(events, player):
    from application.repositories import ChallongeRepository, StartggRepository
    from application.repositories.bracket_repository import BracketRepository
    from models import (
        Bracket,
        BracketEntrant,
        BracketEntry,
        BracketFormat,
        BracketMatch,
        BracketMatchState,
        BracketState,
        ChallongeMatch,
        ChallongeMatchState,
        ChallongeParticipant,
        StartggEntrant,
        StartggPhase,
        StartggPool,
        StartggSet,
        StartggSetState,
    )
    main, _winter, past = events
    rival = await User.create(discord_id=8101, username='rival')
    for n, event in enumerate((main, past)):
        t = await Tournament.create(tenant_id=1, event=event, name=f'T{n}', is_active=True)
        p1 = await ChallongeParticipant.create(tournament=t, challonge_participant_id=f'{n}1', name='p', user=player)
        p2 = await ChallongeParticipant.create(tournament=t, challonge_participant_id=f'{n}2', name='r', user=rival)
        await ChallongeMatch.create(tournament=t, challonge_match_id=f'90{n}', state=ChallongeMatchState.OPEN,
                                    participant1=p1, participant2=p2)
        phase = await StartggPhase.create(tournament=t, phase_id=f'{n}', name='Pools')
        pool = await StartggPool.create(tournament=t, phase=phase, phase_group_id=f'{n}', identifier='A')
        e1 = await StartggEntrant.create(tournament=t, entrant_id=f'{n}1', name='p', user=player)
        e2 = await StartggEntrant.create(tournament=t, entrant_id=f'{n}2', name='r', user=rival)
        await StartggSet.create(tournament=t, pool=pool, set_id=f'80{n}', state=StartggSetState.OPEN,
                                entrant1=e1, entrant2=e2)
        bracket = await Bracket.create(tournament=t, name='Main', format=BracketFormat.SINGLE_ELIM,
                                       state=BracketState.ACTIVE)
        b1 = await BracketEntrant.create(tournament=t, display_name='p', user=player)
        b2 = await BracketEntrant.create(tournament=t, display_name='r', user=rival)
        await BracketMatch.create(
            bracket=bracket, round=1, position=1, state=BracketMatchState.OPEN,
            entry1=await BracketEntry.create(bracket=bracket, entrant=b1),
            entry2=await BracketEntry.create(bracket=bracket, entrant=b2),
        )
    with event_scope(main.id):
        challonge = await ChallongeRepository.unscheduled_open_matches_for_user(player)
        startgg = await StartggRepository.open_unbooked_sets_for_user(player)
        native = await BracketRepository().open_matches_for_user(player.id)
    assert [m.tournament.name for m in challonge] == ['T0']
    assert [s.tournament.name for s in startgg] == ['T0']
    assert [m.bracket.tournament.name for m in native] == ['T0']
