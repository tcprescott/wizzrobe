"""Mystery weightsets in the preset library: check, save, read the odds, roll, import."""

import pytest
import yaml

from application.utils.clients import sahasrahbot_client
from tests.api_helpers import client_for, create_user_token


@pytest.fixture(autouse=True)
def _mock_seedgen(monkeypatch):
    monkeypatch.setenv('MOCK_SEEDGEN', '1')


def _friendly():
    with open('presets/alttprmystery/friendly.yaml', encoding='utf-8') as f:
        return yaml.safe_load(f)['settings']


async def test_write_a_weightset_then_read_its_odds_and_roll_it(db, app):
    _, raw = await create_user_token(username='tom')
    async with client_for(app, raw) as c:
        bad = await c.post('/api/preset-library/mystery/check', json={'weightset': 'world_state: {open: 1}'})
        assert bad.status_code == 400 and 'glitches_required' in bad.json()['detail']
        checked = await c.post('/api/preset-library/mystery/check', json={'weightset': yaml.safe_dump(_friendly())})
        assert checked.status_code == 200, checked.text
        made = await c.post('/api/preset-library/personal', json={
            'name': 'myfriendly', 'randomizer': 'alttprmystery', 'settings': checked.json()['settings'],
        })
        assert made.status_code == 201, made.text
        detail = (await c.get(f"/api/preset-library/{made.json()['id']}")).json()
        assert detail['randomizer_name'] == 'A Link to the Past (mystery)'
        odds = {o['setting']: o for o in detail['odds']}
        assert odds['world_state']['options'][0] == {'value': 'open', 'percent': 35.0}
        assert yaml.safe_load(detail['settings_yaml']) == detail['settings']
        rolled = await c.post(f"/api/preset-library/{made.json()['id']}/roll")
        assert rolled.status_code == 200 and rolled.json()['seed_url']
        broken = await c.post('/api/preset-library/personal', json={
            'name': 'broken', 'randomizer': 'alttprmystery', 'settings': {'world_state': {'open': 1}},
        })
    assert broken.status_code == 400


async def test_builtin_weightsets_are_in_the_library(db, app):
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as c:
        rows = {r['reference']: r for r in (await c.get('/api/preset-library')).json()}
        friendly = rows['wizzrobe/friendly']
        assert friendly['randomizer'] == 'alttprmystery'
        detail = (await c.get(f"/api/preset-library/{friendly['id']}")).json()
    assert detail['odds'] and detail['settings_yaml']


async def test_a_settings_preset_has_no_odds(db, app):
    _, raw = await create_user_token(username='tom')
    async with client_for(app, raw) as c:
        made = await c.post('/api/preset-library/personal', json={'name': 'plain', 'randomizer': 'alttpr', 'settings': {}})
        detail = (await c.get(f"/api/preset-library/{made.json()['id']}")).json()
    assert detail['odds'] is None and detail['settings_yaml'] is None


async def test_import_a_sahasrahbot_weightset(db, app, monkeypatch):
    async def namespace_presets(randomizer, namespace):
        return ['mine'] if randomizer == 'alttprmystery' else []

    async def fetch_preset(randomizer, namespace, name):
        return {'description': 'Mine.', **_friendly()}

    monkeypatch.setattr(sahasrahbot_client, 'namespace_presets', namespace_presets)
    monkeypatch.setattr(sahasrahbot_client, 'fetch_preset', fetch_preset)
    _, raw = await create_user_token(username='tom')
    async with client_for(app, raw) as c:
        listed = (await c.get('/api/preset-library/sahasrahbot/tom')).json()
        assert listed == [{'randomizer': 'alttprmystery', 'name': 'mine', 'exists': False}]
        done = await c.post('/api/preset-library/sahasrahbot/tom/import',
                            json={'presets': [{'randomizer': 'alttprmystery', 'name': 'mine'}]})
        assert done.json()[0]['preset_id'], done.text
        refs = {p['reference']: p for p in (await c.get('/api/preset-library')).json()}
    assert refs['tom/mine']['randomizer'] == 'alttprmystery' and refs['tom/mine']['description'] == 'Mine.'
