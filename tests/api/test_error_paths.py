"""Coverage-gap tests for partially-covered REST API routers.

Targets the untested branches of:
- ``api/routers/health.py``            (liveness probe: ok + db-down)
- ``api/routers/audit.py``             (``_decode_details`` None / bad-JSON / good-JSON)
- ``api/routers/users.py``            (profile / admin-field / enrollment writes, 403 & 404)
- ``api/routers/tournament_actions.py`` (admin / crew-coordinator add-remove, 403 & 404)
- ``api/routers/match_actions.py``     (request, update, stage/stations/candidate, seed, crew)

These exercise auth failures (403), read-only-token rejection, not-found (404), and
validation branches without touching existing test cases.
"""

from tortoise import connections

from models import AuditLog, Match, MatchPlayers, Role, Stage, Tournament, TournamentPlayers, User
from tests.api_helpers import client_for, create_community_member, create_user_token


async def _tournament_and_players(**tournament_kwargs):
    kwargs = {'name': 'Cup', 'is_active': True}
    kwargs.update(tournament_kwargs)
    t = await Tournament.create(**kwargs)
    p1 = await create_community_member(discord_id=1101, username='p1')
    p2 = await create_community_member(discord_id=1102, username='p2')
    return t, p1, p2


async def _create_match(client, t, p1, p2, **overrides):
    payload = {
        'tournament_id': t.id,
        'scheduled_date': '2099-06-10',
        'scheduled_time': '18:00',
        'player_ids': [p1.id, p2.id],
    }
    payload.update(overrides)
    return await client.post('/api/matches', json=payload)


class TestHealthProbe:
    async def test_health_ok(self, db, app):
        # Unauthenticated: the probe router carries no auth dependency.
        async with client_for(app) as c:
            resp = await c.get('/api/health')
            assert resp.status_code == 200
            assert resp.json() == {'status': 'ok'}

    async def test_health_db_down_is_503(self, db, app, monkeypatch):
        # Patch execute_query on the real connection (not connections.get) so the
        # connection object keeps its .close() for the db fixture's teardown.
        conn = connections.get('default')

        async def _boom(*args, **kwargs):
            raise RuntimeError('db gone')

        monkeypatch.setattr(conn, 'execute_query', _boom)
        async with client_for(app) as c:
            resp = await c.get('/api/health')
            assert resp.status_code == 503
            assert resp.json()['detail'] == 'database unavailable'


class TestAuditDetailDecoding:
    async def test_decodes_null_bad_and_good_details(self, db, app):
        user, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        await AuditLog.create(user=user, action='gap.none', details=None)
        await AuditLog.create(user=user, action='gap.badjson', details='not valid json{')
        await AuditLog.create(user=user, action='gap.goodjson', details='{"x": 1}')

        async with client_for(app, raw) as c:
            resp = await c.get('/api/audit-logs')
            assert resp.status_code == 200
            by_action = {e['action']: e['details'] for e in resp.json()['items']}
            assert by_action['gap.none'] is None
            assert by_action['gap.badjson'] == 'not valid json{'
            assert by_action['gap.goodjson'] == {'x': 1}


