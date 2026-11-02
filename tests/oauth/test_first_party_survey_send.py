"""Send, remind and close a survey: Wizzrobe's own app only (Thomas, 2026-11-02).

A staff member's personal token or a third-party app gets a 403; the first-party
app's grant goes through.
"""

import pytest

from application.services import OAuthAppService, OAuthGrantService, SurveyService
from models import Match, MatchPlayers, OAuthApp, Role, Survey, SurveyStatus, Tenant, Tournament, User, UserRole
from tests.api_helpers import client_for, create_community_member, create_user_token
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.oauth.conftest import client, exchange, pkce, query, start

pytestmark = pytest.mark.usefixtures('db')

APP_REDIRECT = 'http://localhost:5173/auth/callback'
HEADER = 'X-Wizzrobe-Community'


async def _app_token(user: User) -> str:
    admin, _ = await create_user_token(username='platform', roles=[Role.SUPER_ADMIN])
    app: OAuthApp = await OAuthAppService().register_first_party(
        admin, client_id='wizzrobe-app', redirect_uris=[APP_REDIRECT],
    )
    verifier, challenge = pkce()
    async with client() as http:
        txn = await start(http, app, challenge, redirect_uri=APP_REDIRECT)
        redirect = await OAuthGrantService().approve_first_party(txn, user)
        assert redirect is not None
        resp = await exchange(http, app, None, query(redirect)['code'], verifier, redirect_uri=APP_REDIRECT)
    assert resp.status_code == 200, resp.text
    return resp.json()['access_token']


async def _draft(staff: User) -> Survey:
    player = await create_community_member(username='alice')
    match = await Match.create(tournament=await Tournament.create(name='ALTTPR'))
    await MatchPlayers.create(match=match, user=player)
    return await SurveyService().create_survey(staff, 'SGL26 survey')


async def test_a_personal_token_cannot_send(app, stub_discord_queue):
    staff, raw = await create_user_token(username='staff', roles=[Role.STAFF])
    survey = await _draft(staff)
    async with client_for(app, raw) as c:
        refused = await c.post(f'/api/surveys/{survey.id}/send', json={})
        assert refused.status_code == 403
        assert 'Wizzrobe app' in refused.json()['detail']
        assert (await c.post(f'/api/surveys/{survey.id}/close')).status_code == 403
    assert (await Survey.get(id=survey.id)).status is SurveyStatus.DRAFT


async def test_the_app_sends_reminds_and_closes(stub_discord_queue):
    staff = await create_community_member(username='staff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    survey = await _draft(staff)
    token = await _app_token(staff)
    slug = (await Tenant.get(id=DEFAULT_TEST_TENANT_ID)).slug
    async with client(token) as http:
        sent = await http.post(f'/api/surveys/{survey.id}/send', json={}, headers={HEADER: slug})
        assert sent.status_code == 200, sent.text
        assert sent.json()['people'] >= 1
        listed = (await http.get('/api/surveys', headers={HEADER: slug})).json()
        assert listed[0]['status'] == 'open' and listed[0]['reminders_left'] == 2
        reminded = await http.post(f'/api/surveys/{survey.id}/remind', headers={HEADER: slug})
        assert reminded.status_code == 200, reminded.text
        again = await http.post(f'/api/surveys/{survey.id}/remind', headers={HEADER: slug})
        assert again.status_code == 400
        closed = await http.post(f'/api/surveys/{survey.id}/close', headers={HEADER: slug})
        assert closed.status_code == 200, closed.text
        assert closed.json()['status'] == 'closed'
