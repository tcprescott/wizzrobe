"""Which admin areas and settings panes a viewer may open, for the app's admin shell.

The NiceGUI admin page builds the same list in ``pages/admin.py``
(``build_admin_tabs``, ``build_settings_panes``, ``build_event_settings_panes``).
``tests/test_admin_nav_drift.py`` fails when the two disagree.
"""

from dataclasses import dataclass, field
from typing import Optional

from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.tenant_setup_service import SetupStep, TenantSetupService
from application.tenant_context import get_current_tenant_id
from models import FeatureFlag, Role, User

RUN_THE_EVENT = 'Run the event'
ONLINE_PLAY = 'Online play'
PEOPLE = 'People'
COMMUNITY = 'Community'
CONFIGURATION = 'Configuration'
SYSTEM = 'System'

ADMIN_GROUP_ORDER = [RUN_THE_EVENT, ONLINE_PLAY, PEOPLE, COMMUNITY, CONFIGURATION, SYSTEM]
EVENT_GROUPS = frozenset({RUN_THE_EVENT, ONLINE_PLAY, PEOPLE})
# Areas built only in the new app; the website's admin drawer never gets them.
APP_ONLY_AREAS = frozenset({'tickets', 'patches'})


@dataclass(frozen=True)
class AdminAccess:
    """What the viewer may reach in this tenant, resolved once per page load."""

    is_staff: bool = False
    is_stream_manager: bool = False
    is_volunteer_coordinator: bool = False
    is_equipment_manager: bool = False
    is_preset_manager: bool = False
    is_sync_admin: bool = False
    is_qualifier_admin: bool = False
    is_tournament_admin: bool = False
    is_crew_coordinator: bool = False
    is_qualifier_owner: bool = False
    # Not part of any(): derived from the booleans above, not an access grant of its own.
    operated_tournament_ids: frozenset[int] = frozenset()
    # None means unknown: the board then scopes nothing per row.
    admin_tournament_ids: frozenset[int] | None = None
    crew_tournament_ids: frozenset[int] | None = None

    def any(self) -> bool:
        return any(getattr(self, f) for f in self.__dataclass_fields__
                   if f not in ('operated_tournament_ids', 'admin_tournament_ids',
                                'crew_tournament_ids'))


@dataclass(frozen=True)
class AdminArea:
    slug: str
    label: str
    icon: str
    group: str
    aliases: tuple[str, ...] = ()

    @property
    def half(self) -> str:
        """``event`` for areas that work inside one event, ``community`` for the rest."""
        return 'event' if self.group in EVENT_GROUPS else 'community'


@dataclass(frozen=True)
class SettingsPaneSpec:
    slug: str
    label: str
    icon: str
    summary: str


@dataclass
class AdminNav:
    areas: list[AdminArea]
    settings_panes: list[SettingsPaneSpec]
    event_settings_panes: list[SettingsPaneSpec]
    setup: list[SetupStep] = field(default_factory=list)
    setup_ready: bool = True


