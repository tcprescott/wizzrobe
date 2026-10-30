"""Tests for the newly exposed REST endpoints:

- Match watchers (watch / unwatch / list watched)
- Player self-service availability
- Match-time suggestion
- Discord role mappings (Staff)
"""

from datetime import timedelta

from application.utils.timezone import now_local
from models import Match, Role, SystemConfiguration, TenantMembership, Tournament, User
from tests.api_helpers import client_for, create_community_member, create_user_token


class TestMatchWatchers:
    async def test_watch_list_and_unwatch(self, db, app):
        _, raw = await create_user_token(username='fan')
        t = await Tournament.create(name='Cup', is_active=True)
        match = await Match.create(tournament=t)
        async with client_for(app, raw) as c:
            assert (await c.get('/api/matches/watching')).json() == []

            watched = await c.post(f'/api/matches/{match.id}/watch')
            assert watched.status_code == 200

            listed = await c.get('/api/matches/watching')
            assert [m['id'] for m in listed.json()] == [match.id]

            removed = await c.delete(f'/api/matches/{match.id}/watch')
            assert removed.status_code == 204
            assert (await c.get('/api/matches/watching')).json() == []

    async def test_watch_missing_match_is_404(self, db, app):
        _, raw = await create_user_token(username='fan')
        async with client_for(app, raw) as c:
            assert (await c.post('/api/matches/999/watch')).status_code == 404

    async def test_read_only_token_cannot_watch(self, db, app):
        _, raw = await create_user_token(username='fan', read_only=True)
        t = await Tournament.create(name='Cup', is_active=True)
        match = await Match.create(tournament=t)
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/matches/{match.id}/watch')).status_code == 403


class TestPlayerAvailability:
    async def test_set_list_and_clear(self, db, app):
        _, raw = await create_user_token(username='player')
        windows = {
            'windows': [
                {
                    'starts_at': '2026-06-10T18:00:00+00:00',
                    'ends_at': '2026-06-10T20:00:00+00:00',
                    'status': 'preferred',
                    'note': 'evenings',
                }
            ]
        }
        async with client_for(app, raw) as c:
            put = await c.put('/api/users/me/availability', json=windows)
            assert put.status_code == 200
            assert len(put.json()) == 1
            assert put.json()[0]['status'] == 'preferred'

            listed = await c.get('/api/users/me/availability')
            assert len(listed.json()) == 1

            assert (await c.delete('/api/users/me/availability')).status_code == 204
            assert (await c.get('/api/users/me/availability')).json() == []

    async def test_available_window_is_dropped(self, db, app):
        """Opt-out: an 'available' window states the default, so it is not kept."""
        _, raw = await create_user_token(username='player')
        payload = {
            'windows': [
                {
                    'starts_at': '2026-06-10T18:00:00+00:00',
                    'ends_at': '2026-06-10T20:00:00+00:00',
                    'status': 'available',
                },
                {
                    'starts_at': '2026-06-11T18:00:00+00:00',
                    'ends_at': '2026-06-11T20:00:00+00:00',
                    'status': 'unavailable',
                },
            ]
        }
        async with client_for(app, raw) as c:
            put = await c.put('/api/users/me/availability', json=payload)
            assert put.status_code == 200
            assert [w['status'] for w in put.json()] == ['unavailable']
            assert len((await c.get('/api/users/me/availability')).json()) == 1

    async def test_inverted_window_is_400(self, db, app):
        _, raw = await create_user_token(username='player')
        bad = {
            'windows': [
                {
                    'starts_at': '2026-06-10T20:00:00+00:00',
                    'ends_at': '2026-06-10T18:00:00+00:00',
                }
            ]
        }
        async with client_for(app, raw) as c:
            assert (await c.put('/api/users/me/availability', json=bad)).status_code == 400

    async def test_read_only_token_cannot_set(self, db, app):
        _, raw = await create_user_token(username='player', read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.put('/api/users/me/availability', json={'windows': []})
            assert resp.status_code == 403


ONE_WINDOW = {
    'windows': [
        {
            'starts_at': '2026-06-10T18:00:00+00:00',
            'ends_at': '2026-06-10T20:00:00+00:00',
            'status': 'preferred',
        }
    ]
}


class TestReadingAnotherPlayersAvailability:
    """``GET /users/{id}/availability`` — the scheduler's read, staff-gated."""

    async def _player_with_a_window(self, app):
        player, raw = await create_user_token(username='player')
        # A member, because the route resolves the target within the token's
        # community — a global User with no membership is not someone this
        # community can look up by id.
        await TenantMembership.get_or_create(user=player, tenant_id=1)
        async with client_for(app, raw) as c:
            await c.put('/api/users/me/availability', json=ONE_WINDOW)
        return player

    async def test_staff_reads_another_players_windows(self, db, app):
        player = await self._player_with_a_window(app)
        _, staff = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, staff) as c:
            resp = await c.get(f'/api/users/{player.id}/availability')
            assert resp.status_code == 200
            assert [w['status'] for w in resp.json()] == ['preferred']

    async def test_your_own_windows_need_no_role(self, db, app):
        player, raw = await create_user_token(username='player')
        async with client_for(app, raw) as c:
            await c.put('/api/users/me/availability', json=ONE_WINDOW)
            resp = await c.get(f'/api/users/{player.id}/availability')
            assert resp.status_code == 200
            assert len(resp.json()) == 1

    async def test_reading_someone_else_without_staff_is_403(self, db, app):
        player = await self._player_with_a_window(app)
        _, nosy = await create_user_token(username='nosy')
        async with client_for(app, nosy) as c:
            assert (await c.get(f'/api/users/{player.id}/availability')).status_code == 403

    async def test_the_window_filters_bound_the_answer(self, db, app):
        player = await self._player_with_a_window(app)
        _, staff = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, staff) as c:
            after = await c.get(
                f'/api/users/{player.id}/availability',
                params={'start': '2026-06-11T00:00:00+00:00'},
            )
            assert after.json() == []
            overlapping = await c.get(
                f'/api/users/{player.id}/availability',
                params={'start': '2026-06-10T19:00:00+00:00',
                        'end': '2026-06-10T19:30:00+00:00'},
            )
            assert len(overlapping.json()) == 1

    async def test_unknown_user_is_404(self, db, app):
        _, staff = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, staff) as c:
            assert (await c.get('/api/users/9999/availability')).status_code == 404

    async def test_me_still_routes_to_the_self_service_endpoint(self, db, app):
        """The literal ``/users/me/…`` router is included first, so ``me`` never
        falls through to the int path param."""
        _, raw = await create_user_token(username='player')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/users/me/availability')).status_code == 200


