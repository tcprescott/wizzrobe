"""Admin → Settings: every community setting in one place, behind its own menu.

Settings used to be nine drawer tabs (Settings, Features, Appearance, Timezone,
Role Mappings, Discord Events, Webhooks, Randomizer Keys, Profile Reminders) interleaved with the
day-to-day tools, and the one called Settings was a 2,000px column with two
Save buttons that each wrote a different half of it. Now the drawer has one
Settings entry, which opens on a home page of tiles, one per pane; each pane
is a stack of cards that save on their own, with a link back to the tiles.

Panes are addressed as ``/admin/settings/<pane>``. The retired tab slugs
(``/admin/appearance`` and the rest) resolve to the pane that now holds them,
because DM buttons and bookmarks outlive the tabs they pointed at.
"""

import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from nicegui import app, ui

from application.services import AuthService, get_user_from_session
from application.table_preferences_context import current_table_prefs, table_prefs_scope
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.timezone_context import current_timezone_name, tz_scope
from models import FeatureFlag
from pages.admin_tabs.admin_challonge import admin_challonge_connection_page
from pages.admin_tabs.admin_discord_events import admin_discord_events_page
from pages.admin_tabs.admin_discord_role_sync import TAB_LABEL as ROLE_SYNC_TAB_LABEL
from pages.admin_tabs.admin_discord_role_sync import admin_discord_role_sync_page
from pages.admin_tabs.admin_discord_roles import admin_discord_roles_page
from pages.admin_tabs.admin_features import admin_features_page
from pages.admin_tabs.admin_profile_reminders import admin_profile_reminders_page
from pages.admin_tabs.admin_randomizer_keys import admin_randomizer_keys_page
from pages.admin_tabs.admin_startgg import admin_startgg_connection_page
from pages.admin_tabs.admin_theme import admin_theme_page
from pages.admin_tabs.admin_timezone import admin_timezone_page
from pages.admin_tabs.admin_webhooks import admin_webhooks_page
from pages.admin_tabs.settings.community_events import community_events_settings_pane
from pages.admin_tabs.settings.connected_apps import connected_apps_settings_pane
from pages.admin_tabs.settings.event import capacity_pane, dates_and_hours_pane
from pages.admin_tabs.settings.event_crew import event_crew_pane
from pages.admin_tabs.settings.event_timezone import event_timezone_pane
from pages.admin_tabs.settings.members import members_settings_pane
from pages.admin_tabs.settings.venue import venue_settings_pane
from pages.admin_tabs.settings.volunteers import reward_tiers_pane, volunteers_settings_pane
from pages.admin_tabs.settings.watch_page import watch_page_settings_pane
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.page_header import nested_page_headers, page_header
from theme.save_footer import UnsavedChanges
from theme.url_state import TAIL, url_param, url_state

#: The admin drawer label, and so the ``selected_tab`` value this page hears.
SETTINGS_TAB_LABEL = 'Settings'

#: Retired admin tab slug → the pane that holds it now.
LEGACY_TAB_PANES: dict[str, str] = {
    'appearance': 'general',
    'timezone': 'general',
    'features': 'features',
    'role-mappings': 'discord',
    'discord-events': 'discord',
    'webhooks': 'integrations',
    'randomizer-keys': 'integrations',
    'challonge': 'integrations',
    'profile-reminders': 'members',
}


@dataclass(frozen=True)
class EmbeddedPage:
    """A page that used to be its own tab, now one card of a pane."""

    render: Callable[..., Awaitable[None]]
    kwargs: dict = field(default_factory=dict)
    #: The old tab label its ``wire_tab_refresh`` listens for.
    refresh_label: Optional[str] = None


@dataclass(frozen=True)
class SettingsPane:
    slug: str
    label: str
    icon: str
    subtitle: str
    #: A pane built from cards of its own; takes ``can_edit``.
    native: Optional[Callable[[bool], Awaitable[None]]] = None
    embedded: tuple[EmbeddedPage, ...] = ()

    @property
    def refresh_labels(self) -> tuple[str, ...]:
        return tuple(p.refresh_label for p in self.embedded if p.refresh_label)


