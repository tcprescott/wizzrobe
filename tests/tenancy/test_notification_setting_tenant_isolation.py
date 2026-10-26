"""NotificationSetting overrides stay inside their community."""

from application.services.notification_catalog import MATCH_CONFIRMED, WATCHERS
from application.services.notification_settings_service import NotificationSettingsService
from application.tenant_context import tenant_scope
from models import NotificationMode, NotificationSetting, Role, UserRole
from tests.factories import make_user


async def test_overrides_do_not_leak_between_tenants(two_tenants):
    a, b = two_tenants
    staff = await make_user(discord_id=860, username='leak_notify_staff')
    for t in (a, b):
        await UserRole.create(user=staff, role=Role.STAFF, tenant=t)
    target = MATCH_CONFIRMED.to(WATCHERS)

    with tenant_scope(a.id):
        await NotificationSettingsService.set_mode(staff, target.key, WATCHERS, NotificationMode.OFF)
        assert await NotificationSettingsService.mode_for(target) is NotificationMode.OFF

    with tenant_scope(b.id):
        assert await NotificationSettingsService.mode_for(target) is NotificationMode.SILENT
        rows = await NotificationSettingsService.list_settings(staff)
        assert not any(r.changed for r in rows)
        assert await NotificationSettingsService.reset_all(staff) == 0

    with tenant_scope(a.id):
        assert await NotificationSettingsService.mode_for(target) is NotificationMode.OFF

    assert await NotificationSetting.filter(tenant_id=a.id).count() == 1
    assert await NotificationSetting.filter(tenant_id=b.id).count() == 0
