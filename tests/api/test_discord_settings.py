"""The app's Discord settings pane over REST: link status, role mappings, sync and the events mirror."""

import pytest

from application.services.discord.discord_service import DiscordService
from application.tenant_context import get_current_tenant_id
from models import DiscordRoleMapping, DiscordScheduledEvent, Match, Role, Tenant, Tournament
from tests.api._bracket_api import staff_token
from tests.api_helpers import client_for, create_user_token

GUILD = 1000000000000000001
ROLES = [
    {'id': 2000000000000000002, 'name': 'Proctors', 'color': 0x3498DB, 'mappable': True, 'position': 3},
    {'id': 2000000000000000020, 'name': 'Wizzrobe', 'color': 0, 'mappable': False, 'position': 2},
    {'id': 2000000000000000001, 'name': 'Admins', 'color': 0, 'mappable': True, 'position': 1},
]


@pytest.fixture
def discord(monkeypatch):
    async def summary(self, guild_id):
        return True, {'id': guild_id, 'name': 'SpeedGaming Live'}

    async def roles(self, guild_id):
        return True, [dict(r) for r in ROLES]

    async def no_problem(self, guild_id):
        return None

    monkeypatch.setattr(DiscordService, 'get_guild_summary', summary)
    monkeypatch.setattr(DiscordService, 'list_guild_roles', roles)
    monkeypatch.setattr(DiscordService, 'bot_event_problem', no_problem)


async def _link():
    await Tenant.filter(id=get_current_tenant_id()).update(discord_guild_id=GUILD)


async def test_unlinked_community(db, app, discord):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        body = (await c.get('/api/settings/discord')).json()
        assert body['linked'] is False and body['guild_id'] is None
        assert body['can_manage_roles'] is True and body['can_manage_events'] is True
        assert (await c.get('/api/settings/discord/mappings')).json() == []
        assert (await c.get('/api/settings/discord/mapping-options')).status_code == 400
        assert (await c.post('/api/settings/discord/disconnect')).status_code == 400


async def test_link_mappings_and_disconnect(db, app, discord):
    await _link()
    cup = await Tournament.create(name='ALTTPR Main')
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        body = (await c.get('/api/settings/discord')).json()
        assert body['linked'] is True and body['guild_id'] == str(GUILD)
        assert body['server_name'] == 'SpeedGaming Live' and body['reachable'] is True

        options = (await c.get('/api/settings/discord/mapping-options')).json()
        assert [r['name'] for r in options['discord_roles']] == ['Admins', 'Proctors']
        assert options['discord_roles'][1]['id'] == '2000000000000000002'
        kinds = {g['value']: g['kind'] for g in options['grants']}
        assert kinds['staff'] == 'community' and kinds['proctor'] == 'event'
        assert kinds['tournament_admin'] == 'tournament' and 'super_admin' not in kinds
        assert {'id': cup.id, 'name': 'ALTTPR Main'} in options['tournaments']

        no_cup = await c.post('/api/settings/discord/mappings', json={
            'discord_roles': [{'id': '2000000000000000001', 'name': 'Admins', 'color': 0}],
            'tournament_grants': ['tournament_admin'],
        })
        assert no_cup.status_code == 400

        r = await c.post('/api/settings/discord/mappings', json={
            'discord_roles': [
                {'id': '2000000000000000001', 'name': 'Admins', 'color': 0},
                {'id': '2000000000000000002', 'name': 'Proctors', 'color': 0},
            ],
            'app_roles': ['staff', 'proctor'],
        })
        assert r.status_code == 201 and r.json() == {'created': 4, 'skipped': 0}
        again = await c.post('/api/settings/discord/mappings', json={
            'discord_roles': [{'id': '2000000000000000001', 'name': 'Admins', 'color': 0}],
            'app_roles': ['staff'], 'tournament_grants': ['tournament_admin'], 'tournament_id': cup.id,
        })
        assert again.json() == {'created': 1, 'skipped': 1}

        rows = (await c.get('/api/settings/discord/mappings')).json()
        assert len(rows) == 5
        assert rows[0]['discord_role_id'] == '2000000000000000001'
        labels = {(r['discord_role_name'], r['grants'], r['applies_to']) for r in rows}
        assert ('Admins', 'Staff', 'Community-wide') in labels
        assert ('Admins', 'Tournament Admin', 'ALTTPR Main') in labels
        assert all(not r['grants_nothing'] for r in rows)
        assert await DiscordRoleMapping.filter(discord_role_id=2000000000000000002).count() == 2

        assert (await c.post('/api/settings/discord/disconnect')).status_code == 204
        assert (await c.get('/api/settings/discord')).json()['linked'] is False


async def test_sync_everyone_returns_summary(db, app, discord, monkeypatch):
    await _link()

    async def fake_sync(self, actor):
        return {'users_processed': 3, 'users_created': 1, 'granted': 2, 'revoked': 0, 'skipped': 0,
                'created': ['newbie'], 'changed': [{'name': 'ana', 'granted': ['staff'], 'revoked': []}]}

    from application.services import DiscordRoleMappingService
    monkeypatch.setattr(DiscordRoleMappingService, 'sync_all_users', fake_sync)
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        r = await c.post('/api/settings/discord/sync')
        assert r.status_code == 200
        assert r.json()['changed'] == [{'name': 'ana', 'granted': ['staff'], 'revoked': []}]


async def test_events_overview_counts_mirrored_matches(db, app, discord):
    await _link()
    on = await Tournament.create(name='ALTTPR Main', discord_events_enabled=True)
    await Tournament.create(name='Super Metroid')
    match = await Match.create(tournament=on, title='Final')
    await DiscordScheduledEvent.create(
        guild_id=GUILD, discord_event_id=1, source_type='match', source_id=match.id, title='Final',
    )
    _, raw = await create_user_token(username='syncer', roles=[Role.SYNC_ADMIN])
    async with client_for(app, raw) as c:
        link = (await c.get('/api/settings/discord')).json()
        assert link['can_manage_roles'] is False and link['can_manage_events'] is True
        body = (await c.get('/api/settings/discord/events')).json()
        assert body['problem'] is None
        rows = {t['name']: t for t in body['tournaments']}
        assert rows['ALTTPR Main']['enabled'] is True and rows['ALTTPR Main']['mirrored'] == 1
        assert rows['Super Metroid']['mirrored'] == 0
        assert (await c.get('/api/settings/discord/mappings')).status_code == 403


async def test_players_are_refused(db, app, discord):
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as c:
        assert (await c.get('/api/settings/discord')).status_code == 403
        assert (await c.get('/api/settings/discord/events')).status_code == 403
        assert (await c.post('/api/settings/discord/sync')).status_code == 403