def build_settings_panes(access, live: set) -> list[SettingsPane]:
    """The panes this viewer may open, in menu order.

    Pure, like ``build_admin_tabs``, so who-sees-what is testable without a
    browser. Each pane keeps the role gate its old tab had: a sync admin still
    reaches Discord Events and a preset manager still reaches Randomizer Keys,
    and they see only those.
    """
    is_staff = access.is_staff
    panes: list[SettingsPane] = []
    if is_staff:
        panes.append(SettingsPane(
            'general', 'General', 'tune',
            'How the community looks, and the clock it tells time by.',
            embedded=(EmbeddedPage(admin_theme_page), EmbeddedPage(admin_timezone_page)),
        ))
        panes.append(SettingsPane(
            'members', 'Members', 'group_add',
            'Who can join, what everyone else sees, and what members are asked to finish.',
            native=members_settings_pane,
            embedded=(EmbeddedPage(admin_profile_reminders_page),),
        ))
        panes.append(SettingsPane(
            'watch', 'Watch page', 'live_tv',
            'Who is watching at /live, and whether it runs live or static.',
            native=watch_page_settings_pane,
        ))
        if FeatureFlag.VOLUNTEERS in live:
            panes.append(SettingsPane(
                'volunteers', 'Volunteers', 'volunteer_activism',
                'When volunteers hear about their next shift.',
                native=volunteers_settings_pane,
            ))
        if FeatureFlag.COMMUNITY_EVENTS in live:
            panes.append(SettingsPane(
                'community-events', 'Meetups', 'groups',
                'Whether staff approve meetups first, and how early subscribers get a reminder.',
                native=community_events_settings_pane,
            ))
    discord: list[EmbeddedPage] = []
    if is_staff:
        discord.append(EmbeddedPage(
            admin_discord_roles_page, {'volunteer_mappings': FeatureFlag.VOLUNTEERS in live},
            refresh_label='Role Mappings',
        ))
    if is_staff or access.is_sync_admin:
        discord.append(EmbeddedPage(admin_discord_events_page, refresh_label='Discord Events'))
    if discord:
        panes.append(SettingsPane(
            'discord', 'Discord', 'forum',
            'The linked server, which of its roles mean what here, and the events mirrored into it.',
            embedded=tuple(discord),
        ))
    integrations: list[EmbeddedPage] = []
    if is_staff and FeatureFlag.CHALLONGE in live:
        integrations.append(EmbeddedPage(admin_challonge_connection_page))
    if is_staff and FeatureFlag.STARTGG in live:
        integrations.append(EmbeddedPage(admin_startgg_connection_page))
    if is_staff:
        integrations.append(EmbeddedPage(admin_webhooks_page, refresh_label='Webhooks'))
    if is_staff or access.is_preset_manager:
        integrations.append(EmbeddedPage(admin_randomizer_keys_page, refresh_label='Randomizer Keys'))
    if integrations:
        panes.append(SettingsPane(
            'integrations', 'Integrations', 'extension',
            'Other services this community sends to or signs in to.',
            embedded=tuple(integrations),
        ))
    if is_staff and FeatureFlag.THIRD_PARTY_APPS in live:
        panes.append(SettingsPane(
            'apps', 'Connected apps', 'apps',
            'Outside apps members have connected here, and blocking one.',
            native=connected_apps_settings_pane,
        ))
    if is_staff:
        panes.append(SettingsPane(
            'features', 'Features', 'toggle_on',
            'Turn parts of the app on or off for this community.',
            embedded=(EmbeddedPage(admin_features_page),),
        ))
    return panes


#: The admin drawer label of the event's own settings.
EVENT_SETTINGS_TAB_LABEL = 'Event settings'

#: Community Settings panes that moved to Event settings → the pane that took them.
MOVED_TO_EVENT_PANES: dict[str, str] = {'event': 'dates', 'venue': 'venue'}


