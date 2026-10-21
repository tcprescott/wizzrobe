"""Profile → Notifications: a person's preset and per-message choices, under the community's ceiling."""

import pytest

from application.services.discord.discord_service import DM_NOTICE_OFF, MockDiscordService
from application.services.notification_catalog import (
    FOLLOWERS,
    MATCH_FINISHED,
    MATCH_SCHEDULED,
    MATCHUP_READY,
    ON_DUTY,
    PLAYER_COMMENT,
    PLAYERS,
    STAFF_COMMENT,
    STAGE_CALL,
    WATCHERS,
)
from application.services.notification_preference_service import (
    NotificationPreferenceService,
    preset_mode,
)
from application.services.notification_settings_service import NotificationSettingsService
from models import NotificationMode, NotificationPreference, NotificationPreset, Role, User, UserRole
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

OFF, SILENT, PING = NotificationMode.OFF, NotificationMode.SILENT, NotificationMode.PING
pytestmark = pytest.mark.usefixtures("db")


def test_presets():
    assert preset_mode(NotificationPreset.EVERYTHING, MATCH_SCHEDULED.to(WATCHERS)) is PING
    assert preset_mode(NotificationPreset.NEEDS_ME, MATCH_SCHEDULED.to(PLAYERS)) is PING
    assert preset_mode(NotificationPreset.NEEDS_ME, MATCH_FINISHED.to(PLAYERS)) is SILENT
    assert preset_mode(NotificationPreset.QUIET, STAGE_CALL.to(PLAYERS)) is PING
    assert preset_mode(NotificationPreset.QUIET, MATCH_SCHEDULED.to(PLAYERS)) is SILENT
    assert preset_mode(NotificationPreset.QUIET, MATCH_SCHEDULED.to(FOLLOWERS)) is OFF


async def test_no_row_means_the_community_decides():
    player = await make_user(discord_id=8101, username='p1')
    mine = await NotificationPreferenceService.get_mine(player)
    assert mine.preset is NotificationPreset.EVERYTHING
    assert mine.changed == 0
    row = next(r for r in mine.rows if r.target == MATCHUP_READY.to(PLAYERS))
    assert (row.community, row.mine, row.effective) == (SILENT, PING, SILENT)
    assert await NotificationPreferenceService.mode_for(8101, MATCH_SCHEDULED.to(PLAYERS)) is PING


async def test_staff_alerts_are_listed_only_for_the_admin_side():
    player = await make_user(discord_id=8102, username='p2')
    staff = await make_user(discord_id=8103, username='s1')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    assert 'Staff alerts' not in {r.section for r in (await NotificationPreferenceService.get_mine(player)).rows}
    assert 'Staff alerts' in {r.section for r in (await NotificationPreferenceService.get_mine(staff)).rows}


async def test_a_preset_and_an_override_reach_send_dm(capsys):
    player = await make_user(discord_id=8104, username='p3')
    await NotificationPreferenceService.set_preset(player, NotificationPreset.QUIET)
    assert await MockDiscordService().send_dm(8104, 'x', notice=MATCH_SCHEDULED.to(FOLLOWERS)) == (True, DM_NOTICE_OFF)
    await MockDiscordService().send_dm(8104, 'scheduled', notice=MATCH_SCHEDULED.to(PLAYERS))
    assert 'scheduled [silent]' in capsys.readouterr().out

    row = await NotificationPreferenceService.set_mode(player, MATCH_SCHEDULED.key, 'players', 'ping')
    assert row.changed and row.effective is PING
    await MockDiscordService().send_dm(8104, 'scheduled again', notice=MATCH_SCHEDULED.to(PLAYERS))
    out = capsys.readouterr().out
    assert 'scheduled again' in out and '[silent]' not in out


async def test_the_community_is_a_ceiling():
    staff = await make_user(discord_id=8105, username='s2')
    await UserRole.create(user=staff, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    player = await make_user(discord_id=8106, username='p4')
    await NotificationSettingsService.set_mode(staff, MATCH_SCHEDULED.key, PLAYERS, SILENT)
    row = await NotificationPreferenceService.set_mode(player, MATCH_SCHEDULED.key, PLAYERS, PING)
    assert row.effective is SILENT


async def test_choosing_the_preset_value_clears_the_override_and_reset_keeps_the_preset():
    player = await make_user(discord_id=8107, username='p5')
    await NotificationPreferenceService.set_preset(player, NotificationPreset.NEEDS_ME)
    await NotificationPreferenceService.set_mode(player, MATCH_FINISHED.key, PLAYERS, OFF)
    await NotificationPreferenceService.set_mode(player, MATCH_SCHEDULED.key, PLAYERS, PING)
    pref = await NotificationPreference.get(user_id=player.id)
    assert pref.overrides == {'match_finished:players': 'off'}
    mine = await NotificationPreferenceService.reset(player)
    assert mine.preset is NotificationPreset.NEEDS_ME and mine.changed == 0


async def test_staff_comments_stay_in_step_with_the_old_switch():
    player = await make_user(discord_id=8108, username='p6')
    await NotificationPreferenceService.set_mode(player, STAFF_COMMENT.key, PLAYERS, OFF)
    assert (await User.get(id=player.id)).dm_match_comments is False
    await NotificationPreferenceService.set_mode(player, STAFF_COMMENT.key, PLAYERS, None)
    assert (await User.get(id=player.id)).dm_match_comments is True


async def test_bad_input_is_refused():
    player = await make_user(discord_id=8109, username='p7')
    with pytest.raises(ValueError):
        await NotificationPreferenceService.set_mode(player, 'nope', PLAYERS, OFF)
    with pytest.raises(ValueError):
        await NotificationPreferenceService.set_preset(player, 'loud')
    with pytest.raises(PermissionError):
        await NotificationPreferenceService.get_mine(None)


async def test_a_player_cannot_set_a_staff_alert():
    # It used to save the override and then crash looking for a row get_mine
    # never lists for them, so REST and MCP answered 500.
    player = await make_user(discord_id=8110, username='p8')
    with pytest.raises(ValueError, match='staff alerts'):
        await NotificationPreferenceService.set_mode(player, PLAYER_COMMENT.key, ON_DUTY, OFF)
    assert await NotificationPreference.filter(user=player).count() == 0


async def test_choices_are_per_community():
    from application.tenant_context import tenant_scope
    from models import Tenant

    player = await make_user(discord_id=8110, username='p8')
    other = await Tenant.create(slug='other-pref', name='Other')
    await NotificationPreferenceService.set_preset(player, NotificationPreset.QUIET)
    with tenant_scope(other.id):
        assert (await NotificationPreferenceService.get_mine(player)).preset is NotificationPreset.EVERYTHING
        assert await NotificationPreferenceService.mode_for(8110, MATCH_SCHEDULED.to(FOLLOWERS)) is PING
