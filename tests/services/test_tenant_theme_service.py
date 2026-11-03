"""Tests for TenantThemeService — the per-tenant brand palette.

Pure-merge/validation tests need no DB; the persistence, reset, staff-gate, and
per-tenant isolation tests run against the real ``Tenant`` rows via the
``two_tenants`` fixture. ``Tenant`` is the tenancy discriminator (no ``tenant``
FK of its own), so this is not a repository-leak case — but a tenant's colours
must still never bleed into another's, which the isolation test asserts.
"""

import pytest

from application.services.tenant_theme_service import (
    CONFIG_KEY,
    DEFAULT_THEME,
    TenantThemeService,
)
from application.tenant_context import tenant_scope
from application.utils.color_contrast import contrast_ratio
from models import Role, User, UserRole


class TestMerge:
    def test_none_returns_defaults(self):
        assert TenantThemeService._merge(None) == DEFAULT_THEME

    def test_empty_returns_defaults(self):
        assert TenantThemeService._merge({}) == DEFAULT_THEME

    def test_partial_override_layers_on_defaults(self):
        merged = TenantThemeService._merge({'primary': '#123456'})
        assert merged['primary'] == '#123456'
        assert merged['accent'] == DEFAULT_THEME['accent']

    def test_invalid_hex_is_ignored(self):
        merged = TenantThemeService._merge({'primary': 'red', 'accent': '#GGGGGG'})
        assert merged['primary'] == DEFAULT_THEME['primary']
        assert merged['accent'] == DEFAULT_THEME['accent']

    def test_non_string_is_ignored(self):
        merged = TenantThemeService._merge({'primary': 123})
        assert merged['primary'] == DEFAULT_THEME['primary']

    def test_value_is_lowercased(self):
        assert TenantThemeService._merge({'primary': '#ABCDEF'})['primary'] == '#abcdef'

    def test_unknown_keys_are_dropped(self):
        merged = TenantThemeService._merge({'bogus': '#123456'})
        assert 'bogus' not in merged
        assert merged == DEFAULT_THEME


class TestIsCustomized:
    def test_defaults_are_not_customized(self):
        assert TenantThemeService.is_customized(dict(DEFAULT_THEME)) is False

    def test_changed_value_is_customized(self):
        colors = dict(DEFAULT_THEME, primary='#000000')
        assert TenantThemeService.is_customized(colors) is True


class TestContrast:
    def test_defaults_have_no_warnings(self):
        assert TenantThemeService.contrast_warnings(dict(DEFAULT_THEME)) == []

    def test_near_white_header_warns(self):
        warnings = TenantThemeService.contrast_warnings({'header': '#fefefe'})
        assert any(w.startswith('Header: The brand mark') for w in warnings)

    def test_dark_accent_warns_against_dark_surface(self):
        issues = TenantThemeService.contrast_issues({'accent': '#101010'})
        assert issues['accent'] and 'in dark mode' in issues['accent'][0]

    def test_pale_primary_names_button_text_and_links(self):
        issues = TenantThemeService.contrast_issues({'primary': '#f5e050'})['primary']
        assert any(i.startswith('Button text in light and dark mode') for i in issues)
        assert any(i.startswith('Links and highlighted text in light mode') for i in issues)
        assert all(':1, under the 4.5:1 AA minimum.' in i for i in issues)

    def test_link_check_judges_the_darkened_link_colour(self):
        assert contrast_ratio('#9c6b12', '#f5f5f4') < 4.5
        link = TenantThemeService.contrast_report({'primary': '#9c6b12'})['primary'][1]
        assert link['use'] == 'links and highlighted text'
        assert link['ok'] is True

    def test_dark_secondary_judged_after_the_lift_toward_white(self):
        issues = TenantThemeService.contrast_issues({'secondary': '#9f1239'})
        assert issues['secondary'] == [
            'Text buttons in dark mode would be 3.9:1, under the 4.5:1 AA minimum.'
        ]

    def test_report_skips_invalid_and_blank(self):
        assert TenantThemeService.contrast_report({'primary': 'nope', 'accent': ''}) == {}

    def test_report_shape(self):
        report = TenantThemeService.contrast_report({'primary': '#9c6b12'})
        for result in report['primary']:
            assert set(result) == {'use', 'mode', 'ratio', 'threshold', 'ok'}
            assert result['ok'] is True

    def test_list_presets_includes_verified_default(self):
        presets = TenantThemeService.list_presets()
        assert presets['Phoenix (default)'] == DEFAULT_THEME
        presets['Phoenix (default)']['primary'] = '#000000'
        assert TenantThemeService.list_presets()['Phoenix (default)'] == DEFAULT_THEME


@pytest.fixture
async def tenants_with_staff(two_tenants):
    a, b = two_tenants
    staff = await User.create(discord_id=810, username='staff')
    await UserRole.create(user=staff, role=Role.STAFF, tenant=a)
    await UserRole.create(user=staff, role=Role.STAFF, tenant=b)
    return a, b, staff


async def test_get_theme_returns_defaults_when_unset(two_tenants):
    a, _ = two_tenants
    assert await TenantThemeService.get_theme(a.id) == DEFAULT_THEME


async def test_set_theme_persists_and_reads_back(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        result = await TenantThemeService.set_theme(staff, {'primary': '#0E7470'})
    assert result['primary'] == '#0e7470'
    reloaded = await TenantThemeService.get_theme(a.id)
    assert reloaded['primary'] == '#0e7470'
    assert reloaded['accent'] == DEFAULT_THEME['accent']


async def test_set_theme_blank_values_reset_to_defaults(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TenantThemeService.set_theme(staff, {'primary': '#0E7470'})
        await TenantThemeService.set_theme(staff, {k: '' for k in DEFAULT_THEME})
    assert await TenantThemeService.get_theme(a.id) == DEFAULT_THEME
    await a.refresh_from_db()
    assert CONFIG_KEY not in (a.config or {})


async def test_set_theme_rejects_invalid_hex(tenants_with_staff):
    a, _, staff = tenants_with_staff
    with tenant_scope(a.id):
        with pytest.raises(ValueError):
            await TenantThemeService.set_theme(staff, {'primary': 'not-a-hex'})


async def test_set_theme_is_staff_gated(two_tenants):
    a, _ = two_tenants
    non_staff = await User.create(discord_id=811, username='rando')
    with tenant_scope(a.id):
        with pytest.raises(PermissionError):
            await TenantThemeService.set_theme(non_staff, {'primary': '#0E7470'})


async def test_theme_is_tenant_isolated(tenants_with_staff):
    a, b, staff = tenants_with_staff
    with tenant_scope(a.id):
        await TenantThemeService.set_theme(staff, {'primary': '#111111'})
    with tenant_scope(b.id):
        await TenantThemeService.set_theme(staff, {'primary': '#222222'})
    assert (await TenantThemeService.get_theme(a.id))['primary'] == '#111111'
    assert (await TenantThemeService.get_theme(b.id))['primary'] == '#222222'
