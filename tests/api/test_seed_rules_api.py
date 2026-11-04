"""Seed rules over REST: read, save, preview a match, and the text view."""

from datetime import datetime, timedelta, timezone

from models import Match, Preset, Tournament
from tests.api._bracket_api import staff_token
from tests.api_helpers import client_for, create_user_token


async def _cup():
    open_ = await Preset.create(name='Open', randomizer='alttpr', settings={'mode': 'open'})
    std = await Preset.create(name='Standard', randomizer='alttpr', settings={'mode': 'standard'})
    other = await Preset.create(name='Smz3 thing', randomizer='smz3', settings={})
    cup = await Tournament.create(name='Cup', seed_generator='alttpr', preset=open_)
    return cup, open_, std, other


class TestSeedRules:
    async def test_save_read_and_preview(self, db, app):
        _, raw = await staff_token()
        cup, open_, std, other = await _cup()
        match = await Match.create(
            tournament=cup, title='Grand Final', scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        rules = [
            {'name': 'Finals', 'when': {'title': ['final']}, 'then': {'preset': std.id},
             'tweaks': {'item.swords': 'assured'}},
            {'name': 'Mix', 'then': {'weighted': [{'preset': open_.id, 'weight': 3}, {'preset': std.id, 'weight': 1}]}},
        ]
        url = f'/api/admin/tournaments/{cup.id}/seed-rules'
        async with client_for(app, raw) as c:
            empty = (await c.get(url)).json()
            assert empty['rules'] == [] and empty['default_preset']['name'] == 'Open'
            assert {p['name'] for p in empty['presets']} == {'Open', 'Standard'}
            assert [m['id'] for m in empty['matches']] == [match.id]

            saved = await c.put(url, json={'rules': rules})
            assert saved.status_code == 200, saved.text
            body = saved.json()
            assert [r['name'] for r in body['rules']] == ['Finals', 'Mix']
            assert body['rules'][0]['when']['title'] == ['final']

            hit = (await c.post(f'{url}/preview', json={'match_id': match.id})).json()
            assert (hit['source'], hit['rule_number'], hit['rule_name']) == ('rule', 1, 'Finals')
            assert hit['settings'] == {'mode': 'standard', 'item': {'swords': 'assured'}}
            assert hit['match_label']

            mix = (await c.post(f'{url}/preview', json={'facts': {'title': 'Round 1'}})).json()
            assert mix['rule_name'] == 'Mix' and mix['settings'] is None
            assert [(o['name'], o['percent']) for o in mix['outcomes']] == [('Open', 75.0), ('Standard', 25.0)]

            draft = (await c.post(f'{url}/preview', json={'rules': [], 'facts': {}})).json()
            assert draft['source'] == 'default' and draft['outcomes'][0]['name'] == 'Open'

    async def test_a_bad_rule_and_another_randomizer_are_refused(self, db, app):
        _, raw = await staff_token()
        cup, _, _, other = await _cup()
        url = f'/api/admin/tournaments/{cup.id}/seed-rules'
        async with client_for(app, raw) as c:
            bad = await c.put(url, json={'rules': [{'then': {}}]})
            assert bad.status_code == 400 and 'Rule 1' in bad.text
            wrong = await c.put(url, json={'rules': [{'then': {'preset': other.id}}]})
            assert wrong.status_code == 400 and 'smz3 preset' in wrong.text

    async def test_text_round_trip(self, db, app):
        _, raw = await staff_token()
        cup, _, std, _ = await _cup()
        url = f'/api/admin/tournaments/{cup.id}/seed-rules'
        async with client_for(app, raw) as c:
            await c.put(url, json={'rules': [{'name': 'G2', 'when': {'game': [2]}, 'then': {'preset': std.id}}]})
            text = (await c.get(f'{url}/text')).json()['text']
            assert 'preset: Standard' in text
            parsed = (await c.post(f'{url}/text', json={'text': text})).json()
            assert parsed['rules'][0]['then'] == {'preset': std.id, 'weighted': None}
            broken = await c.post(f'{url}/text', json={'text': 'rules:\n  - name: a\n   then: x\n'})
            assert broken.status_code == 400 and 'Line 3' in broken.text

    async def test_a_plain_member_is_refused(self, db, app):
        cup, *_ = await _cup()
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get(f'/api/admin/tournaments/{cup.id}/seed-rules')).status_code == 403
            assert (await c.put(f'/api/admin/tournaments/{cup.id}/seed-rules', json={'rules': []})).status_code == 403