def admin_areas(access: AdminAccess, live: set, setup_ready: bool = True) -> list[AdminArea]:
    """The admin areas ``access`` opens, in menu order. Pure, so who-sees-what is testable."""
    staff = access.is_staff
    ta = access.is_tournament_admin
    cc = access.is_crew_coordinator
    sm = access.is_stream_manager
    vc = access.is_volunteer_coordinator
    areas: list[AdminArea] = []

    def add(ok: bool, slug: str, label: str, icon: str, group: str, *aliases: str) -> None:
        if ok:
            areas.append(AdminArea(slug, label, icon, group, aliases))

    add(not setup_ready and (staff or ta), 'setup', 'Setup', 'checklist', RUN_THE_EVENT)
    add(staff or ta or cc or sm, 'match-schedule', 'Match Schedule', 'view_timeline', RUN_THE_EVENT, 'schedule')
    add(staff or ta, 'tournaments', 'Tournaments', 'trophy', RUN_THE_EVENT)
    add(staff or sm, 'stages', 'Stages', 'tv', RUN_THE_EVENT)
    add((staff or sm) and FeatureFlag.STREAM_LAYOUTS in live, 'stream-layouts', 'Stream Layouts', 'live_tv', RUN_THE_EVENT)
    add(staff and FeatureFlag.EVENT_CHECK_IN in live, 'check-in-roster', 'Check-in Roster', 'fact_check', RUN_THE_EVENT, 'check-in')
    add((staff or ta) and FeatureFlag.PAYOUTS in live, 'payouts', 'Payouts', 'payments', RUN_THE_EVENT)
    add(staff and FeatureFlag.TICKET_SALES in live, 'tickets', 'Tickets', 'local_activity', RUN_THE_EVENT)
    add((staff or ta) and FeatureFlag.TRIFORCE_TEXTS in live, 'triforce-texts', 'Triforce Texts', 'change_history', RUN_THE_EVENT)
    add(bool(event_settings_panes(access, live)), 'event-settings', 'Event settings', 'event', RUN_THE_EVENT)
    add((staff or access.is_qualifier_admin or access.is_qualifier_owner) and FeatureFlag.ASYNC_QUALIFIERS in live,
        'qualifiers', 'Qualifiers', 'timer', ONLINE_PLAY)
    add(staff and FeatureFlag.BRACKETS in live, 'brackets', 'Brackets', 'schema', ONLINE_PLAY)
    add((staff or access.is_sync_admin) and FeatureFlag.SPEEDGAMING_ETL in live, 'speedgaming', 'SpeedGaming', 'sync_alt', ONLINE_PLAY)
    volunteers = (staff or vc) and FeatureFlag.VOLUNTEERS in live
    add(volunteers, 'volunteers', 'Volunteers', 'groups', PEOPLE, 'vol-roster')
    add(volunteers, 'volunteer-shifts', 'Volunteer Shifts', 'calendar_view_week', PEOPLE, 'vol-schedule')
    add(volunteers, 'shift-board', 'Shift Board', 'edit_calendar', PEOPLE)
    add(staff and FeatureFlag.COMMUNITY_EVENTS in live, 'meetups', 'Meetups', 'celebration', PEOPLE, 'community-events')
    add(staff, 'members', 'Members', 'manage_accounts', COMMUNITY, 'users')
    add((staff or access.is_equipment_manager) and FeatureFlag.EQUIPMENT in live, 'equipment', 'Equipment', 'inventory_2', COMMUNITY)
    add((staff or access.is_sync_admin) and FeatureFlag.RACETIME_ROOMS in live, 'racetime', 'Racetime', 'flag', COMMUNITY)
    add(staff and FeatureFlag.FEEDBACK in live, 'feedback', 'Feedback', 'feedback', COMMUNITY)
    add(staff and FeatureFlag.SURVEYS in live, 'surveys', 'Surveys', 'rate_review', PEOPLE)
    add(staff, 'events', 'Events', 'calendar_month', CONFIGURATION)
    add(staff or access.is_preset_manager, 'seed-presets', 'Seed Presets', 'tune', CONFIGURATION, 'presets')
    add(staff or access.is_preset_manager, 'patches', 'Patches', 'difference', CONFIGURATION)
    add(staff, 'notifications', 'Notifications', 'notifications', CONFIGURATION)
    add(bool(settings_panes(access, live)), 'settings', 'Settings', 'settings', CONFIGURATION)
    add(staff or ta or cc or (vc and FeatureFlag.VOLUNTEERS in live), 'reports', 'Reports', 'analytics', SYSTEM)
    add(staff, 'service-health', 'Service Health', 'monitor_heart', SYSTEM)
    areas.sort(key=lambda a: ADMIN_GROUP_ORDER.index(a.group))
    return areas


def settings_panes(access: AdminAccess, live: set) -> list[SettingsPaneSpec]:
    """Community settings panes ``access`` opens, in menu order."""
    staff = access.is_staff
    panes: list[SettingsPaneSpec] = []

    def add(ok: bool, slug: str, label: str, icon: str, summary: str) -> None:
        if ok:
            panes.append(SettingsPaneSpec(slug, label, icon, summary))

    add(staff, 'general', 'General', 'tune', 'How the community looks, and the clock it tells time by.')
    add(staff, 'members', 'Members', 'group_add',
        'Who can join, what everyone else sees, and what members are asked to finish.')
    add(staff, 'watch', 'Watch page', 'live_tv', 'Who is watching at /live, and whether it runs live or static.')
    add(staff and FeatureFlag.VOLUNTEERS in live, 'volunteers', 'Volunteers', 'volunteer_activism',
        'When volunteers hear about their next shift.')
    add(staff and FeatureFlag.COMMUNITY_EVENTS in live, 'community-events', 'Meetups', 'groups',
        'Whether staff approve meetups first, and how early subscribers get a reminder.')
    add(staff or access.is_sync_admin, 'discord', 'Discord', 'forum',
        'The linked server, which of its roles mean what here, and the events mirrored into it.')
    add(staff or access.is_preset_manager, 'integrations', 'Integrations', 'extension',
        'Other services this community sends to or signs in to.')
    add(staff and FeatureFlag.THIRD_PARTY_APPS in live, 'apps', 'Connected apps', 'apps',
        'Outside apps members have connected here, and blocking one.')
    add(staff, 'features', 'Features', 'toggle_on', 'Turn parts of the app on or off for this community.')
    return panes