def build_event_settings_panes(access, live: set) -> list[SettingsPane]:
    """The event's own settings: what is set again for each event, in menu order.

    Staff only, like the community panes these came from.
    """
    if not access.is_staff:
        return []
    panes = [
        SettingsPane(
            'dates', 'Dates & hours', 'date_range',
            'When the event runs, and the hours matches can start each day.',
            native=dates_and_hours_pane,
        ),
        SettingsPane(
            'capacity', 'Capacity', 'groups_2',
            'How much the scheduler lets run at the same time.',
            native=capacity_pane,
        ),
        SettingsPane(
            'venue', 'Venue', 'chair',
            'The stations in the room and the screens on its walls.',
            native=venue_settings_pane,
        ),
        SettingsPane(
            'timezone', 'Timezone', 'schedule',
            'The community’s clock, or one of this event’s own.',
            native=event_timezone_pane,
        ),
        SettingsPane(
            'crew', 'Crew', 'podcasts',
            'Whether matches take broadcasters as well as commentators and trackers.',
            native=event_crew_pane,
        ),
    ]
    if FeatureFlag.VOLUNTEERS in live:
        panes.append(SettingsPane(
            'reward-tiers', 'Volunteer reward tiers', 'military_tech',
            'The hour totals that earn a volunteer something.',
            native=reward_tiers_pane,
        ))
    if FeatureFlag.DISCORD_ROLE_SYNC in live:
        panes.append(SettingsPane(
            'discord-roles', ROLE_SYNC_TAB_LABEL, 'forum',
            'Discord roles this event gives its tournament players, crew and volunteers.',
            embedded=(EmbeddedPage(admin_discord_role_sync_page, refresh_label=ROLE_SYNC_TAB_LABEL),),
        ))
    return panes


def resolve_pane(panes: list[SettingsPane], requested: Optional[str]) -> Optional[SettingsPane]:
    """The pane a URL asks for, or None for the Settings home.

    A viewer with a single pane (a sync admin, a preset manager) has no home to
    go back to, so every address opens that pane.
    """
    if len(panes) == 1:
        return panes[0]
    by_slug = {p.slug: p for p in panes}
    slug = LEGACY_TAB_PANES.get(requested or '', requested)
    return by_slug.get(slug or '')


#: Arms the browser's own leave-page prompt while any card has unsaved edits.
_BEFOREUNLOAD_JS = """
if (!window.__wizUnsavedHook) {
    window.__wizUnsavedHook = true;
    window.addEventListener('beforeunload', (e) => {
        if (window.wizUnsaved) { e.preventDefault(); e.returnValue = ''; }
    });
}
window.wizUnsaved = %d;
"""


