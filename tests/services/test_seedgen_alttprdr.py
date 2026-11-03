"""The Door Randomizer backend: roll through the sidecar, host the patch, gate the spoiler.

The sidecar is stood in for by a real aiohttp server on loopback, so the HTTP
path (auth header, status mapping, base patch fetch) runs for real.
"""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from application.errors import NotFoundError
from application.services import door_seed_service
from application.services.door_seed_service import DoorSeedService
from application.services.seedgen_service import SeedGenerationService
from application.utils.clients import alttpr_sprites_client
from application.utils.door_seed_store import DoorSeedStore
from application.utils.seed_provider import SeedProviderInvalidRequest
from models import DoorSeed, Preset, Role, UserRole
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("db")

# Five file-select items at 0x180215: Bow, Hookshot, Bombs, Mushroom, Lamp.
_PATCH = {'1572864': [1, 2, 3], str(0x180215 - 2): [9, 9, 0, 2, 3, 4, 11]}


class _Sidecar:
    def __init__(self):
        self.requests = []
        self.generate_status = 200
        self.base_fetches = 0

    async def generate(self, request):
        self.requests.append({'auth': request.headers.get('Authorization'), 'body': await request.json()})
        if self.generate_status != 200:
            return web.json_response({'error': 'Generation failed after 4 attempts: no layout'}, status=self.generate_status)
        return web.json_response({
            'version': '1.5.7-u', 'commit': 'abc', 'seed': 42, 'attempts': 1,
            'patch': _PATCH, 'spoiler': {'meta': {'seed': 42}, 'Hashes': {}},
        })

    async def base_patch(self, _request):
        self.base_fetches += 1
        return web.Response(body=b'BPS1-fake', headers={'X-Door-Rando-Version': '1.5.7-u'})


@pytest.fixture
async def sidecar(monkeypatch, tmp_path):
    fake = _Sidecar()
    app = web.Application()
    app.router.add_post('/generate', fake.generate)
    app.router.add_get('/base-patch', fake.base_patch)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setenv('DOOR_RANDO_URL', str(server.make_url('')))
    monkeypatch.setenv('DOOR_RANDO_TOKEN', 'sekrit')
    monkeypatch.setenv('DOOR_SEED_DIR', str(tmp_path))
    monkeypatch.delenv('MOCK_SEEDGEN', raising=False)
    yield fake
    await server.close()


def _preset(spoiler='staff'):
    return Preset(
        name='crossed', randomizer='alttprdr',
        settings={'settings': {'door_shuffle': 'crossed', 'intensity': 2}, 'spoiler': spoiler},
    )


async def test_a_roll_hosts_the_patch_and_links_the_patcher(sidecar, tmp_path):
    call = await SeedGenerationService().generate_seed_call('alttprdr', _preset())
    seed = await DoorSeed.get(tenant_id=DEFAULT_TEST_TENANT_ID)

    assert call.value.url.endswith(f'/door/{seed.token}')
    assert call.value.code == ['Bow', 'Hookshot', 'Bombs', 'Mushroom', 'Lamp']
    assert seed.seed_code == call.value.code
    assert call.value.settings['settings'] == {'door_shuffle': 'crossed', 'intensity': 2}
    assert call.value.settings['version'] == '1.5.7-u'
    assert sidecar.requests == [{'auth': 'Bearer sekrit', 'body': {'settings': {'door_shuffle': 'crossed', 'intensity': 2}}}]

    store = DoorSeedStore(str(tmp_path))
    assert json.loads(await store.get_patch(seed.token)) == {'version': '1.5.7-u', 'patch': _PATCH}
    assert await store.get_base_patch('1.5.7-u') == b'BPS1-fake'


async def test_the_base_patch_is_fetched_once_per_version(sidecar):
    service = SeedGenerationService()
    await service.generate_seed_call('alttprdr', _preset())
    await service.generate_seed_call('alttprdr', _preset())
    assert sidecar.base_fetches == 1


async def test_a_failed_layout_is_not_retried_here(sidecar):
    sidecar.generate_status = 422
    with pytest.raises(SeedProviderInvalidRequest):
        await SeedGenerationService().generate_seed_call('alttprdr', _preset())
    assert len(sidecar.requests) == 1
    assert await DoorSeed.all().count() == 0


async def test_rolling_needs_a_preset(sidecar):
    with pytest.raises(ValueError, match='preset'):
        await SeedGenerationService().generate_seed_call('alttprdr', None)


