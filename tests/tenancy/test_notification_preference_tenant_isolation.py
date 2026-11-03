"""A person's own notification choices stay inside the community they made them in."""

from application.services.notification_catalog import MATCH_SCHEDULED, WATCHERS
from application.services.notification_preference_service import NotificationPreferenceService
from application.tenant_context import tenant_scope
from models import NotificationMode, NotificationPreference, NotificationPreset
from tests.factories import make_user


async def test_choices_do_not_leak_between_tenants(two_tenants):
    a, b = two_tenants
    player = await make_user(discord_id=861, username='leak_pref_player')
    target = MATCH_SCHEDULED.to(WATCHERS)

    with tenant_scope(a.id):
        await NotificationPreferenceService.set_preset(player, NotificationPreset.QUIET)
        await NotificationPreferenceService.set_mode(player, target.key, WATCHERS, NotificationMode.SILENT)
        assert await NotificationPreferenceService.mode_for(861, target) is NotificationMode.SILENT

    with tenant_scope(b.id):
        mine = await NotificationPreferenceService.get_mine(player)
        assert mine.preset is NotificationPreset.EVERYTHING and mine.changed == 0
        assert await NotificationPreferenceService.mode_for(861, target) is NotificationMode.PING

    assert await NotificationPreference.filter(tenant_id=a.id).count() == 1
    assert await NotificationPreference.filter(tenant_id=b.id).count() == 0
