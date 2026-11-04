"""The read-only built-in preset space and importing presets from SahasrahBot.

Every file under ``presets/`` (Wizzrobe's own plus SahasrahBot's global
presets) shows in the library as ``wizzrobe/<name>``, synced from the files.
Players import their SahasrahBot namespace into their own space; presets
Wizzrobe can't roll are reported, not imported.
"""

import pytest

from application.services.builtin_preset_service import BuiltinPresetService
from application.utils.clients import sahasrahbot_client
from models import Preset, PresetSpace, PresetSpaceKind
from tests.api_helpers import client_for, create_user_token


@pytest.fixture(autouse=True)
def _mock_seedgen(monkeypatch):
    monkeypatch.setenv('MOCK_SEEDGEN', '1')


async def test_builtin_presets_show_in_the_library_read_only(db, app):
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as c:
        rows = (await c.get('/api/preset-library')).json()
        builtin = {r['reference']: r for r in rows if r['space_kind'] == 'builtin'}
        assert 'wizzrobe/open' in builtin and 'wizzrobe/casualboots' in builtin
        assert 'wizzrobe/crosskeys' in builtin
        assert 'wizzrobe/tournament_full' in builtin
        row = builtin['wizzrobe/open']
        assert row['can_edit'] is False and row['space_name'] == 'Wizzrobe'
        assert (await c.patch(f"/api/preset-library/personal/{row['id']}", json={'name': 'mine'})).status_code == 404
        assert (await c.post(f"/api/preset-library/{row['id']}/roll")).status_code == 200
        copy = await c.post(f"/api/preset-library/{row['id']}/copy-to-mine", json={})
        assert copy.status_code == 201 and copy.json()['copied_from'] == 'wizzrobe/open'


async def test_sync_is_idempotent_and_follows_the_files(db):
    service = BuiltinPresetService()
    await service.ensure_synced()
    space = await PresetSpace.get(kind=PresetSpaceKind.BUILTIN)
    count = await Preset.filter(space_id=space.id).count()
    assert count > 100
    assert await Preset.filter(space_id=space.id, tenant_id__isnull=False).count() == 0

    stale = await Preset.get(space_id=space.id, randomizer='alttpr', name='open')
    stale.settings = {'mode': 'changed'}
    await stale.save(update_fields=['settings'])
    await Preset.create(space_id=space.id, tenant_id=None, randomizer='alttpr', name='gone', settings={})

    from application.services import builtin_preset_service
    builtin_preset_service.reset_builtin_sync()
    await service.ensure_synced()
    assert await Preset.filter(space_id=space.id).count() == count
    assert (await Preset.get(id=stale.id)).settings != {'mode': 'changed'}
    assert not await Preset.exists(space_id=space.id, name='gone')


async def test_door_presets_are_built_in_and_forced_locations_are_not(db):
    await BuiltinPresetService().ensure_synced()
    rows = await Preset.filter(space__kind=PresetSpaceKind.BUILTIN).values_list('randomizer', 'name')
    assert ('alttprdr', 'crossdoors') in rows
    assert 'invrosia' not in {name for _, name in rows}


def _fake_sahasrahbot(monkeypatch, presets):
    async def namespace_presets(randomizer, namespace):
        return [n for (r, ns, n) in presets if r == randomizer and ns == namespace]

    async def fetch_preset(randomizer, namespace, name):
        for (r, ns, n), data in presets.items():
            if (r, ns, n) == (randomizer, namespace, name):
                return data
        raise sahasrahbot_client.SahasrahBotAPIError(f'SahasrahBot has no {randomizer} preset {namespace}/{name}')

    monkeypatch.setattr(sahasrahbot_client, 'namespace_presets', namespace_presets)
    monkeypatch.setattr(sahasrahbot_client, 'fetch_preset', fetch_preset)