@pytest.mark.parametrize('settings', [
    {}, {'settings': {}}, {'settings': {'a': 1}, 'spoiler': 'everyone'}, {'settings': {'a': 1}, 'extra': 1},
])
def test_preset_shape_is_checked_on_save(settings):
    with pytest.raises(ValueError):
        SeedGenerationService.normalize_preset_settings('alttprdr', settings)


def test_preset_spoiler_defaults_to_staff():
    assert SeedGenerationService.normalize_preset_settings('alttprdr', {'settings': {'a': 1}}) == {
        'settings': {'a': 1}, 'spoiler': 'staff',
    }


def test_not_offered_without_a_sidecar(monkeypatch):
    monkeypatch.delenv('DOOR_RANDO_URL', raising=False)
    monkeypatch.delenv('MOCK_SEEDGEN', raising=False)
    assert 'alttprdr' not in SeedGenerationService.available_randomizers(set())
    monkeypatch.setenv('DOOR_RANDO_URL', 'http://door-rando:8080')
    assert 'alttprdr' in SeedGenerationService.available_randomizers(set())


class TestSpoilerAccess:
    async def _roll(self, spoiler):
        await SeedGenerationService().generate_seed_call('alttprdr', _preset(spoiler))
        return await DoorSeed.get(tenant_id=DEFAULT_TEST_TENANT_ID)

    async def test_staff_spoiler_is_staff_only(self, sidecar):
        seed = await self._roll('staff')
        player = await make_user(discord_id=700, username='player')
        staff = await make_user(discord_id=701, username='staffer')
        await UserRole.create(user=staff, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
        service = DoorSeedService()

        assert json.loads(await service.spoiler_for(seed, staff))['meta']['seed'] == 42
        with pytest.raises(NotFoundError):
            await service.spoiler_for(seed, player)
        with pytest.raises(NotFoundError):
            await service.spoiler_for(seed, None)
        with pytest.raises(NotFoundError):
            await service.spoiler_by_token(seed.token, None)

    async def test_public_spoiler_is_open_to_anyone(self, sidecar):
        seed = await self._roll('public')
        service = DoorSeedService()
        assert await service.spoiler_for(seed, None)
        assert await service.spoiler_by_token(seed.token, None)

    async def test_none_keeps_no_spoiler(self, sidecar, tmp_path):
        seed = await self._roll('none')
        assert await DoorSeedStore(str(tmp_path)).get_spoiler(seed.token) is None
        with pytest.raises(NotFoundError):
            await DoorSeedService().spoiler_for(seed, None)


@pytest.mark.parametrize('name', ['../etc', '.hidden', 'a/b', ''])
async def test_store_refuses_path_tricks(tmp_path, name, db):
    with pytest.raises(ValueError):
        await DoorSeedStore(str(tmp_path)).get_patch(name)
    with pytest.raises(NotFoundError):
        await DoorSeedService(DoorSeedStore(str(tmp_path))).patch(name)


class TestSpriteList:
    def test_trim_keeps_only_alttpr_asset_links(self):
        prefix = alttpr_sprites_client.ASSET_PREFIX
        sprites = alttpr_sprites_client.trim_sprite_list([
            {'name': 'Link', 'author': 'Nintendo', 'file': prefix + '001.link.1.zspr', 'preview': prefix + 'l.png'},
            {'name': 'Abigail', 'author': 'F', 'file': prefix + 'abigail.1.zspr', 'preview': 'https://evil.example/x.png',
             'tags': ['Female', 3]},
            {'name': 'Elsewhere', 'file': 'https://evil.example/x.zspr'},
            'junk',
        ])
        assert [s['name'] for s in sprites] == ['Link', 'Abigail']
        assert sprites[0]['default'] is True
        assert sprites[1] == {
            'default': False, 'name': 'Abigail', 'author': 'F', 'file': prefix + 'abigail.1.zspr',
            'preview': None, 'tags': ['Female'],
        }

    async def test_list_is_cached_and_survives_an_outage(self, monkeypatch):
        calls = []

        async def fetch():
            calls.append(1)
            if len(calls) > 1:
                raise alttpr_sprites_client.SpriteListError('down')
            return [{'name': 'Abigail'}]

        monkeypatch.setattr(alttpr_sprites_client, 'fetch_sprite_list', fetch)
        monkeypatch.setattr(door_seed_service, '_sprite_cache', {'sprites': None, 'expires': 0.0})
        assert await DoorSeedService.sprite_list() == [{'name': 'Abigail'}]
        assert await DoorSeedService.sprite_list() == [{'name': 'Abigail'}]
        assert len(calls) == 1
        door_seed_service._sprite_cache['expires'] = 0.0
        assert await DoorSeedService.sprite_list() == [{'name': 'Abigail'}]
        assert len(calls) == 2
