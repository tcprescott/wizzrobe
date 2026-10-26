"""Admin → Settings: which panes each role sees, and where old tab links land."""

from models import FeatureFlag
from pages.admin import AdminAccess, build_admin_tabs
from pages.admin_tabs.settings import (
    LEGACY_TAB_PANES,
    build_event_settings_panes,
    build_settings_panes,
    resolve_pane,
)
from theme.base import tab_slug

STAFF = AdminAccess(is_staff=True)


def _slugs(panes):
    return [p.slug for p in panes]


def test_staff_sees_every_pane_in_menu_order():
    panes = build_settings_panes(STAFF, {FeatureFlag.VOLUNTEERS})
    assert _slugs(panes) == [
        'general', 'members', 'watch', 'volunteers',
        'discord', 'integrations', 'features',
    ]


def test_staff_set_the_events_own_settings_on_event_settings():
    panes = build_event_settings_panes(STAFF, {FeatureFlag.VOLUNTEERS, FeatureFlag.DISCORD_ROLE_SYNC})
    assert _slugs(panes) == ['dates', 'capacity', 'venue', 'timezone', 'reward-tiers', 'discord-roles']
    assert build_event_settings_panes(AdminAccess(is_sync_admin=True), set()) == []


def test_volunteers_pane_follows_the_flag():
    assert 'volunteers' not in _slugs(build_settings_panes(STAFF, set()))


def test_sync_admin_gets_only_discord_events():
    panes = build_settings_panes(AdminAccess(is_sync_admin=True), set())
    assert _slugs(panes) == ['discord']
    assert panes[0].refresh_labels == ('Discord Events',)


def test_preset_manager_gets_only_randomizer_keys():
    panes = build_settings_panes(AdminAccess(is_preset_manager=True), set())
    assert _slugs(panes) == ['integrations']
    assert panes[0].refresh_labels == ('Randomizer Keys',)


def test_roles_without_settings_get_no_settings_tab():
    access = AdminAccess(is_stream_manager=True)
    assert build_settings_panes(access, set()) == []
    labels = [t['label'] for t in build_admin_tabs(access, set(), {}, None)]
    assert 'Settings' not in labels


def test_the_drawer_has_one_settings_entry():
    labels = [t['label'] for t in build_admin_tabs(STAFF, set(), {}, None)]
    assert 'Settings' in labels
    for retired in ('Appearance', 'Timezone', 'Features', 'Role Mappings',
                    'Discord Events', 'Webhooks', 'Randomizer Keys'):
        assert retired not in labels


def test_retired_slugs_resolve_to_their_pane():
    panes = build_settings_panes(STAFF, set())
    for slug, pane in LEGACY_TAB_PANES.items():
        assert resolve_pane(panes, slug).slug == pane
    tab = next(t for t in build_admin_tabs(STAFF, set(), {}, None) if t['label'] == 'Settings')
    assert set(tab['aliases']) == set(LEGACY_TAB_PANES)
    assert tab_slug('Settings') not in LEGACY_TAB_PANES


def test_no_pane_or_an_unknown_one_opens_the_settings_home():
    panes = build_settings_panes(STAFF, set())
    assert resolve_pane(panes, 'nonsense') is None
    assert resolve_pane(panes, None) is None
    assert resolve_pane(panes, 'members').slug == 'members'
    assert resolve_pane([], 'members') is None


def test_a_single_pane_skips_the_home():
    panes = build_settings_panes(AdminAccess(is_sync_admin=True), set())
    assert resolve_pane(panes, None).slug == 'discord'
    assert resolve_pane(panes, 'general').slug == 'discord'


def test_legacy_slug_reaches_the_tab_as_its_pane():
    tab = next(t for t in build_admin_tabs(STAFF, set(), {}, None, settings_pane='discord')
               if t['label'] == 'Settings')
    assert tab['content'][2]['pane'] == 'discord'
