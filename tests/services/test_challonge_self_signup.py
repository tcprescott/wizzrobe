"""Players signing themselves up for a Challonge-linked tournament.

The Wizzrobe service account adds the entrant on Challonge by the player's linked
username; the tests swap the HTTP client for a fake and run everything else
against the in-memory DB.
"""

from unittest.mock import AsyncMock

import pytest

from application.events import EventType, event_bus
from application.repositories.user_role_repository import UserRoleRepository
from application.services._challonge_signup import (
    MISC_USER_PREFIX,
    ROSTER_CHANGES_PER_HOUR,
    misc_user_id,
    roster_locked,
)
from application.services.challonge_service import ChallongeService
from application.services.tenant_membership_service import TenantMembershipService
from application.services.tournament_service import TournamentService
from application.utils.clients.challonge_client import ChallongeAPIError
from models import (
    ChallongeConnection,
    ChallongeParticipant,
    Role,
    TenantMembership,
    Tournament,
    TournamentPlayers,
    User,
)

FULL_SCOPES = 'me tournaments:read participants:read participants:write'


@pytest.fixture
async def staff(db):
    boss = await User.create(discord_id=7400, username='boss')
    await UserRoleRepository.add(boss, Role.STAFF)
    await TenantMembership.create(user=boss, tenant_id=1)
    return boss


@pytest.fixture
async def linked_player(staff, db):
    person = await User.create(
        discord_id=7401, username='entrant',
        challonge_user_id='555', challonge_username='linkedplayer',
    )
    await TenantMembershipService().add_member(staff, person)
    return person


@pytest.fixture
async def unlinked_player(staff, db):
    person = await User.create(discord_id=7402, username='nolink')
    await TenantMembershipService().add_member(staff, person)
    return person


@pytest.fixture
async def bracket(db):
    return await Tournament.create(
        name='Bracket Cup', challonge_tournament_id='T1', challonge_community='sg',
        challonge_state='pending',
    )


@pytest.fixture
async def connection(db):
    return await ChallongeConnection.create(tenant_id=1, access_token='tok', scopes=FULL_SCOPES)


@pytest.fixture
def api(monkeypatch):
    fake = AsyncMock()
    fake.create_participant.return_value = {
        'participant_id': '9100', 'name': 'linkedplayer',
        'challonge_user_id': None, 'username': 'linkedplayer', 'misc': None,
    }
    monkeypatch.setattr(ChallongeService, '_api_client', lambda self: fake)
    return fake


async def _card(user, tournament):
    return next(
        c for c in await TournamentService().list_signup_cards(user)
        if c.tournament.id == tournament.id
    )


@pytest.mark.parametrize('state, locked', [
    (None, False), ('pending', False), ('checking_in', False), ('checked_in', False),
    ('underway', True), ('group_stages_underway', True), ('complete', True),
])
def test_roster_locks_once_the_bracket_starts(state, locked):
    assert roster_locked(state) is locked


def test_misc_names_only_wizzrobe_entrants():
    assert misc_user_id(f'{MISC_USER_PREFIX}42') == 42
    assert misc_user_id('seed notes') is None
    assert misc_user_id(f'{MISC_USER_PREFIX}abc') is None
    assert misc_user_id(None) is None


async def test_a_linked_player_is_added_to_the_bracket(linked_player, bracket, connection, api):
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    try:
        await TournamentService().self_enroll(bracket.id, linked_player)
    finally:
        event_bus.unsubscribe(token)

    api.create_participant.assert_awaited_once_with(
        'T1', name='linkedplayer', username='linkedplayer',
        misc=f'{MISC_USER_PREFIX}{linked_player.id}', community='sg',
    )
    row = await ChallongeParticipant.get(tournament=bracket, challonge_participant_id='9100')
    assert row.user_id == linked_player.id
    assert row.challonge_user_id == '555'
    assert not row.user_assigned_manually
    # The Wizzrobe player pool is not the roster of a Challonge bracket.
    assert await TournamentPlayers.filter(tournament=bracket).count() == 0
    enrolled = [e for e in seen if e.event_type == EventType.TOURNAMENT_ENROLLED]
    assert [e.payload['tournament_id'] for e in enrolled] == [bracket.id]
    assert enrolled[0].payload['user_id'] == linked_player.id

    card = await _card(linked_player, bracket)
    assert card.enrolled and card.entrant_count == 1
    assert card.can_withdraw and not card.can_sign_up


async def test_the_card_offers_a_linked_player_the_button(linked_player, bracket, connection):
    card = await _card(linked_player, bracket)
    assert card.challonge_managed and card.challonge_name == 'linkedplayer'
    assert card.can_sign_up and not card.needs_challonge_link


async def test_an_unlinked_player_is_asked_to_link_first(unlinked_player, bracket, connection, api):
    card = await _card(unlinked_player, bracket)
    assert card.needs_challonge_link and not card.can_sign_up
    with pytest.raises(ValueError, match='Link your Challonge account'):
        await TournamentService().self_enroll(bracket.id, unlinked_player)
    api.create_participant.assert_not_awaited()


async def test_a_started_bracket_takes_no_one(linked_player, bracket, connection, api):
    bracket.challonge_state = 'underway'
    await bracket.save()
    card = await _card(linked_player, bracket)
    assert not card.can_sign_up and not card.needs_challonge_link
    with pytest.raises(ValueError, match='started'):
        await TournamentService().self_enroll(bracket.id, linked_player)
    api.create_participant.assert_not_awaited()


