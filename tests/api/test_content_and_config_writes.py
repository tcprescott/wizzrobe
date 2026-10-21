"""Tests for Phase 5 writes: stages, triforce, notifications, config."""


from typing import ClassVar

from models import Role, Tournament
from tests.api_helpers import client_for, create_user_token


class TestStages:
    async def test_stream_manager_crud(self, db, app):
        _, raw = await create_user_token(username='sm', roles=[Role.STREAM_MANAGER])
        async with client_for(app, raw) as c:
            created = await c.post('/api/stages', json={'name': 'Stage X'})
            assert created.status_code == 201
            rid = created.json()['id']

            updated = await c.patch(f'/api/stages/{rid}', json={'name': 'Stage Y'})
            assert updated.status_code == 200
            assert updated.json()['name'] == 'Stage Y'

            assert (await c.delete(f'/api/stages/{rid}')).status_code == 204

    async def test_plain_user_forbidden(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.post('/api/stages', json={'name': 'Z'})).status_code == 403

    async def test_duplicate_name_is_a_400_not_a_crash(self, db, app):
        _, raw = await create_user_token(username='sm', roles=[Role.STREAM_MANAGER])
        async with client_for(app, raw) as c:
            first = await c.post('/api/stages', json={'name': 'Main Stage'})
            other = await c.post('/api/stages', json={'name': 'Side Stage'})
            assert first.status_code == other.status_code == 201

            dupe = await c.post('/api/stages', json={'name': ' Main Stage '})
            assert dupe.status_code == 400
            assert 'already exists' in dupe.json()['detail']

            renamed = await c.patch(f"/api/stages/{other.json()['id']}", json={'name': 'Main Stage'})
            assert renamed.status_code == 400

            kept = await c.patch(f"/api/stages/{first.json()['id']}", json={'name': 'Main Stage'})
            assert kept.status_code == 200


class TestTriforce:
    async def test_submit_and_moderate(self, db, app):
        _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
        player, player_raw = await create_user_token(
            username='player', roles=[Role.TRIFORCE_SUBMITTER]
        )
        t = await Tournament.create(name='Cup', is_active=True, seed_generator='alttpr')

        async with client_for(app, player_raw) as player_c, client_for(app, staff_raw) as staff_c:
            submitted = await player_c.post(
                '/api/triforce-texts', json={'tournament_id': t.id, 'lines': ['HELLO', 'WORLD', '']}
            )
            assert submitted.status_code == 201
            text_id = submitted.json()['id']

            # Player cannot moderate.
            assert (await player_c.post(
                f'/api/triforce-texts/{text_id}/moderate', json={'approved': True}
            )).status_code == 400  # service raises ValueError -> 400

            # Staff approves.
            approved = await staff_c.post(
                f'/api/triforce-texts/{text_id}/moderate', json={'approved': True}
            )
            assert approved.status_code == 200
            assert approved.json()['approved'] is True

    async def test_submit_invalid_lines(self, db, app):
        _, raw = await create_user_token(username='player', roles=[Role.TRIFORCE_SUBMITTER])
        t = await Tournament.create(name='Cup', is_active=True, seed_generator='alttpr')
        async with client_for(app, raw) as c:
            # Only 2 lines -> service requires exactly 3 -> 400.
            resp = await c.post('/api/triforce-texts', json={'tournament_id': t.id, 'lines': ['A', 'B']})
            assert resp.status_code == 400


class TestNotifications:
    async def test_upsert_and_list_preference(self, db, app):
        _, raw = await create_user_token(username='player')
        t = await Tournament.create(name='Cup', is_active=True)
        async with client_for(app, raw) as c:
            put = await c.put(
                '/api/notifications/preferences',
                json={'tournament_id': t.id, 'match_notifications': 'all'},
            )
            assert put.status_code == 200
            assert put.json()['match_notifications'] == 'all'

            listed = await c.get('/api/notifications/preferences')
            assert any(p['tournament_id'] == t.id for p in listed.json())


    async def test_my_messages_preset_and_one_message(self, db, app):
        _, raw = await create_user_token(username='player')
        async with client_for(app, raw) as c:
            mine = (await c.get('/api/notifications/messages')).json()
            assert mine['preset'] == 'everything'
            assert not any(m['section'] == 'Staff alerts' for m in mine['messages'])

            quiet = (await c.put('/api/notifications/messages/preset', json={'preset': 'quiet'})).json()
            watched = next(m for m in quiet['messages'] if m['notice'] == 'match_scheduled' and m['recipient'] == 'watchers')
            assert watched['mode'] == 'off'

            one = await c.put('/api/notifications/messages/match_scheduled/watchers', json={'mode': 'silent'})
            assert one.status_code == 200
            assert one.json()['changed'] is True and one.json()['mode'] == 'silent'

            reset = (await c.post('/api/notifications/messages/reset')).json()
            assert reset['preset'] == 'quiet' and reset['changed'] == 0

            assert (await c.put('/api/notifications/messages/nope/players', json={'mode': 'off'})).status_code == 400
            assert (await c.put('/api/notifications/messages/preset', json={'preset': 'loud'})).status_code == 422

class TestConfig:
    async def test_staff_sets_config(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            put = await c.put('/api/config/max_concurrent_players', json={'value': '40'})
            assert put.status_code == 200
            assert put.json()['value'] == '40'

    async def test_non_staff_cannot_set_config(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.put('/api/config/max_concurrent_players', json={'value': '40'})
            assert resp.status_code == 403

    async def test_read_only_staff_token_cannot_set_config(self, db, app):
        """require_staff_write rejects read-only tokens even for Staff."""
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF], read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.put('/api/config/max_concurrent_players', json={'value': '40'})
            assert resp.status_code == 403


class TestDiscordRoleMappingWrites:
    _BODY: ClassVar[dict] = {
        'guild_id': 1,
        'discord_role_id': 2,
        'discord_role_name': 'Mods',
        'app_role': 'staff',
    }

    async def test_staff_creates_mapping(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/discord-role-mappings', json=self._BODY)
            assert resp.status_code == 201

    async def test_non_staff_cannot_create_mapping(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/discord-role-mappings', json=self._BODY)
            assert resp.status_code == 403

    async def test_read_only_staff_token_cannot_create_mapping(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF], read_only=True)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/discord-role-mappings', json=self._BODY)
            assert resp.status_code == 403