async def admin_settings_page(
    panes: list[SettingsPane],
    pane: Optional[str] = None,
    base_path: str = '/admin',
    tab_label: str = SETTINGS_TAB_LABEL,
    title: str = 'Settings',
    subtitle: str = 'Everything about how this community runs. Pick an area to change it.',
) -> None:
    if not panes:
        return
    # The address, not ``pane``: that is the request's, stale once the viewer
    # has switched tabs, and Back lands here with a different one.
    pane = url_state().requested(TAIL)
    current = resolve_pane(panes, pane)
    if pane and current is None:
        # A DM button or bookmark can outlive the pane it named.
        ui.notify('That settings page doesn’t exist any more. Pick one below.', color='warning')
    has_home = len(panes) > 1
    can_edit = await AuthService.is_staff(
        await get_user_from_session(app.storage.user))
    # Captured now: a pane built later is built inside a click handler, which
    # has lost the tenant, the display zone and the table layouts.
    tenant_id = get_current_tenant_id()
    tz = current_timezone_name()
    prefs = current_table_prefs()

    containers: dict[str, ui.element] = {}
    built: set[str] = set()
    state: dict[str, Optional[str]] = {'slug': current.slug if current else None}

    def unsaved_changed() -> None:
        ui.run_javascript(_BEFOREUNLOAD_JS % len(unsaved.dirty()))

    unsaved = UnsavedChanges(on_change=unsaved_changed)

    async def build(p: SettingsPane) -> None:
        built.add(p.slug)
        with containers[p.slug], tenant_scope(tenant_id), tz_scope(tz), table_prefs_scope(prefs), unsaved.collect():
            if has_home:
                ui.button(f'All {title.lower()}', icon='chevron_left', on_click=leave_pane) \
                    .props('flat dense no-caps color=primary').classes('wiz-settings-back')
            page_header(p.label, p.subtitle)
            if p.native is not None:
                await p.native(can_edit)
            for page in p.embedded:
                with ui.element('section').classes('wiz-panel wiz-settings-embed'), nested_page_headers():
                    await page.render(**page.kwargs)

    def reveal(slug: Optional[str]) -> None:
        state['slug'] = slug
        home.set_visibility(slug is None)
        for key, container in containers.items():
            container.set_visibility(key == slug)
        pane_param.set(slug or '')
        ui.run_javascript('window.scrollTo(0, 0)')

    async def restore_pane(slug: str) -> None:
        # A pane left with unsaved edits keeps them: its container is only
        # hidden, so Forward finds them where they were.
        target = resolve_pane(panes, slug) if slug else None
        if target is None:
            reveal(None)
        else:
            await show(target)

    async def show(p: SettingsPane) -> None:
        reveal(p.slug)
        if p.slug not in built:
            await build(p)
            return
        for label in p.refresh_labels:
            ui.run_javascript(f"emitEvent('selected_tab', {json.dumps(label)})")

    async def leave_pane() -> None:
        dirty = unsaved.dirty()
        if not dirty:
            reveal(None)
            return
        label = next(p.label for p in panes if p.slug == state['slug'])

        async def save_and_leave() -> None:
            dialog.close()
            for footer in dirty:
                if not await footer.save():
                    return
            reveal(None)

        def discard_and_leave() -> None:
            dialog.close()
            for footer in dirty:
                footer.discard()
            reveal(None)

        with form_dialog('Leave without saving?', card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('q-pa-md'):
                ui.label(f'Your changes on {label} haven’t been saved.')
            with dialog_actions().classes('justify-end'):  # style-guide: exempt — a three-way choice, short labels keep it one line
                ui.button('Keep editing', on_click=dialog.close).props('flat no-caps')
                ui.button('Discard', on_click=discard_and_leave).props('outline no-caps color=primary')
                ui.button('Save', on_click=save_and_leave).props('unelevated no-caps color=primary')
        dialog.open()

    labels = {p.slug: p.label for p in panes}
    with ui.column().classes('wiz-settings') as root:
        pane_param = url_param(
            TAIL, root, default='', value=state['slug'] or '', label=labels.get, on_restore=restore_pane,
        )
        home = ui.column().classes('wiz-settings__home wiz-section-stack')
        home.set_visibility(current is None)
        if has_home:
            with home:
                page_header(title, subtitle)
                with ui.element('div').classes('wiz-settings-grid'):
                    for p in panes:
                        tile = ui.element('button').classes('wiz-settings-tile').props(
                            f'type=button data-pane={p.slug}')
                        tile.on('click', lambda _e=None, p=p: show(p))
                        with tile:
                            with ui.element('span').classes('wiz-settings-tile__icon'):
                                ui.icon(p.icon)
                            with ui.element('span').classes('wiz-settings-tile__text'):
                                name = ui.label(p.label).classes('wiz-settings-tile__name')
                                name.tag = 'span'
                                blurb = ui.label(p.subtitle).classes('wiz-settings-tile__sub')
                                blurb.tag = 'span'
                            ui.icon('chevron_right').classes('wiz-settings-tile__chevron')
        for p in panes:
            containers[p.slug] = ui.column().classes('wiz-settings__pane wiz-section-stack')
            containers[p.slug].set_visibility(p is current)

    if current is not None:
        await build(current)

    def on_admin_tab(e) -> None:
        # Coming back to Settings from another admin tab: the open pane's
        # embedded pages want the refresh their own tab used to get.
        if e.args != tab_label:
            return
        if state['slug'] is None:
            return
        for label in next(p for p in panes if p.slug == state['slug']).refresh_labels:
            ui.run_javascript(f"emitEvent('selected_tab', {json.dumps(label)})")

    ui.on('selected_tab', on_admin_tab)