class TestUserProfileWrites:
    async def test_self_updates_own_profile(self, db, app):
        user, raw = await create_user_token(username='me')
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/users/{user.id}', json={'display_name': 'Renamed'})
            assert resp.status_code == 200
            assert resp.json()['display_name'] == 'Renamed'

    async def test_staff_updates_other_profile(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        other = await create_community_member(discord_id=4242, username='target')
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/users/{other.id}', json={'pronouns': 'they/them'})
            assert resp.status_code == 200
            assert resp.json()['pronouns'] == 'they/them'

    async def test_non_staff_cannot_update_other_profile(self, db, app):
        _, raw = await create_user_token(username='plain')
        other = await create_community_member(discord_id=4343, username='target')
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/users/{other.id}', json={'display_name': 'Nope'})
            assert resp.status_code == 403

    async def test_update_missing_user_is_404(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.patch('/api/users/999', json={'display_name': 'X'})).status_code == 404

    async def test_read_only_token_cannot_update_profile(self, db, app):
        user, raw = await create_user_token(username='ro', read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/users/{user.id}', json={'display_name': 'X'})
            assert resp.status_code == 403


class TestUserAdminFieldWrites:
    async def test_staff_deactivates_user(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        other = await create_community_member(discord_id=5151, username='target')
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/users/{other.id}/admin', json={'is_active': False})
            assert resp.status_code == 200
            assert resp.json()['is_active'] is False

    async def test_non_staff_cannot_update_admin_fields(self, db, app):
        user, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            # Staff-only even on one's own record -> service PermissionError -> 403.
            resp = await c.patch(f'/api/users/{user.id}/admin', json={'is_active': False})
            assert resp.status_code == 403

    async def test_admin_update_missing_user_is_404(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.patch('/api/users/999/admin', json={'is_active': False})).status_code == 404


class TestUserEnrollmentWrites:
    async def test_self_replaces_enrollments(self, db, app):
        user, raw = await create_user_token(username='me')
        t = await Tournament.create(name='Cup', is_active=True)
        async with client_for(app, raw) as c:
            resp = await c.put(f'/api/users/{user.id}/tournaments', json={'tournament_ids': [t.id]})
            assert resp.status_code == 200
            assert resp.json() == {'detail': 'Enrollments updated'}

    async def test_self_edit_keeps_the_signup_rules(self, db, app):
        """The self path used to enrol straight into closed or inactive
        tournaments, skipping everything the signup button checks."""
        from datetime import timedelta

        from application.utils.timezone import now_local

        user, raw = await create_user_token(username='late')
        closed = await Tournament.create(
            name='Closed', is_active=True, signups_close_at=now_local() - timedelta(days=1),
        )
        retired = await Tournament.create(name='Retired', is_active=False)
        async with client_for(app, raw) as c:
            for t in (closed, retired):
                resp = await c.put(
                    f'/api/users/{user.id}/tournaments', json={'tournament_ids': [t.id]},
                )
                assert resp.status_code == 400, resp.text
        assert not await TournamentPlayers.filter(user=user).exists()

    async def test_staff_replaces_other_enrollments(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        other = await create_community_member(discord_id=6161, username='target')
        t = await Tournament.create(name='Cup', is_active=True)
        async with client_for(app, raw) as c:
            resp = await c.put(f'/api/users/{other.id}/tournaments', json={'tournament_ids': [t.id]})
            assert resp.status_code == 200

    async def test_non_staff_cannot_replace_other_enrollments(self, db, app):
        _, raw = await create_user_token(username='plain')
        other = await create_community_member(discord_id=6262, username='target')
        async with client_for(app, raw) as c:
            resp = await c.put(f'/api/users/{other.id}/tournaments', json={'tournament_ids': []})
            assert resp.status_code == 403

    async def test_staff_enroll_missing_user_is_404(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.put('/api/users/999/tournaments', json={'tournament_ids': []})
            assert resp.status_code == 404


class TestUserRead:
    async def test_staff_get_missing_user_is_404(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/users/999')).status_code == 404


class TestStageDetail:
    async def test_get_stage_by_id(self, db, app):
        _, raw = await create_user_token(username='viewer')
        stage = await Stage.create(name='Stage One')
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/stages/{stage.id}')
            assert resp.status_code == 200
            assert resp.json()['name'] == 'Stage One'

    async def test_get_missing_stage_is_404(self, db, app):
        _, raw = await create_user_token(username='viewer')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/stages/999')).status_code == 404

    async def test_stage_requires_auth(self, db, app):
        async with client_for(app) as c:
            assert (await c.get('/api/stages/1')).status_code == 401


class TestTournamentMembership:
    async def test_staff_add_and_remove_admin(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        target = await create_community_member(discord_id=7171, username='ta')
        t = await Tournament.create(name='Cup')
        async with client_for(app, raw) as c:
            added = await c.post(f'/api/tournaments/{t.id}/admins', json={'user_id': target.id})
            assert added.status_code == 200
            removed = await c.delete(f'/api/tournaments/{t.id}/admins/{target.id}')
            assert removed.status_code == 204

    async def test_staff_add_and_remove_crew_coordinator(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        target = await create_community_member(discord_id=7272, username='cc')
        t = await Tournament.create(name='Cup')
        async with client_for(app, raw) as c:
            added = await c.post(
                f'/api/tournaments/{t.id}/crew-coordinators', json={'user_id': target.id}
            )
            assert added.status_code == 200
            assert added.json() == {'detail': 'Crew coordinator added'}
            removed = await c.delete(f'/api/tournaments/{t.id}/crew-coordinators/{target.id}')
            assert removed.status_code == 204

    async def test_granting_an_account_from_outside_the_community_is_404(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        outsider = await User.create(discord_id=7474, username='elsewhere')
        t = await Tournament.create(name='Cup')
        async with client_for(app, raw) as c:
            for path in ('admins', 'crew-coordinators'):
                resp = await c.post(f'/api/tournaments/{t.id}/{path}', json={'user_id': outsider.id})
                assert resp.status_code == 404, (path, resp.text)
        assert not await t.admins.filter(id=outsider.id).exists()
        assert not await t.crew_coordinators.filter(id=outsider.id).exists()

    async def test_add_admin_missing_tournament_is_404(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        target = await User.create(discord_id=7373, username='ta')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/tournaments/999/admins', json={'user_id': target.id})
            assert resp.status_code == 404

    async def test_add_admin_missing_user_is_404(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t = await Tournament.create(name='Cup')
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/tournaments/{t.id}/admins', json={'user_id': 999})
            assert resp.status_code == 404

    async def test_non_staff_cannot_add_crew_coordinator(self, db, app):
        _, raw = await create_user_token(username='plain')
        target = await User.create(discord_id=7474, username='cc')
        t = await Tournament.create(name='Cup')
        async with client_for(app, raw) as c:
            resp = await c.post(
                f'/api/tournaments/{t.id}/crew-coordinators', json={'user_id': target.id}
            )
            assert resp.status_code == 403


class TestMatchLifecycleNotFound:
    async def test_seat_missing_match_is_404(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.post('/api/matches/999/seat')).status_code == 404


class TestSubmitMatchRequest:
    async def test_player_submits_own_request(self, db, app):
        actor, raw = await create_user_token(username='requester', member=True)
        t = await Tournament.create(name='Cup', is_active=True)
        opponent = await create_community_member(discord_id=8181, username='opp')
        await TournamentPlayers.create(tournament=t, user=opponent, tenant_id=1)
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/matches/request',
                json={
                    'tournament_id': t.id,
                    'scheduled_date': '2099-06-10',
                    'scheduled_time': '18:00',
                    'player_ids': [actor.id, opponent.id],
                },
            )
            assert resp.status_code == 201
            assert len(resp.json()['players']) == 2

    async def test_non_player_cannot_submit_request(self, db, app):
        _, raw = await create_user_token(username='meddler')
        t = await Tournament.create(name='Cup', is_active=True)
        p1 = await User.create(discord_id=8282, username='a')
        p2 = await User.create(discord_id=8283, username='b')
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/matches/request',
                json={
                    'tournament_id': t.id,
                    'scheduled_date': '2099-06-10',
                    'scheduled_time': '18:00',
                    'player_ids': [p1.id, p2.id],
                },
            )
            assert resp.status_code == 403

    async def test_read_only_token_cannot_submit_request(self, db, app):
        actor, raw = await create_user_token(username='requester', read_only=True)
        t = await Tournament.create(name='Cup', is_active=True)
        async with client_for(app, raw) as c:
            resp = await c.post(
                '/api/matches/request',
                json={
                    'tournament_id': t.id,
                    'scheduled_date': '2099-06-10',
                    'scheduled_time': '18:00',
                    'player_ids': [actor.id],
                },
            )
            assert resp.status_code == 403


class TestUpdateMatch:
    async def test_staff_updates_comment(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            resp = await c.patch(f'/api/matches/{mid}', json={'comment': 'updated comment'})
            assert resp.status_code == 200
            assert resp.json()['comment'] == 'updated comment'

    async def test_player_as_commentator_is_400(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            resp = await c.patch(f'/api/matches/{mid}', json={'commentator_ids': [p1.id]})
            assert resp.status_code == 400

    async def test_update_missing_match_is_404(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.patch('/api/matches/999', json={'comment': 'x'})).status_code == 404


class TestStreamAssignments:
    async def test_set_stream_candidate(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            resp = await c.post(f'/api/matches/{mid}/stream-candidate', json={'flag': True})
            assert resp.status_code == 200
            # MatchResponse omits the flag; confirm the write persisted at the model.
            assert (await Match.get(id=mid)).is_stream_candidate is True

    async def test_assign_and_clear_stage(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        stage = await Stage.create(name='Stage A')
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            assigned = await c.post(f'/api/matches/{mid}/stage', json={'stage_id': stage.id})
            assert assigned.status_code == 200
            cleared = await c.post(f'/api/matches/{mid}/stage', json={'stage_id': None})
            assert cleared.status_code == 200

    async def test_assign_stations(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            mps = await MatchPlayers.filter(match_id=mid)
            assignments = {str(mp.id): None for mp in mps}
            resp = await c.post(f'/api/matches/{mid}/stations', json={'assignments': assignments})
            assert resp.status_code == 200


class TestGenerateSeed:
    async def test_no_generator_configured_is_400(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()  # seed_generator defaults to None
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            resp = await c.post(f'/api/matches/{mid}/seed')
            assert resp.status_code == 400
            assert 'No seed generator' in resp.json()['detail']

    async def test_generate_seed_success(self, db, app, monkeypatch):
        from application.services.seedgen_service import RolledSeed
        from application.utils.seed_provider import ProviderCall

        async def _fake_generate_seed_call(self, randomizer, preset=None, *, surface=None, triforce_text=None):
            return ProviderCall(
                value=RolledSeed(url='https://example.com/generated-seed'),
                provider=randomizer, operation='generate_seed', surface=surface,
            )

        monkeypatch.setattr(
            'application.services.seedgen_service.SeedGenerationService.generate_seed_call',
            _fake_generate_seed_call,
        )
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players(seed_generator='test')
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            resp = await c.post(f'/api/matches/{mid}/seed')
            assert resp.status_code == 200
            body = resp.json()
            assert body['seed_url'] == 'https://example.com/generated-seed'
            assert 'Seed rolled' in body['message']


class TestCrewSignup:
    async def test_signup_then_undo(self, db, app):
        _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
        caster, caster_raw = await create_user_token(username='caster')
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, staff_raw) as staff, client_for(app, caster_raw) as caster_c:
            mid = (await _create_match(staff, t, p1, p2)).json()['id']

            signup = await caster_c.post(f'/api/matches/{mid}/crew', json={'role': 'commentator'})
            assert signup.status_code == 201
            assert signup.json() == {'detail': 'Signed up as commentator'}

            undo = await caster_c.delete(f'/api/matches/{mid}/crew/commentator')
            assert undo.status_code == 204

    async def test_signup_refuses_a_non_member(self, db, app):
        """UI-only gating is not gating: the REST path hits the same wall."""
        _, staff_raw = await create_user_token(username='boss2', roles=[Role.STAFF])
        _, stranger_raw = await create_user_token(username='stranger', member=False)
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, staff_raw) as staff, client_for(app, stranger_raw) as stranger:
            mid = (await _create_match(staff, t, p1, p2)).json()['id']
            resp = await stranger.post(f'/api/matches/{mid}/crew', json={'role': 'commentator'})
            assert resp.status_code == 403
            assert 'not a member' in resp.json()['detail']

    async def test_invalid_crew_role_is_400(self, db, app):
        caster, raw = await create_user_token(username='caster')
        async with client_for(app, raw) as c:
            # No match needed: role validation raises ValueError before match lookup.
            resp = await c.post('/api/matches/1/crew', json={'role': 'wizard'})
            assert resp.status_code == 400