def event_settings_panes(access: AdminAccess, live: set) -> list[SettingsPaneSpec]:
    """The event's own settings panes, staff only, in menu order."""
    if not access.is_staff:
        return []
    panes = [
        SettingsPaneSpec('dates', 'Dates & hours', 'date_range',
                         'When the event runs, and the hours matches can start each day.'),
        SettingsPaneSpec('capacity', 'Capacity', 'groups_2', 'How much the scheduler lets run at the same time.'),
        SettingsPaneSpec('venue', 'Venue', 'chair', 'The stations in the room and the screens on its walls.'),
        SettingsPaneSpec('timezone', 'Timezone', 'schedule', 'The community’s clock, or one of this event’s own.'),
        SettingsPaneSpec('crew', 'Crew', 'podcasts',
                         'Whether matches take broadcasters as well as commentators and trackers.'),
    ]
    if FeatureFlag.VOLUNTEERS in live:
        panes.append(SettingsPaneSpec('reward-tiers', 'Volunteer reward tiers', 'military_tech',
                                      'The hour totals that earn a volunteer something.'))
    if FeatureFlag.DISCORD_ROLE_SYNC in live:
        panes.append(SettingsPaneSpec('discord-roles', 'Discord Roles from Wizzrobe', 'forum',
                                      'Discord roles this event gives its tournament players, crew and volunteers.'))
    return panes


class AdminNavService:
    """Who may open what in the admin area of the community in scope."""

    @staticmethod
    async def access(user: User) -> AdminAccess:
        roles = await AuthService.get_roles(user)
        # Must stay tenant-filtered: these hang off the global User, so unfiltered a tournament
        # admin in one community passes this gate in every other.
        admin_tournament_ids, cc_tournament_ids = await AuthService.tournament_scope(user)
        return AdminAccess(
            # is_staff, not the literal STAFF grant, so a super-admin passes.
            is_staff=await AuthService.is_staff(user),
            is_stream_manager=Role.STREAM_MANAGER in roles,
            is_volunteer_coordinator=Role.VOLUNTEER_COORDINATOR in roles,
            is_equipment_manager=await AuthService.is_equipment_manager(user),
            is_preset_manager=Role.PRESET_MANAGER in roles,
            is_sync_admin=Role.SYNC_ADMIN in roles,
            is_qualifier_admin=Role.QUALIFIER_ADMIN in roles,
            is_tournament_admin=bool(admin_tournament_ids),
            is_crew_coordinator=bool(cc_tournament_ids),
            is_qualifier_owner=await user.admin_async_qualifiers.filter(  # type: ignore[attr-defined]
                tenant_id=get_current_tenant_id()).exists(),
            operated_tournament_ids=frozenset(admin_tournament_ids | cc_tournament_ids),
            admin_tournament_ids=frozenset(admin_tournament_ids),
            crew_tournament_ids=frozenset(cc_tournament_ids),
        )

    async def nav(self, user: User, access: Optional[AdminAccess] = None) -> AdminNav:
        """Every admin area, settings pane and setup step ``user`` may open here.

        Someone with no admin access gets empty lists rather than an error, so the
        app can simply leave Admin out of Work.
        """
        access = access or await self.access(user)
        if not access.any():
            return AdminNav(areas=[], settings_panes=[], event_settings_panes=[])
        live = await FeatureFlagService().enabled_flags()
        steps = await TenantSetupService().status()
        ready = TenantSetupService.is_ready(steps)
        return AdminNav(
            areas=admin_areas(access, live, setup_ready=ready),
            settings_panes=settings_panes(access, live),
            event_settings_panes=event_settings_panes(access, live),
            setup=steps if (access.is_staff or access.is_tournament_admin) else [],
            setup_ready=ready,
        )
