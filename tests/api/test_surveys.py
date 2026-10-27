"""REST API tests for post-event surveys (api/routers/surveys.py).

Staff build surveys and read results here; sending stays in the app, and only
drafts can be deleted over REST.
"""

from models import FeatureFlag, Role, Survey, SurveyStatus, TenantFeatureFlag
from tests.api_helpers import client_for, create_user_token


async def _staff():
    return await create_user_token(username='staff', roles=[Role.STAFF])


async def test_staff_build_a_survey_end_to_end(db, app):
    _, raw = await _staff()
    async with client_for(app, raw) as c:
        resp = await c.post('/api/surveys', json={'title': 'How was it?', 'from_template': False})
        assert resp.status_code == 201, resp.text
        survey_id = resp.json()['id']
        section = (await c.post(f'/api/surveys/{survey_id}/sections',
                                json={'title': 'Your tournaments', 'audience': 'each_tournament'})).json()
        q1 = await c.post(f"/api/surveys/sections/{section['id']}/questions",
                          json={'prompt': 'Room?', 'kind': 'rating', 'condition': 'tournament_room'})
        assert q1.status_code == 201, q1.text
        q2 = await c.post(f"/api/surveys/sections/{section['id']}/questions",
                          json={'prompt': 'Back?', 'kind': 'choice_one', 'options': ['Yes', 'No']})
        q3 = await c.post(f"/api/surveys/sections/{section['id']}/questions",
                          json={'prompt': 'Why not?', 'kind': 'comment', 'condition': 'answer',
                                'condition_args': {'question_id': q2.json()['id'], 'values': ['No']}})
        assert q3.status_code == 201, q3.text
        moved = await c.post(f"/api/surveys/questions/{q2.json()['id']}/move", json={'step': -1})
        assert moved.status_code == 204
        body = (await c.get(f'/api/surveys/{survey_id}')).json()
        assert [q['prompt'] for q in body['sections'][0]['questions']] == ['Back?', 'Room?', 'Why not?']
        listed = (await c.get('/api/surveys')).json()
        assert [s['id'] for s in listed] == [survey_id]
        results = (await c.get(f'/api/surveys/{survey_id}/results')).json()
        assert results['responses'] == 0
        keys = {c['key'] for c in (await c.get('/api/surveys/conditions')).json()}
        assert {'on_stage', 'answer'} <= keys


async def test_bad_shapes_are_400(db, app):
    _, raw = await _staff()
    async with client_for(app, raw) as c:
        survey_id = (await c.post('/api/surveys', json={'title': 'S', 'from_template': False})).json()['id']
        section = (await c.post(f'/api/surveys/{survey_id}/sections', json={'title': 'A'})).json()
        one_choice = await c.post(f"/api/surveys/sections/{section['id']}/questions",
                                  json={'prompt': 'Pick', 'kind': 'choice_one', 'options': ['Only']})
        assert one_choice.status_code == 400
        unknown = await c.post(f"/api/surveys/sections/{section['id']}/questions",
                               json={'prompt': 'Q', 'kind': 'rating', 'condition': 'made_up'})
        assert unknown.status_code == 400


async def test_a_sent_survey_cant_be_deleted_or_changed_here(db, app):
    _, raw = await _staff()
    sent = await Survey.create(title='Sent', status=SurveyStatus.OPEN)
    async with client_for(app, raw) as c:
        assert (await c.delete(f'/api/surveys/{sent.id}')).status_code == 400
        assert (await c.post(f'/api/surveys/{sent.id}/sections', json={'title': 'Late'})).status_code == 400
        draft = (await c.post('/api/surveys', json={'title': 'Draft'})).json()['id']
        assert (await c.delete(f'/api/surveys/{draft}')).status_code == 204


async def test_players_are_refused(db, app):
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as c:
        assert (await c.get('/api/surveys')).status_code == 403
        assert (await c.post('/api/surveys', json={'title': 'Mine'})).status_code == 403


async def test_feature_off_is_404(db, app):
    await TenantFeatureFlag.filter(tenant_id=1, flag=FeatureFlag.SURVEYS.value).update(available=False, enabled=False)
    _, raw = await _staff()
    async with client_for(app, raw) as c:
        assert (await c.get('/api/surveys')).status_code == 404
