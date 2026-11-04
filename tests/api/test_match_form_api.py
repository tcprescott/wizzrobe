"""The match form over REST: staff build it, players answer, rules read it."""

from datetime import datetime, timedelta, timezone

from models import Match, MatchPlayers, Preset, Tournament
from tests.api._bracket_api import staff_token
from tests.api_helpers import client_for, create_user_token


async def _cup():
    open_ = await Preset.create(name='Open', randomizer='alttpr', settings={'mode': 'open'})
    hard = await Preset.create(name='Hard', randomizer='alttpr', settings={'mode': 'hard'})
    cup = await Tournament.create(name='Cup', seed_generator='alttpr', preset=open_)
    match = await Match.create(tournament=cup, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1))
    return cup, match, hard


class TestMatchForm:
    async def test_build_answer_and_rule(self, db, app):
        _, staff_raw = await staff_token()
        cup, match, hard = await _cup()
        p1, p1_raw = await create_user_token(username='p1')
        p2, p2_raw = await create_user_token(username='p2')
        for p in (p1, p2):
            await MatchPlayers.create(match=match, user=p)
        form_url = f'/api/admin/tournaments/{cup.id}/match-form'
        async with client_for(app, staff_raw) as c:
            saved = await c.put(form_url, json={'questions': [
                {'kind': 'yes_no', 'prompt': 'Play on hard?', 'visibility': 'never'},
            ]})
            assert saved.status_code == 200, saved.text
            key = saved.json()['questions'][0]['key']
            rules = await c.put(f'/api/admin/tournaments/{cup.id}/seed-rules', json={'rules': [
                {'name': 'Hard', 'when': {'answer': [{'q': key, 'who': 'every', 'is': ['yes']}]},
                 'then': {'preset': hard.id}},
            ]})
            assert rules.status_code == 200, rules.text
            assert rules.json()['questions'][0]['used_by'] == [1]
            assert rules.json()['rules'][0]['when']['answer'] == [{'q': key, 'who': 'every', 'is': ['yes']}]
            refused = await c.put(form_url, json={'questions': []})
            assert refused.status_code == 400 and 'seed rule 1' in refused.text

        async with client_for(app, p1_raw) as c:
            form = (await c.get(f'/api/matches/{match.id}/form')).json()
            assert [q['prompt'] for q in form['questions']] == ['Play on hard?'] and not form['locked']
            ok = await c.put(f'/api/matches/{match.id}/form/{key}', json={'value': 'yes'})
            assert ok.status_code == 200 and ok.json()['questions'][0]['answer'] == 'yes'
            bad = await c.put(f'/api/matches/{match.id}/form/{key}', json={'value': 'maybe'})
            assert bad.status_code == 400

        async with client_for(app, p2_raw) as c:
            theirs = (await c.get(f'/api/matches/{match.id}/form')).json()['questions'][0]
            assert theirs['answer'] is None and theirs['others'] == []

    async def test_outsiders_are_refused(self, db, app):
        cup, match, _ = await _cup()
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get(f'/api/admin/tournaments/{cup.id}/match-form')).status_code == 403
            assert (await c.get(f'/api/matches/{match.id}/form')).status_code == 403