async def test_an_old_connection_without_the_write_scope_refuses(linked_player, bracket, api, db):
    await ChallongeConnection.create(tenant_id=1, access_token='tok', scopes='me participants:read')
    with pytest.raises(ValueError, match='Ask staff'):
        await TournamentService().self_enroll(bracket.id, linked_player)
    api.create_participant.assert_not_awaited()


async def test_a_non_member_cannot_sign_up(bracket, connection, api, db):
    outsider = await User.create(
        discord_id=7403, username='outsider', challonge_user_id='777', challonge_username='out',
    )
    with pytest.raises(ValueError, match='Join this community'):
        await TournamentService().self_enroll(bracket.id, outsider)
    api.create_participant.assert_not_awaited()


async def test_challonge_refusing_reads_as_a_plain_message(linked_player, bracket, connection, api):
    api.create_participant.side_effect = ChallongeAPIError('nope', status=422)
    with pytest.raises(ValueError, match="Challonge wouldn't add you"):
        await TournamentService().self_enroll(bracket.id, linked_player)
    assert await ChallongeParticipant.filter(tournament=bracket).count() == 0


async def test_signing_up_twice_is_refused(linked_player, bracket, connection, api):
    await TournamentService().self_enroll(bracket.id, linked_player)
    with pytest.raises(ValueError, match='already signed up'):
        await TournamentService().self_enroll(bracket.id, linked_player)
    assert api.create_participant.await_count == 1


async def test_withdrawing_removes_the_entry_on_challonge(linked_player, bracket, connection, api):
    await TournamentService().self_enroll(bracket.id, linked_player)
    await TournamentService().self_withdraw(bracket.id, linked_player)
    api.delete_participant.assert_awaited_once_with('T1', '9100', community='sg')
    assert await ChallongeParticipant.filter(tournament=bracket).count() == 0


async def test_an_entry_already_gone_on_challonge_still_withdraws(
    linked_player, bracket, connection, api,
):
    await TournamentService().self_enroll(bracket.id, linked_player)
    api.delete_participant.side_effect = ChallongeAPIError('gone', status=404)
    await TournamentService().self_withdraw(bracket.id, linked_player)
    assert await ChallongeParticipant.filter(tournament=bracket).count() == 0


async def test_no_withdrawing_once_the_bracket_starts(linked_player, bracket, connection, api):
    await TournamentService().self_enroll(bracket.id, linked_player)
    bracket.challonge_state = 'underway'
    await bracket.save()
    card = await _card(linked_player, bracket)
    assert card.enrolled and not card.can_withdraw
    with pytest.raises(ValueError, match='started'):
        await TournamentService().self_withdraw(bracket.id, linked_player)
    api.delete_participant.assert_not_awaited()


async def test_toggling_is_capped(linked_player, bracket, connection, api):
    service = TournamentService()
    for _ in range(ROSTER_CHANGES_PER_HOUR // 2):
        await service.self_enroll(bracket.id, linked_player)
        await service.self_withdraw(bracket.id, linked_player)
    with pytest.raises(ValueError, match='this hour'):
        await service.self_enroll(bracket.id, linked_player)


async def test_unmapped_entrants_are_listed_by_name(linked_player, bracket, connection):
    await ChallongeParticipant.create(
        tenant_id=1, tournament=bracket, challonge_participant_id='1', name='Walk-in',
    )
    await ChallongeParticipant.create(
        tenant_id=1, tournament=bracket, challonge_participant_id='2', name='x',
        user=linked_player,
    )
    card = await _card(linked_player, bracket)
    assert card.entrant_count == 2
    assert card.unmapped_entrant_names == ['Walk-in']
    assert [u.id for u in card.entrants] == [linked_player.id]
    assert card.enrolled


async def test_client_adds_an_entrant_by_username():
    from application.utils.clients.challonge_client import ChallongeClient

    client = ChallongeClient('id', 'secret', token_provider=AsyncMock(return_value='tok'))
    client._authed_request = AsyncMock(return_value={'data': {
        'id': 77, 'type': 'participant',
        'attributes': {'name': 'linkedplayer', 'username': 'linkedplayer', 'misc': 'wizzrobe-user:5'},
    }})
    result = await client.create_participant(
        'T1', name='linkedplayer', username='linkedplayer', misc='wizzrobe-user:5', community='sg',
    )
    method, path = client._authed_request.call_args.args
    assert (method, path) == ('POST', '/communities/sg/tournaments/T1/participants.json')
    assert client._authed_request.call_args.kwargs['json'] == {'data': {
        'type': 'Participant',
        'attributes': {'name': 'linkedplayer', 'username': 'linkedplayer', 'misc': 'wizzrobe-user:5'},
    }}
    assert result['participant_id'] == '77'
    assert result['misc'] == 'wizzrobe-user:5'


async def test_client_removes_an_entrant():
    from application.utils.clients.challonge_client import ChallongeClient

    client = ChallongeClient('id', 'secret', token_provider=AsyncMock(return_value='tok'))
    client._authed_request = AsyncMock(return_value={})
    await client.delete_participant('T1', '77')
    assert client._authed_request.call_args.args == ('DELETE', '/tournaments/T1/participants/77.json')
