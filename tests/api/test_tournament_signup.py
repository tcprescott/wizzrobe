"""Player self-service tournament signup over REST.

The API counterpart of the Tournaments tab: the same service, so the same
refusals. Worth its own coverage because the route reaches past whatever the tab
chose to draw — hiding a button is not enforcing a rule.
"""

from datetime import timedelta

from application.services.tenant_membership_service import TenantMembershipService
from application.utils.timezone import now_local
from models import Role, TenantMembership, Tournament, TournamentPlayers, User
from tests.api_helpers import client_for, create_user_token


async def _admit(person: User) -> None:
    """Admit ``person`` to the default community, the way staff would."""
    boss, _ = await create_user_token(
        username=f'boss-{person.discord_id}',
        discord_id=int(person.discord_id) + 900,
        roles=[Role.STAFF],
    )
    await TenantMembership.get_or_create(user=boss, tenant_id=1)
    await TenantMembershipService().add_member(boss, person)


async def _open_tournament(name: str = 'Spring Open') -> Tournament:
    return await Tournament.create(
        name=name,
        signups_open_at=now_local() - timedelta(days=1),
        signups_close_at=now_local() + timedelta(days=7),
    )


class TestSignup:
    async def test_sign_up_then_withdraw(self, db, app):
        user, raw = await create_user_token(username='entrant', discord_id=9500)
        await _admit(user)
        tournament = await _open_tournament()

        async with client_for(app, raw) as c:
            signed = await c.post(f'/api/tournaments/{tournament.id}/signup')
            assert signed.status_code == 200
            assert await TournamentPlayers.filter(
                tournament=tournament, user=user,
            ).count() == 1

            withdrawn = await c.delete(f'/api/tournaments/{tournament.id}/signup')
            assert withdrawn.status_code == 200
            assert await TournamentPlayers.filter(
                tournament=tournament, user=user,
            ).count() == 0

    async def test_signup_outside_the_window_is_refused(self, db, app):
        user, raw = await create_user_token(username='latecomer', discord_id=9501)
        await _admit(user)
        tournament = await Tournament.create(
            name='Last Season', signups_close_at=now_local() - timedelta(days=1),
        )
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/tournaments/{tournament.id}/signup')).status_code == 400

    async def test_a_non_member_is_refused(self, db, app):
        """A non-member's token is refused before the route runs; the service
        refuses them too, for the MCP and UI paths."""
        _, raw = await create_user_token(username='stranger', discord_id=9502, member=False)
        tournament = await _open_tournament()
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/tournaments/{tournament.id}/signup')).status_code == 403

    async def test_missing_tournament_is_404_not_a_refusal(self, db, app):
        """A tournament that does not exist — including another community's,
        since the read is tenant-scoped — must not come back as "not accepting
        signups", which would confirm it exists."""
        user, raw = await create_user_token(username='entrant2', discord_id=9503)
        await _admit(user)
        async with client_for(app, raw) as c:
            assert (await c.post('/api/tournaments/999/signup')).status_code == 404
            assert (await c.delete('/api/tournaments/999/signup')).status_code == 404

    async def test_a_read_only_token_cannot_sign_up(self, db, app):
        user, raw = await create_user_token(
            username='observer', discord_id=9504, read_only=True,
        )
        await _admit(user)
        tournament = await _open_tournament()
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/tournaments/{tournament.id}/signup')).status_code == 403


class TestSignupWindowFields:
    async def test_the_window_round_trips_through_the_tournament_endpoints(self, db, app):
        _, raw = await create_user_token(
            username='boss', discord_id=9505, roles=[Role.STAFF],
        )
        opens = (now_local() + timedelta(days=1)).replace(microsecond=0)
        closes = (now_local() + timedelta(days=8)).replace(microsecond=0)

        async with client_for(app, raw) as c:
            created = await c.post('/api/tournaments', json={
                'name': 'Windowed Cup',
                'signups_open_at': opens.isoformat(),
                'signups_close_at': closes.isoformat(),
            })
            assert created.status_code == 201
            tournament_id = created.json()['id']

            got = (await c.get(f'/api/tournaments/{tournament_id}')).json()
            assert got['signups_open_at'] is not None
            assert got['signups_close_at'] is not None

    async def test_a_close_before_the_open_is_refused(self, db, app):
        _, raw = await create_user_token(
            username='boss2', discord_id=9506, roles=[Role.STAFF],
        )
        async with client_for(app, raw) as c:
            created = await c.post('/api/tournaments', json={
                'name': 'Backwards Cup',
                'signups_open_at': (now_local() + timedelta(days=8)).isoformat(),
                'signups_close_at': (now_local() + timedelta(days=1)).isoformat(),
            })
            assert created.status_code == 400
