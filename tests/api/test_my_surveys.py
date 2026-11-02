"""REST tests for taking a survey (api/routers/my_surveys.py) and the builder routes (api/routers/survey_builder.py)."""

from datetime import datetime, timedelta, timezone

from application.services import SurveyService
from models import Match, MatchPlayers, Role, Survey, SurveyCommentTag, Tournament, User
from tests.api_helpers import client_for, create_user_token

NOW = datetime.now(timezone.utc)


async def _sent_to(player: User) -> Survey:
    staff, _ = await create_user_token(username='staff', roles=[Role.STAFF])
    match = await Match.create(tournament=await Tournament.create(name='ALTTPR'))
    await MatchPlayers.create(match=match, user=player)
    service = SurveyService()
    survey = await service.create_survey(staff, 'SGL26 survey')
    await service.send(staff, survey.id, closes_at=NOW + timedelta(days=7))
    return survey


async def test_a_player_opens_saves_and_sends(db, app, stub_discord_queue):
    player, raw = await create_user_token(username='alice')
    survey = await _sent_to(player)
    async with client_for(app, raw) as c:
        waiting = (await c.get('/api/me/surveys')).json()
        assert [s['id'] for s in waiting] == [survey.id]
        body = (await c.get(f'/api/me/surveys/{survey.id}')).json()
        assert body['open'] and not body['submitted']
        assert body['parts'] and body['seconds'] > 0
        part = body['parts'][0]
        assert (await c.post(f"/api/me/surveys/{survey.id}/parts/{part['key']}/open")).status_code == 204
        answers = {}
        for q in part['questions']:
            if q['kind'] in ('rating', 'nps'):
                answers[str(q['id'])] = 4
        saved = await c.put(f"/api/me/surveys/{survey.id}/parts/{part['key']}", json={'answers': answers})
        assert saved.status_code == 200, saved.text
        missing = saved.json()['missing']
        if missing:
            refused = await c.post(f'/api/me/surveys/{survey.id}/submit', json={})
            assert refused.status_code == 400
        else:
            sent = await c.post(f'/api/me/surveys/{survey.id}/submit', json={})
            assert sent.status_code == 200, sent.text
            assert sent.json()['submitted'] and not sent.json()['open']
            assert (await c.get('/api/me/surveys')).json() == []


async def test_someone_it_wasnt_sent_to_gets_404(db, app, stub_discord_queue):
    player, _ = await create_user_token(username='alice')
    survey = await _sent_to(player)
    _, other = await create_user_token(username='nosy')
    async with client_for(app, other) as c:
        assert (await c.get(f'/api/me/surveys/{survey.id}')).status_code == 404
        assert (await c.post(f'/api/me/surveys/{survey.id}/decline')).status_code == 404


async def test_decline(db, app, stub_discord_queue):
    player, raw = await create_user_token(username='alice')
    survey = await _sent_to(player)
    async with client_for(app, raw) as c:
        assert (await c.post(f'/api/me/surveys/{survey.id}/decline')).status_code == 204


async def test_staff_builder_preview_and_tags(db, app):
    _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    async with client_for(app, raw) as c:
        survey_id = (await c.post('/api/surveys', json={'title': 'S'})).json()['id']
        builder = await c.get(f'/api/surveys/{survey_id}/builder')
        assert builder.status_code == 200, builder.text
        assert builder.json()['target_seconds'] > 0
        paths = await c.get(f'/api/surveys/{survey_id}/preview-paths')
        assert paths.status_code == 200, paths.text
        assert paths.json()
        persona = paths.json()[0]['persona']
        preview = await c.get(f'/api/surveys/{survey_id}/preview', params={'persona': persona})
        assert preview.status_code == 200, preview.text
        assert preview.json()['open'] is False
        everything = (await c.get(f'/api/surveys/{survey_id}/preview')).json()
        assert everything['parts']

        tag = await c.post(f'/api/surveys/{survey_id}/comment-tags', json={'name': 'Parking'})
        assert tag.status_code == 200, tag.text
        assert (await c.post(f'/api/surveys/{survey_id}/comment-tags', json={'name': 'parking'})).status_code == 400
        gone = await c.delete(f"/api/surveys/{survey_id}/comment-tags/{tag.json()['id']}")
        assert gone.status_code == 204
        assert not await SurveyCommentTag.filter(id=tag.json()['id']).exists()

        detail = (await c.get(f'/api/surveys/{survey_id}')).json()
        first, second = detail['sections'][0], detail['sections'][1]
        q = first['questions'][-1]
        placed = await c.post(f"/api/surveys/questions/{q['id']}/place",
                              json={'section_id': second['id'], 'index': 0})
        assert placed.status_code == 204, placed.text
        after = (await c.get(f'/api/surveys/{survey_id}')).json()
        assert after['sections'][1]['questions'][0]['id'] == q['id']


async def test_players_cant_reach_the_builder(db, app):
    staff, _ = await create_user_token(username='staff', roles=[Role.STAFF])
    survey = await SurveyService().create_survey(staff, 'S')
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as c:
        assert (await c.get(f'/api/surveys/{survey.id}/builder')).status_code == 403
        assert (await c.post(f'/api/surveys/{survey.id}/comment-tags', json={'name': 'X'})).status_code == 403
