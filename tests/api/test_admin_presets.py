"""The app's Admin → Seed Presets screen: editor context and checking a pasted value."""

from models import Preset, Role, Tournament
from tests.api_helpers import client_for, create_user_token


class TestEditor:
    async def test_lists_randomizers_and_usage(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.PRESET_MANAGER])
        preset = await Preset.create(name='sgl2026', randomizer='z1r', settings={'flags': 'abc'})
        await Tournament.create(name='Zelda 1', preset=preset)
        async with client_for(app, raw) as c:
            body = (await c.get('/api/admin/presets/editor')).json()
        z1r = next(r for r in body['randomizers'] if r['slug'] == 'z1r')
        assert z1r['rolls_from_flags'] is True
        assert z1r['paste_field']['key'] == 'flags'
        assert body['usage'] == [{'preset_id': preset.id, 'tournaments': ['Zelda 1']}]

    async def test_a_plain_member_is_refused(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/presets/editor')).status_code == 403
            assert (await c.post('/api/admin/presets/check-paste',
                                 json={'randomizer': 'z1r', 'value': 'x'})).status_code == 403


class TestCheckPaste:
    async def test_flag_string_gives_an_example(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            body = (await c.post('/api/admin/presets/check-paste',
                                 json={'randomizer': 'z1r', 'value': 'ABCDEF'})).json()
            assert body['kind'] == 'example' and 'ABCDEF' in body['example']
            none = (await c.post('/api/admin/presets/check-paste',
                                 json={'randomizer': 'alttpr', 'value': '{}'})).json()
            assert none['kind'] == 'none'

    async def test_bad_ffr_link_says_why(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/admin/presets/check-paste',
                                json={'randomizer': 'ff1r', 'value': 'https://example.com/'})
        assert resp.status_code == 400
