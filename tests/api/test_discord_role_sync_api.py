"""The app's Discord Roles from Wizzrobe pane over REST: the checklist, setting a line, sync and clear."""

from application.services.feature_flag_service import reset_flag_cache
from models import AuditLog, DiscordRoleRule, FeatureFlag, Role, TenantFeatureFlag, Tournament
from tests.api._bracket_api import staff_token
from tests.api_helpers import client_for, create_user_token
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.services.test_discord_role_sync_service import ABOVE_BOT, COMPETITOR, discord  # noqa: F401

URL = '/api/event-settings/discord-roles'


async def test_checklist_lists_every_line_and_role(db, app, discord):  # noqa: F811
    await Tournament.create(name='ALTTPR Main', is_active=True)
    await Tournament.create(name='Old Cup', is_active=False)
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        body = (await c.get(URL)).json()
    assert body['last_synced_at'] is None and body['roles_error'] is None
    roles = {r['name']: r for r in body['roles']}
    assert roles['Competitor']['id'] == str(COMPETITOR) and roles['Competitor']['problem'] is None
    assert 'above the bot' in roles['Admins']['problem']
    assert 'Server Booster' not in roles
    labels = [(line['group'], line['label']) for line in body['lines']]
    assert labels[:2] == [('players', 'Enrolled in any tournament'), ('players', 'In a match right now')]
    assert ('tournaments', 'ALTTPR Main') in labels and ('tournaments', 'Old Cup') not in labels
    assert all(line['discord_role_id'] is None for line in body['lines'])


async def test_set_and_clear_a_line(db, app, discord):  # noqa: F811
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        r = await c.put(f'{URL}/rules', json={'kind': 'any_tournament', 'discord_role_id': str(COMPETITOR)})
        assert r.status_code == 204
        line = (await c.get(URL)).json()['lines'][0]
        assert line['discord_role_id'] == str(COMPETITOR) and line['discord_role_name'] == 'Competitor'

        refused = await c.put(f'{URL}/rules', json={'kind': 'live_match', 'discord_role_id': str(ABOVE_BOT)})
        assert refused.status_code == 400 and 'above the bot' in refused.json()['detail']

        r = await c.put(f'{URL}/rules', json={'kind': 'any_tournament', 'discord_role_id': None})
        assert r.status_code == 204
    assert await DiscordRoleRule.all().count() == 0
    assert await AuditLog.filter(action='discord_role.rule_cleared').count() == 1


async def test_sync_and_clear(db, app, discord):  # noqa: F811
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        await c.put(f'{URL}/rules', json={'kind': 'any_tournament', 'discord_role_id': str(COMPETITOR)})
        synced = await c.post(f'{URL}/sync')
        assert synced.status_code == 200 and synced.json()['error'] is None
        assert (await c.get(URL)).json()['last_synced_at'] is not None
        cleared = await c.post(f'{URL}/clear')
        assert cleared.status_code == 200
    assert await DiscordRoleRule.all().count() == 0


async def test_staff_only_and_feature_gated(db, app, discord):  # noqa: F811
    _, proctor = await create_user_token(username='proctor', roles=[Role.PROCTOR])
    async with client_for(app, proctor) as c:
        assert (await c.get(URL)).status_code == 403
    await TenantFeatureFlag.filter(
        tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.DISCORD_ROLE_SYNC.value,
    ).update(enabled=False)
    reset_flag_cache()
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        assert (await c.get(URL)).status_code == 404
