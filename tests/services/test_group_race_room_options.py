"""What the racetime settings forms can pick: bots, profiles, Discord channels and roles."""

import pytest

from application.services.discord.discord_service import DiscordService
from application.services.group_race_service import GroupRaceService
from models import RacetimeBot, RacetimeBotTenant, Role, Tenant, User, UserRole
from tests.conftest import DEFAULT_TEST_TENANT_ID

pytestmark = pytest.mark.anyio


@pytest.fixture
def discord(monkeypatch):
    state = {'channels': (True, [{'id': 5, 'name': 'daily-races'}]), 'roles': (True, [
        {'id': 1, 'name': '@everyone'}, {'id': 9, 'name': 'racers'}, {'id': 8, 'name': 'Admins'},
    ])}

    async def channels(self, guild_id):
        return state['channels']

    async def roles(self, guild_id):
        return state['roles']

    monkeypatch.setattr(DiscordService, 'list_text_channels', channels)
    monkeypatch.setattr(DiscordService, 'list_guild_roles', roles)
    return state


async def _staff() -> User:
    staff = await User.create(discord_id=950001, username='rstaff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return staff


async def test_staff_get_bots_channels_and_roles(db, discord):
    await Tenant.filter(id=DEFAULT_TEST_TENANT_ID).update(discord_guild_id=111)
    bot = await RacetimeBot.create(category='alttpr', client_id='c', client_secret='s', name='A')
    await RacetimeBotTenant.create(bot=bot, tenant_id=DEFAULT_TEST_TENANT_ID, is_active=True)
    await RacetimeBot.create(category='smz3', client_id='c2', client_secret='s2', name='Not ours')

    options = await GroupRaceService().room_options(await _staff())

    assert [b.category for b in options.bots] == ['alttpr']
    assert options.channels == [{'id': '5', 'name': 'daily-races'}]
    assert options.roles == [{'id': '8', 'name': 'Admins'}, {'id': '9', 'name': 'racers'}]
    assert options.discord_problem is None


async def test_no_discord_server_still_answers(db, discord):
    await Tenant.filter(id=DEFAULT_TEST_TENANT_ID).update(discord_guild_id=None)

    options = await GroupRaceService().room_options(await _staff())

    assert options.channels == [] and options.roles == []
    assert options.discord_problem == 'No Discord server is connected.'


async def test_discord_down_says_why(db, discord):
    await Tenant.filter(id=DEFAULT_TEST_TENANT_ID).update(discord_guild_id=111)
    discord['channels'] = (False, 'Discord is unreachable.')

    options = await GroupRaceService().room_options(await _staff())

    assert options.channels == [] and options.discord_problem == 'Discord is unreachable.'


async def test_players_cannot_list_options(db, discord):
    player = await User.create(discord_id=950002, username='player')
    with pytest.raises(PermissionError):
        await GroupRaceService().room_options(player)
