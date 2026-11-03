"""Moving a bracket entrant to another member moves their booked, unstarted matches.

SGL26: staff moved alsoa's start.gg and Challonge entrants from one account to
another, and the booked matches kept the old account, so results could no
longer reach the bracket site.
"""

from datetime import datetime, timezone

import pytest

from application.repositories.user_role_repository import UserRoleRepository
from application.services import ChallongeService, StartggService
from models import (
    AuditLog,
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    Match,
    MatchPlayers,
    Role,
    StartggEntrant,
    StartggPhase,
    StartggPool,
    StartggSet,
    StartggSetState,
    TenantMembership,
    Tournament,
    User,
)


async def _member(discord_id: int, name: str) -> User:
    user = await User.create(discord_id=discord_id, username=name)
    await TenantMembership.create(user=user, tenant_id=1)
    return user


@pytest.fixture
async def people(db):
    staff = await _member(7800, 'boss')
    await UserRoleRepository.add(staff, Role.STAFF)
    return staff, await _member(7801, 'alsoa6800'), await _member(7802, 'bird_man999'), await _member(7803, 'jax')


async def _match(tournament: Tournament, *users: User, started: bool = False) -> Match:
    match = await Match.create(
        tournament=tournament, started_at=datetime.now(timezone.utc) if started else None,
    )
    for user in users:
        await MatchPlayers.create(match=match, user=user)
    return match


async def _player_ids(match: Match) -> set:
    return {p.user_id for p in await MatchPlayers.filter(match=match)}


async def test_startgg_reassign_moves_the_unstarted_booking(people):
    staff, old, new, jax = people
    tournament = await Tournament.create(name='OoTR', startgg_event_slug='tournament/x/event/y')
    phase = await StartggPhase.create(tournament=tournament, phase_id='1', name='Pools')
    pool = await StartggPool.create(tournament=tournament, phase=phase, phase_group_id='2', identifier='6')
    alsoa = await StartggEntrant.create(
        tournament=tournament, entrant_id='3', name='Alsoa', user=old, user_assigned_manually=True,
    )
    other = await StartggEntrant.create(tournament=tournament, entrant_id='4', name='jaxstronomy', user=jax)
    upcoming = await _match(tournament, old, jax)
    live = await _match(tournament, old, jax, started=True)
    await StartggSet.create(tournament=tournament, pool=pool, set_id='5', state=StartggSetState.OPEN,
                            entrant1=alsoa, entrant2=other, match=upcoming)
    await StartggSet.create(tournament=tournament, pool=pool, set_id='6', state=StartggSetState.OPEN,
                            entrant1=other, entrant2=alsoa, match=live)

    await StartggService().clear_entrant(alsoa.id, staff)
    await StartggService().assign_entrant(alsoa.id, new.id, staff)

    assert await _player_ids(upcoming) == {new.id, jax.id}
    assert await _player_ids(live) == {old.id, jax.id}
    audit = await AuditLog.filter(action='match.updated').get()
    assert f'"from": {old.id}' in audit.details and f'"to": {new.id}' in audit.details


async def test_challonge_reassign_moves_the_unstarted_booking(people):
    staff, old, new, rival = people
    tournament = await Tournament.create(name='Z1R', challonge_tournament_id='T1')
    alsoa = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id='100', name='alsoa', user=old,
        user_assigned_manually=True,
    )
    other = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id='200', name='lordfizzlebeef', user=rival,
    )
    upcoming = await _match(tournament, rival, old)
    await ChallongeMatch.create(tournament=tournament, challonge_match_id='8001', state=ChallongeMatchState.OPEN,
                                participant1=other, participant2=alsoa, match=upcoming)

    await ChallongeService().clear_participant_user(alsoa.id, staff)
    await ChallongeService().assign_participant_user(alsoa.id, new.id, staff)

    assert await _player_ids(upcoming) == {new.id, rival.id}


async def test_a_match_with_someone_unrelated_is_left_alone(people):
    staff, old, new, jax = people
    stranger = await _member(7804, 'stranger')
    tournament = await Tournament.create(name='Z1R', challonge_tournament_id='T1')
    alsoa = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id='100', name='alsoa', user=old,
    )
    other = await ChallongeParticipant.create(
        tournament=tournament, challonge_participant_id='200', name='jax', user=jax,
    )
    odd = await _match(tournament, stranger, old)
    await ChallongeMatch.create(tournament=tournament, challonge_match_id='8001', state=ChallongeMatchState.OPEN,
                                participant1=alsoa, participant2=other, match=odd)

    await ChallongeService().assign_participant_user(alsoa.id, new.id, staff)

    assert await _player_ids(odd) == {stranger.id, old.id}
