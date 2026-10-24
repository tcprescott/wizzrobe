"""Tests for TimezoneService — the community timezone policy and its resolution.

Pure normalization/resolution tests need no DB; the persistence, staff-gate, and
per-tenant isolation tests run against real ``Tenant`` rows via the
``two_tenants`` fixture. ``Tenant`` is the tenancy discriminator (no ``tenant``
FK of its own), so this is not a repository-leak case — but one community's
timezone must never bleed into another's, which the isolation test asserts.
"""

from types import SimpleNamespace

import pytest

from application.services.timezone_service import (
    CONFIG_KEY,
    DEFAULT_MODE,
    MODE_PINNED,
    MODE_USER,
    TimezoneService,
)
from application.tenant_context import tenant_scope
from application.timezone_context import FALLBACK_TIMEZONE
from models import Role, UserRole
from tests.factories import make_user

BERLIN = 'Europe/Berlin'
TOKYO = 'Asia/Tokyo'
DENVER = 'America/Denver'


# ---------------------------------------------------------------------------
# _clean — pure, and deliberately total
# ---------------------------------------------------------------------------


class TestClean:
    def test_none_returns_defaults(self):
        assert TimezoneService._clean(None) == {
            'mode': DEFAULT_MODE, 'name': FALLBACK_TIMEZONE,
        }

    def test_empty_returns_defaults(self):
        assert TimezoneService._clean({})['mode'] == DEFAULT_MODE

    def test_valid_blob_survives(self):
        assert TimezoneService._clean({'mode': MODE_PINNED, 'name': BERLIN}) == {
            'mode': MODE_PINNED, 'name': BERLIN,
        }

    def test_unknown_mode_falls_back(self):
        assert TimezoneService._clean(
            {'mode': 'sideways', 'name': BERLIN}
        )['mode'] == DEFAULT_MODE

    def test_unloadable_zone_falls_back(self):
        # A hand-edited or stale config blob must not be able to break rendering.
        assert TimezoneService._clean(
            {'mode': MODE_PINNED, 'name': 'Mars/Olympus_Mons'}
        )['name'] == FALLBACK_TIMEZONE

    def test_non_dict_falls_back(self):
        assert TimezoneService._clean('nonsense')['mode'] == DEFAULT_MODE

    def test_missing_name_falls_back(self):
        assert TimezoneService._clean({'mode': MODE_PINNED})['name'] == FALLBACK_TIMEZONE


# ---------------------------------------------------------------------------
# pick — pure resolution
# ---------------------------------------------------------------------------


class TestPick:
    def test_pinned_ignores_everything_else(self):
        settings = {'mode': MODE_PINNED, 'name': BERLIN}
        viewer = SimpleNamespace(timezone=TOKYO)
        assert TimezoneService.pick(settings, viewer, DENVER) == BERLIN

    def test_user_preference_beats_browser(self):
        settings = {'mode': MODE_USER, 'name': BERLIN}
        viewer = SimpleNamespace(timezone=TOKYO)
        assert TimezoneService.pick(settings, viewer, DENVER) == TOKYO

    def test_browser_used_when_user_has_no_preference(self):
        settings = {'mode': MODE_USER, 'name': BERLIN}
        viewer = SimpleNamespace(timezone=None)
        assert TimezoneService.pick(settings, viewer, DENVER) == DENVER

    def test_tenant_default_when_neither(self):
        settings = {'mode': MODE_USER, 'name': BERLIN}
        viewer = SimpleNamespace(timezone=None)
        assert TimezoneService.pick(settings, viewer, None) == BERLIN

    def test_anonymous_viewer_uses_browser(self):
        settings = {'mode': MODE_USER, 'name': BERLIN}
        assert TimezoneService.pick(settings, None, TOKYO) == TOKYO

    def test_anonymous_viewer_with_no_browser_uses_tenant_default(self):
        settings = {'mode': MODE_USER, 'name': BERLIN}
        assert TimezoneService.pick(settings, None, None) == BERLIN

    def test_invalid_user_preference_is_skipped(self):
        settings = {'mode': MODE_USER, 'name': BERLIN}
        viewer = SimpleNamespace(timezone='Mars/Olympus_Mons')
        assert TimezoneService.pick(settings, viewer, TOKYO) == TOKYO

    def test_invalid_browser_zone_is_skipped(self):
        # The cookie is client-supplied, so a forged value must not stick.
        settings = {'mode': MODE_USER, 'name': BERLIN}
        viewer = SimpleNamespace(timezone=None)
        assert TimezoneService.pick(settings, viewer, 'Mars/Olympus_Mons') == BERLIN


# ---------------------------------------------------------------------------
# Persistence, gating, isolation — DB
# ---------------------------------------------------------------------------


