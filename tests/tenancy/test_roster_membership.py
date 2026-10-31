"""Ids in a request body reach only people in the caller's community.

``User`` is global. Before these checks, a tournament admin anywhere could put
any account on a match (enrolling them, naming them in the response and having
the bot DM them), a volunteer coordinator could assign and DM anyone, and staff
could link any account to a bracket entrant.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.errors import NotFoundError
from application.repositories.tenant_membership_repository import TenantMembershipRepository
from application.services import BracketService
from application.services.tenant_membership_service import TenantMembershipService
from models import (
    ApiToken,
    Match,
    Role,
    Tournament,
    TournamentPlayers,
    User,
    UserRole,
    VolunteerPosition,
    VolunteerShift,
)
from tests.api_helpers import client_for, create_community_member, create_user_token
from tests.conftest import DEFAULT_TEST_TENANT_ID


async def _outsider() -> User:
    return await User.create(discord_id=990001, username='elsewhere')


def _match_body(tournament: Tournament, player_ids: list[int], **extra) -> dict:
    return {
        'tournament_id': tournament.id,
        'scheduled_date': '2099-06-10',
        'scheduled_time': '18:00',
        'player_ids': player_ids,
        **extra,
    }


class TestMatchRoster:
    async def test_create_refuses_an_outsider_and_writes_nothing(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF], member=True)
        t = await Tournament.create(name='Cup', is_active=True)
        member = await create_community_member(discord_id=1201, username='member')
        outsider = await _outsider()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/matches', json=_match_body(t, [member.id, outsider.id]))
        assert resp.status_code == 404
        assert resp.json()['detail'] == f'User {outsider.id} not found'
        assert await Match.all().count() == 0
        assert not await TournamentPlayers.exists(user_id=outsider.id)

    @pytest.mark.parametrize('crew_field', ['commentator_ids', 'tracker_ids'])
    async def test_create_refuses_outsider_crew(self, db, app, crew_field):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF], member=True)
        t = await Tournament.create(name='Cup', is_active=True)
        member = await create_community_member(discord_id=1202, username='member')
        outsider = await _outsider()
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/matches', json=_match_body(t, [member.id], **{crew_field: [outsider.id]}),
            )
        assert resp.status_code == 404

    async def test_update_refuses_adding_an_outsider_but_keeps_existing_players(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF], member=True)
        t = await Tournament.create(name='Cup', is_active=True)
        member = await create_community_member(discord_id=1203, username='member')
        async with client_for(app, raw) as c:
            created = await c.post('/api/matches', json=_match_body(t, [member.id]))
            assert created.status_code == 201, created.text
            match_id = created.json()['id']

            outsider = await _outsider()
            added = await c.patch(f'/api/matches/{match_id}', json={'player_ids': [member.id, outsider.id]})
            assert added.status_code == 404

            await TenantMembershipRepository.remove(member.id, DEFAULT_TEST_TENANT_ID)
            edited = await c.patch(f'/api/matches/{match_id}', json={'comment': 'moved to stage 2'})
            assert edited.status_code == 200, edited.text

    async def test_player_request_refuses_an_outsider_opponent(self, db, app):
        actor, raw = await create_user_token(username='requester', member=True)
        t = await Tournament.create(name='Cup', is_active=True)
        outsider = await _outsider()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/matches/request', json=_match_body(t, [actor.id, outsider.id]))
        assert resp.status_code == 404

    async def test_super_admin_may_name_anyone(self, db, app):
        _, raw = await create_user_token(username='root', roles=[Role.SUPER_ADMIN])
        t = await Tournament.create(name='Cup', is_active=True)
        outsider = await _outsider()
        async with client_for(app, raw) as c:
            resp = await c.post('/api/matches', json=_match_body(t, [outsider.id]))
        assert resp.status_code == 201, resp.text


class TestShiftAssignment:
    async def test_assigning_an_outsider_is_404(self, db, app):
        _, raw = await create_user_token(
            username='coord', roles=[Role.VOLUNTEER_COORDINATOR], member=True,
        )
        position = await VolunteerPosition.create(name='Runner')
        start = datetime.now(timezone.utc) + timedelta(days=2)
        shift = await VolunteerShift.create(
            position=position, starts_at=start, ends_at=start + timedelta(hours=2), slots_needed=1,
        )
        outsider = await _outsider()
        async with client_for(app, raw) as c:
            resp = await c.post(
                f'/api/volunteers/shifts/{shift.id}/assignments', json={'user_id': outsider.id},
            )
        assert resp.status_code == 404


class TestBracketEntrantLink:
    async def test_linking_an_outsider_is_not_found(self, db):
        staff = await User.create(discord_id=1300, username='staff')
        await UserRole.create(user=staff, role=Role.STAFF)
        t = await Tournament.create(name='Cup', is_active=True)
        outsider = await _outsider()
        service = BracketService()
        with pytest.raises(NotFoundError):
            await service.add_entrant(staff, t.id, 'Ghost', user_id=outsider.id)
        entrant = await service.add_entrant(staff, t.id, 'Placeholder')
        with pytest.raises(NotFoundError):
            await service.set_entrant_user(staff, entrant.id, outsider.id)


class TestUserCreate:
    async def test_a_discord_id_that_already_has_an_account_is_400(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF], member=True)
        await User.create(discord_id=424242, username='existing')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/users', json={'username': 'squat', 'discord_id': 424242})
        assert resp.status_code == 400

    async def test_rest_cannot_create_a_deactivated_account(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF], member=True)
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/users', json={'username': 'new', 'discord_id': 515151, 'is_active': False},
            )
        assert resp.status_code == 201, resp.text
        assert (await User.get(discord_id=515151)).is_active is True


class TestRemovalRevokesTokens:
    async def test_removed_member_loses_their_api_tokens(self, db, app):
        staff = await create_community_member(discord_id=1400, username='staff')
        await UserRole.create(user=staff, role=Role.STAFF)
        leaver, raw = await create_user_token(username='leaver', member=True)
        async with client_for(app, raw) as c:
            assert (await c.get('/api/tournaments')).status_code == 200

            await TenantMembershipService().remove_member(staff, leaver)

            assert (await c.get('/api/tournaments')).status_code == 401
        assert not await ApiToken.filter(user_id=leaver.id, revoked_at=None).exists()