async def test_import_a_sahasrahbot_namespace(db, app, monkeypatch):
    _fake_sahasrahbot(monkeypatch, {
        ('alttpr', 'tom', 'FastBoots'): {'description': 'Fast', 'settings': {'mode': 'open'}},
        ('alttpr', 'tom', 'doorsy'): {'doors': True, 'settings': {'door_shuffle': 'basic'}},
        ('alttpr', 'tom', 'forced'): {'forced_locations': [{'item': 'Hammer'}], 'settings': {'mode': 'open'}},
        ('alttpr', 'tom', 'avi'): {'avianart': True, 'settings': {'preset': 'crosskeys'}},
        ('smz3', 'tom', 'normal'): {'settings': {'smlogic': 'normal', 'goal': 'defeatboth'}},
        ('alttpr', 'ann', 'other'): {'settings': {}},
    })
    _, raw = await create_user_token(username='tom')
    async with client_for(app, raw) as c:
        listed = (await c.get('/api/preset-library/sahasrahbot/tom')).json()
        assert {(r['randomizer'], r['name']) for r in listed} == {
            ('alttpr', 'FastBoots'), ('alttpr', 'doorsy'), ('alttpr', 'forced'), ('alttpr', 'avi'), ('smz3', 'normal'),
        }
        picks = [{'randomizer': r['randomizer'], 'name': r['name']} for r in listed]
        resp = await c.post('/api/preset-library/sahasrahbot/tom/import', json={'presets': picks})
        assert resp.status_code == 200, resp.text
        results = {r['name']: r for r in resp.json()}
        assert results['forced']['problem'] and results['forced']['preset_id'] is None
        for name in ('FastBoots', 'doorsy', 'avi', 'normal'):
            assert results[name]['preset_id'] and results[name]['problem'] is None
        refs = {p['reference']: p for p in (await c.get('/api/preset-library')).json()}
        again = (await c.get('/api/preset-library/sahasrahbot/tom')).json()
    assert refs['tom/fastboots']['randomizer'] == 'alttpr'
    assert refs['tom/avi']['randomizer'] == 'avianart'
    assert refs['tom/normal']['randomizer'] == 'smz3'
    assert refs['tom/doorsy']['randomizer'] == 'alttprdr'
    assert {r['name'] for r in again if r['exists']} == {'FastBoots', 'doorsy', 'avi', 'normal'}


async def test_import_rejects_bad_input(db, app, monkeypatch):
    _fake_sahasrahbot(monkeypatch, {})
    _, raw = await create_user_token(username='tom')
    async with client_for(app, raw) as c:
        assert (await c.get('/api/preset-library/sahasrahbot/bad name!')).status_code == 400
        empty = await c.post('/api/preset-library/sahasrahbot/tom/import', json={'presets': []})
        assert empty.status_code == 400
        wrong = await c.post('/api/preset-library/sahasrahbot/tom/import', json={'presets': [{'randomizer': 'ootr', 'name': 'x'}]})
        assert wrong.status_code == 400
        many = [{'randomizer': 'alttpr', 'name': f'p{i}'} for i in range(26)]
        assert (await c.post('/api/preset-library/sahasrahbot/tom/import', json={'presets': many})).status_code == 400


async def test_a_player_cannot_take_the_builtin_slug(db, app):
    _, raw = await create_user_token(username='wizzrobe')
    async with client_for(app, raw) as c:
        made = await c.post('/api/preset-library/personal', json={'name': 'fast', 'randomizer': 'alttpr', 'settings': {}})
        assert made.json()['space'] != 'wizzrobe'
        space_id = (await c.get('/api/preset-library/spaces')).json()[0]['id']
        taken = await c.patch(f'/api/preset-library/spaces/{space_id}', json={'slug': 'wizzrobe'})
    assert taken.status_code == 400


def test_to_wizzrobe_maps_and_refuses():
    assert sahasrahbot_client.to_wizzrobe('alttpr', {'avianart': True, 'settings': {'preset': 'x'}}).randomizer == 'avianart'
    with pytest.raises(ValueError):
        sahasrahbot_client.to_wizzrobe('alttpr', {'forced_locations': [{}], 'settings': {}})
    with pytest.raises(ValueError):
        sahasrahbot_client.to_wizzrobe('alttpr', {'settings': 'nope'})


def test_to_wizzrobe_drops_keys_the_roll_owns():
    got = sahasrahbot_client.to_wizzrobe('smz3', {'settings': {'race': 'true', 'seed': '1', 'goal': 'defeatboth'}})
    assert got.settings == {'goal': 'defeatboth'}