class TestMatchSuggestion:
    async def test_suggests_a_time(self, db, app):
        _, raw = await create_user_token(username='ta', roles=[Role.STAFF])
        today = now_local().date()
        await SystemConfiguration.create(name='event_start_date', value=today.isoformat())
        await SystemConfiguration.create(
            name='event_end_date', value=(today + timedelta(days=2)).isoformat(),
        )
        t = await Tournament.create(name='Cup', is_active=True, average_match_duration=60)
        p1 = await create_community_member(discord_id=201, username='p1')
        p2 = await create_community_member(discord_id=202, username='p2')
        async with client_for(app, raw) as c:
            resp = await c.get(
                f'/api/tournaments/{t.id}/match-suggestion',
                params=[('player_ids', p1.id), ('player_ids', p2.id)],
            )
            assert resp.status_code == 200
            assert 'suggested_at' in resp.json()

    async def test_a_member_can_only_ask_about_their_own_match(self, db, app):
        # The answer, or its "no slot" refusal, read out other players' private
        # availability one probe at a time.
        me, raw = await create_user_token(username='me', member=True)
        t = await Tournament.create(name='Cup', is_active=True)
        other = await create_community_member(discord_id=203, username='other')
        third = await create_community_member(discord_id=204, username='third')
        async with client_for(app, raw) as c:
            resp = await c.get(
                f'/api/tournaments/{t.id}/match-suggestion',
                params=[('player_ids', other.id), ('player_ids', third.id)],
            )
            assert resp.status_code == 403
            outsider = await User.create(discord_id=205, username='outsider')
            resp = await c.get(
                f'/api/tournaments/{t.id}/match-suggestion',
                params=[('player_ids', me.id), ('player_ids', outsider.id)],
            )
            assert resp.status_code == 404


class TestDiscordRoleMappings:
    async def test_staff_crud(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            created = await c.post(
                '/api/discord-role-mappings',
                json={
                    'guild_id': 1,
                    'discord_role_id': 2,
                    'discord_role_name': 'Staff',
                    'app_role': Role.STAFF.value,
                },
            )
            assert created.status_code == 201
            mapping_id = created.json()['id']

            listed = await c.get('/api/discord-role-mappings')
            assert any(m['id'] == mapping_id for m in listed.json())

            assert (await c.delete(f'/api/discord-role-mappings/{mapping_id}')).status_code == 204

    async def test_non_staff_forbidden(self, db, app):
        _, raw = await create_user_token(username='nobody')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/discord-role-mappings')).status_code == 403