@pytest.fixture
async def tenants_with_staff(two_tenants):
    a, b = two_tenants
    staff = await make_user(discord_id=820, username='tz_staff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant=a)
    await UserRole.create(user=staff, role=Role.STAFF, tenant=b)
    return a, b, staff


async def test_get_settings_returns_defaults_when_unset(two_tenants):
    a, _ = two_tenants
    assert await TimezoneService.get_settings(a.id) == {
        'mode': DEFAULT_MODE, 'name': FALLBACK_TIMEZONE,
    }


async def test_set_settings_persists_and_reads_back(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        result = await TimezoneService.set_settings(staff, MODE_PINNED, BERLIN)
    assert result == {'mode': MODE_PINNED, 'name': BERLIN}
    assert await TimezoneService.get_settings(a.id) == {'mode': MODE_PINNED, 'name': BERLIN}


async def test_set_settings_stores_under_its_own_config_key(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TimezoneService.set_settings(staff, MODE_PINNED, BERLIN)
    await a.refresh_from_db()
    assert a.config[CONFIG_KEY] == {'mode': MODE_PINNED, 'name': BERLIN}


async def test_set_settings_preserves_other_config_keys(tenants_with_staff):
    a, _, staff = tenants_with_staff
    a.config = {'theme': {'primary': '#0e7470'}}
    await a.save()
    with tenant_scope(a.id):
        await TimezoneService.set_settings(staff, MODE_USER, TOKYO)
    await a.refresh_from_db()
    assert a.config['theme'] == {'primary': '#0e7470'}


async def test_set_settings_rejects_unknown_mode(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        with pytest.raises(ValueError, match='pinned'):
            await TimezoneService.set_settings(staff, 'sideways', BERLIN)


async def test_set_settings_rejects_unknown_zone(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        with pytest.raises(ValueError, match='not a recognised timezone'):
            await TimezoneService.set_settings(staff, MODE_PINNED, 'Mars/Olympus_Mons')


async def test_set_settings_requires_staff(two_tenants):
    a, _ = two_tenants
    nobody = await make_user(discord_id=821, username='tz_nobody')
    with tenant_scope(a.id):
        with pytest.raises(PermissionError):
            await TimezoneService.set_settings(nobody, MODE_PINNED, BERLIN)


async def test_settings_do_not_leak_between_tenants(tenants_with_staff):
    a, b, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TimezoneService.set_settings(staff, MODE_PINNED, BERLIN)
    assert (await TimezoneService.get_settings(b.id))['name'] == FALLBACK_TIMEZONE


async def test_tenant_timezone_name_is_the_configured_zone(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TimezoneService.set_settings(staff, MODE_USER, TOKYO)
    # Even in user mode: this is the community's own clock, which is what
    # shared/cached output and tenant-anchored rules render on.
    assert await TimezoneService.tenant_timezone_name(a.id) == TOKYO


async def test_tenant_timezone_name_falls_back_with_no_tenant():
    assert await TimezoneService.tenant_timezone_name(None) == FALLBACK_TIMEZONE


async def test_resolve_honours_pin_over_user(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TimezoneService.set_settings(staff, MODE_PINNED, BERLIN)
    resolved = await TimezoneService.resolve(
        user=SimpleNamespace(timezone=TOKYO), browser_timezone=DENVER, tenant_id=a.id,
    )
    assert resolved == BERLIN


async def test_resolve_follows_user_when_not_pinned(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TimezoneService.set_settings(staff, MODE_USER, BERLIN)
    resolved = await TimezoneService.resolve(
        user=SimpleNamespace(timezone=TOKYO), browser_timezone=DENVER, tenant_id=a.id,
    )
    assert resolved == TOKYO


async def test_set_user_timezone_persists(two_tenants):
    a, _ = two_tenants
    user = await make_user(discord_id=822, username='tz_user')
    with tenant_scope(a.id):
        assert await TimezoneService.set_user_timezone(user, TOKYO) == TOKYO
    await user.refresh_from_db()
    assert user.timezone == TOKYO


async def test_set_user_timezone_clears_with_none(two_tenants):
    a, _ = two_tenants
    user = await make_user(discord_id=823, username='tz_user2', timezone=TOKYO)
    with tenant_scope(a.id):
        assert await TimezoneService.set_user_timezone(user, None) is None
    await user.refresh_from_db()
    assert user.timezone is None


async def test_set_user_timezone_treats_blank_as_clear(two_tenants):
    a, _ = two_tenants
    user = await make_user(discord_id=824, username='tz_user3', timezone=TOKYO)
    with tenant_scope(a.id):
        await TimezoneService.set_user_timezone(user, '   ')
    await user.refresh_from_db()
    assert user.timezone is None


async def test_set_user_timezone_rejects_unknown_zone(two_tenants):
    a, _ = two_tenants
    user = await make_user(discord_id=825, username='tz_user4')
    with tenant_scope(a.id):
        with pytest.raises(ValueError, match='not a recognised timezone'):
            await TimezoneService.set_user_timezone(user, 'Mars/Olympus_Mons')


async def test_set_user_hour_cycle_persists_and_clears(two_tenants):
    a, _ = two_tenants
    user = await make_user(discord_id=826, username='hc_user')
    with tenant_scope(a.id):
        assert await TimezoneService.set_user_hour_cycle(user, '12') == '12'
        await user.refresh_from_db()
        assert user.hour_cycle == '12'
        assert await TimezoneService.set_user_hour_cycle(user, '') is None
    await user.refresh_from_db()
    assert user.hour_cycle is None


async def test_set_user_hour_cycle_rejects_anything_else(two_tenants):
    a, _ = two_tenants
    user = await make_user(discord_id=827, username='hc_user2')
    with tenant_scope(a.id):
        with pytest.raises(ValueError, match='12-hour'):
            await TimezoneService.set_user_hour_cycle(user, 'h23')
