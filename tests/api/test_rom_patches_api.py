"""Door seeds, hosted patches and Admin → Patches over REST."""

import json

from application.utils.door_seed_store import DoorSeedStore
from models import DoorSeed, Role, Tenant, Tournament
from tests.api_helpers import client_for, create_user_token

HEADER = {'X-Wizzrobe-Community': 'default'}
IPS = b'PATCH' + b'\x00\x00\x10' + b'\x00\x02' + b'\xAA\xBB' + b'EOF'


async def _seed(spoiler: str = 'staff') -> DoorSeed:
    store = DoorSeedStore()
    await store.put_patch('tok-door-1', json.dumps({'patch': {'1': [2]}}).encode())
    await store.put_spoiler('tok-door-1', b'{"meta": {}}')
    await store.put_base_patch('1.5.7-u', b'BPS1fake')
    return await DoorSeed.create(token='tok-door-1', version='1.5.7-u', seed_code=['Bow', 'Lamp'],
                                 spoiler_access=spoiler, tenant_id=1)


class TestDoorSeeds:
    async def test_signed_out_reads_the_seed_and_its_files(self, db, app):
        await _seed()
        async with client_for(app) as c:
            body = (await c.get('/api/door-seeds/tok-door-1', headers=HEADER)).json()
            assert body['seed_code'] == ['Bow', 'Lamp']
            assert body['spoiler'] is False and body['spoiler_staff_only'] is True
            assert body['filename'] == 'DR_tok-door-1.sfc'
            patch = await c.get('/api/door-seeds/tok-door-1/patch', headers=HEADER)
            assert patch.json() == {'patch': {'1': [2]}}
            base = await c.get('/api/door-seeds/base/1.5.7-u', headers=HEADER)
            assert base.content == b'BPS1fake'
            assert (await c.get('/api/door-seeds/tok-door-1/spoiler', headers=HEADER)).status_code == 404

    async def test_staff_read_a_staff_spoiler(self, db, app):
        await _seed()
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/door-seeds/tok-door-1', headers=HEADER)).json()['spoiler'] is True
            spoiler = await c.get('/api/door-seeds/tok-door-1/spoiler', headers=HEADER)
        assert spoiler.status_code == 200
        assert 'attachment' in spoiler.headers['content-disposition']

    async def test_a_public_spoiler_is_open(self, db, app):
        await _seed('public')
        async with client_for(app) as c:
            assert (await c.get('/api/door-seeds/tok-door-1/spoiler', headers=HEADER)).status_code == 200

    async def test_another_community_cannot_open_it(self, db, app):
        await _seed('public')
        await Tenant.create(name='Elsewhere', slug='elsewhere')
        other = {'X-Wizzrobe-Community': 'elsewhere'}
        async with client_for(app) as c:
            assert (await c.get('/api/door-seeds/tok-door-1', headers=other)).status_code == 404
            assert (await c.get('/api/door-seeds/tok-door-1/patch', headers=other)).status_code == 404


class TestHostedPatches:
    async def test_manage_then_play(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.PRESET_MANAGER])
        tournament = await Tournament.create(name='Super Metroid')
        async with client_for(app, raw) as c:
            created = await c.post(
                '/api/admin/patches', content=IPS,
                params={'title': 'Practice ROM', 'game': 'Super Metroid (JU)', 'filename': 'p.ips',
                        'source_crc': 'd63ed5f8', 'tournament_id': tournament.id},
            )
            assert created.status_code == 201, created.text
            patch = created.json()
            assert patch['format'] == 'ips' and patch['source_crc'] == 'D63ED5F8'
            assert patch['tournament'] == 'Super Metroid' and patch['filename_base'] == 'Practice_ROM'
            board = (await c.get('/api/admin/patches')).json()
            assert [p['title'] for p in board['patches']] == ['Practice ROM']
            assert {'id': tournament.id, 'name': 'Super Metroid'} in board['tournaments']
            edited = await c.put(f"/api/admin/patches/{patch['id']}",
                                 json={'title': 'Practice', 'game': 'SM', 'tournament_id': None})
            assert edited.json()['title'] == 'Practice' and edited.json()['tournament'] is None

        async with client_for(app) as c:
            info = (await c.get(f"/api/patches/{patch['token']}", headers=HEADER)).json()
            assert info['title'] == 'Practice' and info['community']
            file = await c.get(f"/api/patches/{patch['token']}/file", headers=HEADER)
            assert file.content == IPS
            counted = await c.post(f"/api/patches/{patch['token']}/downloaded", headers=HEADER)
            assert counted.status_code == 204

        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/patches')).json()['patches'][0]['download_count'] == 1
            assert (await c.delete(f"/api/admin/patches/{patch['id']}")).status_code == 204
        async with client_for(app) as c:
            assert (await c.get(f"/api/patches/{patch['token']}", headers=HEADER)).status_code == 404

    async def test_not_a_patch_is_a_400(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/admin/patches', content=b'hello',
                                params={'title': 'x', 'game': 'y', 'filename': 'x.bin'})
        assert resp.status_code == 400

    async def test_a_plain_member_is_refused(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/patches')).status_code == 403
            resp = await c.post('/api/admin/patches', content=IPS,
                                params={'title': 'x', 'game': 'y', 'filename': 'x.ips'})
            assert resp.status_code == 403

    async def test_another_communitys_patch_is_not_found(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            token = (await c.post('/api/admin/patches', content=IPS,
                                  params={'title': 'x', 'game': 'y', 'filename': 'x.ips'})).json()['token']
        await Tenant.create(name='Elsewhere', slug='elsewhere')
        async with client_for(app) as c:
            assert (await c.get(f'/api/patches/{token}', headers={'X-Wizzrobe-Community': 'elsewhere'})).status_code == 404
