"""Admin Dashboard Page"""

from dataclasses import dataclass

from fastapi import Request
from nicegui import app, ui

from application.services import (
    AuthService,
    FeatureFlagService,
    SetupStep,
    TenantService,
    TenantSetupService,
    get_user_from_session,
)
from application.tenant_context import get_current_tenant_id
from middleware.auth import protected_page, protected_tab_page
from models import FeatureFlag, Role
from pages.admin_tabs.admin_brackets import admin_brackets_page
from pages.admin_tabs.admin_check_in import admin_check_in_page
from pages.admin_tabs.admin_community_events import admin_community_events_page
from pages.admin_tabs.admin_equipment import admin_equipment_page
from pages.admin_tabs.admin_feedback import admin_feedback_page
from pages.admin_tabs.admin_payouts import admin_payouts_page
from pages.admin_tabs.admin_presets import admin_presets_page
from pages.admin_tabs.admin_qualifiers import admin_qualifiers_page
from pages.admin_tabs.admin_racetime import admin_racetime_page
from pages.admin_tabs.admin_schedule import admin_schedule_page
from pages.admin_tabs.admin_service_health import admin_service_health_page
from pages.admin_tabs.admin_settings import admin_stages_page, admin_tournaments_page
from pages.admin_tabs.admin_setup import admin_setup_page
from pages.admin_tabs.admin_shift_board import admin_shift_board_page
from pages.admin_tabs.admin_speedgaming import admin_speedgaming_page
from pages.admin_tabs.admin_users import admin_users_page
from pages.admin_tabs.admin_volunteer_roster import admin_volunteer_roster_page
from pages.admin_tabs.admin_volunteers import admin_volunteers_page
from pages.admin_tabs.reports import VOLUNTEER_REPORTS, reports_page
from pages.admin_tabs.settings import (
    LEGACY_TAB_PANES,
    SETTINGS_TAB_LABEL,
    admin_settings_page,
    build_settings_panes,
)
from pages.admin_tabs.triforce_texts import admin_triforce_texts_page
from theme.base import BaseLayout
from theme.tables.match_access import MatchBoardAccess

_ADMIN_GROUP_ORDER = ['Operations', 'Online play', 'Community', 'System']


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
    # The tournaments this viewer administers or coordinates, for the surfaces
    # that scope to them. Excluded from ``any()``: it is derived from the two
    # booleans above, and a set is not an access grant of its own.
    operated_tournament_ids: frozenset[int] = frozenset()
    # The two halves of that union, for the board's per-row capabilities. None
    # when unknown (tests, older callers): the board then scopes nothing per row.
    admin_tournament_ids: frozenset[int] | None = None
    crew_tournament_ids: frozenset[int] | None = None

    def any(self) -> bool:
        return any(getattr(self, f) for f in self.__dataclass_fields__
                   if f not in ('operated_tournament_ids', 'admin_tournament_ids',
                                'crew_tournament_ids'))


def build_admin_tabs(
    access: AdminAccess,
    live: set,
    reports_kwargs: dict,
    setup_steps: list[SetupStep] | None = None,
    base_path: str = '/admin',
    *,
    match_id: int | None = None,
    day: str | None = None,
    reschedule_request: int | None = None,
    comments: int | None = None,
    qualifier: int | None = None,
    qualifier_tab: str | None = None,
    ada_request: int | None = None,
    community_event: int | None = None,
    settings_pane: str | None = None,
) -> list[dict]:
    """The admin drawer's tabs, ordered by group.

    Module-level and pure so the ordering rules — notably which tab a new
    community lands on — are testable without a browser.
    """
    is_staff = access.is_staff
    is_ta_any = access.is_tournament_admin
    is_cc_any = access.is_crew_coordinator
    is_qa_any = access.is_qualifier_owner
    is_sm = access.is_stream_manager
    # What the board may offer, resolved per capability rather than from one
    # can-do-everything boolean — the services have always been this granular.
    board_access = MatchBoardAccess.for_roles(
        is_staff=is_staff,
        is_tournament_admin=is_ta_any,
        is_stream_manager=is_sm,
        is_crew_coordinator=is_cc_any,
        admin_tournament_ids=access.admin_tournament_ids,
        crew_tournament_ids=access.crew_tournament_ids,
    )
    # Staff and the stream manager operate community-wide; a TA/CC's authority is
    # per tournament, so their board is narrowed to the ones they run rather than
    # showing every match in the community with controls that refuse.
    board_tournament_ids = (
        None if (is_staff or is_sm)
        else sorted(access.operated_tournament_ids)
    )
    # Each tab that can be deep-linked gets its own params. `state` in
    # reports_kwargs is the Reports tab's own filter and deliberately not
    # reused for the board's state filter — one param must not mean two things.
    schedule_kwargs = {
        'access': board_access,
        'match_id': match_id or comments,
        'tournament_ids': board_tournament_ids,
        # A request id from the "Reschedule request" DM's button: the board
        # opens that request's decision dialog on arrival, so the ask lands on
        # the control that answers it rather than on a row to go and find.
        'reschedule_request': reschedule_request,
        # A match id from a staff alert DM (a player's comment): the board
        # narrows to it and opens its sheet on the comments.
        'comments': comments,
    }

    tabs: list[dict] = []
    # Each tab carries a drawer 'group'; the list is stable-sorted by
    # _ADMIN_GROUP_ORDER below so the drawer reads as labeled sections
    # instead of a flat scroll. Icons are unique per destination (no repeats).
    #
    # Setup comes first while the community is not ready: BaseLayout defaults to
    # tabs[0], so a new community lands on the checklist rather than on Create
    # Match, which cannot yet succeed. It disappears on its own once the
    # required steps are done — nothing stores that it was seen.
    # Filled in once every tab is known, so a step only links to a tab the viewer has.
    setup_kwargs: dict = {'steps': setup_steps, 'base_path': base_path}
    if setup_steps and not TenantSetupService.is_ready(setup_steps) and (is_staff or is_ta_any):
        tabs.append({'label': 'Setup', 'icon': 'checklist', 'group': 'Operations',
                     'content': (admin_setup_page, (), setup_kwargs)})
    # STREAM_MANAGER is here because assigning a stage is a match-board action
    # and this is the only board that offers it — the role existed for that job
    # with no surface that did it. What they get once here is one control:
    # ``board_access`` grants them ``assign_stream`` and nothing else.
    if is_staff or is_ta_any or is_cc_any or is_sm:
        tabs.append({'label': 'Match Schedule', 'icon': 'view_timeline', 'group': 'Operations', 'aliases': ('schedule',), 'content': (admin_schedule_page, (), schedule_kwargs)})
    if is_staff:
        tabs.append({'label': 'Members', 'icon': 'manage_accounts', 'group': 'Operations', 'aliases': ('users',),
                     'content': (admin_users_page, (),
                                 {'accommodations': FeatureFlag.ADA_ACCOMMODATIONS in live,
                                  'emergency_contacts': FeatureFlag.EMERGENCY_CONTACTS in live,
                                  # The ADA staff DMs' button: opens that request.
                                  'ada_request': ada_request})})
    if is_staff or is_ta_any:
        tabs.append({'label': 'Tournaments', 'icon': 'emoji_events', 'group': 'Operations', 'content': admin_tournaments_page})
    if is_staff or access.is_stream_manager:
        tabs.append({'label': 'Stages', 'icon': 'tv', 'group': 'Operations', 'content': admin_stages_page})
    # A tournament admin belongs here: posting their event's payout block is the
    # job this replaces, and the service scopes each of them to their own events.
    if (is_staff or is_ta_any) and FeatureFlag.PAYOUTS in live:
        tabs.append({'label': 'Payouts', 'icon': 'payments', 'group': 'Operations', 'content': admin_payouts_page})
    if is_staff and FeatureFlag.EVENT_CHECK_IN in live:
        tabs.append({'label': 'Check-in Roster', 'icon': 'fact_check', 'group': 'Operations', 'aliases': ('check-in',), 'content': admin_check_in_page})
    if is_staff or access.is_preset_manager:
        tabs.append({'label': 'Seed Presets', 'icon': 'tune', 'group': 'Online play', 'aliases': ('presets',), 'content': admin_presets_page})
    if (is_staff or access.is_qualifier_admin or is_qa_any) and FeatureFlag.ASYNC_QUALIFIERS in live:
        # The "runs are waiting" DM lands here with a qualifier and a tab, so a
        # reviewer arrives at the queue rather than at a list to go and search.
        tabs.append({'label': 'Qualifiers', 'icon': 'timer', 'group': 'Online play',
                     'content': (admin_qualifiers_page, (),
                                 {'qualifier': qualifier, 'open_tab': qualifier_tab})})
    if (is_staff or access.is_sync_admin) and FeatureFlag.RACETIME_ROOMS in live:
        tabs.append({'label': 'Racetime', 'icon': 'flag', 'group': 'Online play', 'content': admin_racetime_page})
    if (is_staff or access.is_sync_admin) and FeatureFlag.SPEEDGAMING_ETL in live:
        tabs.append({'label': 'SpeedGaming', 'icon': 'sync_alt', 'group': 'Online play', 'content': admin_speedgaming_page})
    if is_staff and FeatureFlag.BRACKETS in live:
        tabs.append({'label': 'Brackets', 'icon': 'schema', 'group': 'Online play', 'content': admin_brackets_page})
    if (is_staff or is_ta_any) and FeatureFlag.TRIFORCE_TEXTS in live:
        tabs.append({'label': 'Triforce Texts', 'icon': 'svguse:/static/triforce.svg#triforce|0 0 512 512', 'group': 'Community', 'content': admin_triforce_texts_page})
    if (is_staff or access.is_volunteer_coordinator) and FeatureFlag.VOLUNTEERS in live:
        tabs.append({'label': 'Volunteers', 'icon': 'groups', 'group': 'Community', 'aliases': ('vol-roster',), 'content': admin_volunteer_roster_page})
        tabs.append({'label': 'Volunteer Shifts', 'icon': 'calendar_view_week', 'group': 'Community', 'aliases': ('vol-schedule',), 'content': (admin_volunteers_page, (), {'day': day})})
        tabs.append({'label': 'Shift Board', 'icon': 'edit_calendar', 'group': 'Community', 'content': (admin_shift_board_page, (), {'day': day})})
    if (is_staff or access.is_equipment_manager) and FeatureFlag.EQUIPMENT in live:
        tabs.append({'label': 'Equipment', 'icon': 'inventory_2', 'group': 'Community', 'content': admin_equipment_page})
    if is_staff and FeatureFlag.COMMUNITY_EVENTS in live:
        # The "event to review" DM lands here with the event's dialog open.
        tabs.append({'label': 'Community Events', 'icon': 'celebration', 'group': 'Community',
                     'content': (admin_community_events_page, (), {'open_event': community_event})})
    if is_staff and FeatureFlag.FEEDBACK in live:
        tabs.append({'label': 'Feedback', 'icon': 'feedback', 'group': 'Community', 'content': admin_feedback_page})
    if is_staff or is_ta_any or is_cc_any:
        tabs.append({'label': 'Reports', 'icon': 'analytics', 'group': 'System', 'content': (reports_page, (), reports_kwargs)})
    elif access.is_volunteer_coordinator and FeatureFlag.VOLUNTEERS in live:
        # A coordinator reads Volunteer Coverage and nothing else here: the
        # other reports cover matches, crew and stages they have no say over.
        tabs.append({'label': 'Reports', 'icon': 'analytics', 'group': 'System',
                     'content': (reports_page, (), {**reports_kwargs, 'only': VOLUNTEER_REPORTS})})
    if is_staff:
        tabs.append({'label': 'Service Health', 'icon': 'monitor_heart', 'group': 'System', 'content': admin_service_health_page})
    # Every setting lives under one entry with its own menu; the panes keep
    # their old tabs' role gates, so a sync admin or preset manager still finds
    # the one pane they had, and the retired slugs resolve to it.
    settings_panes = build_settings_panes(access, live)
    if settings_panes:
        tabs.append({'label': SETTINGS_TAB_LABEL, 'icon': 'settings', 'group': 'System',
                     'aliases': tuple(LEGACY_TAB_PANES),
                     'content': (admin_settings_page, (),
                                 {'panes': settings_panes, 'pane': settings_pane,
                                  'base_path': base_path})})

    setup_kwargs['reachable_tabs'] = frozenset(t['label'] for t in tabs)
    tabs.sort(key=lambda t: _ADMIN_GROUP_ORDER.index(t['group']))
    return tabs


def create() -> None:
    @protected_tab_page('/admin')
    async def admin_dashboard_page(
        section: str | None = None,
        request: Request = None,
        report: str | None = None,
        start: str | None = None,
        end: str | None = None,
        bucket: str | None = None,
        tournament_id: int | None = None,
        user_id: int | None = None,
        stage_id: int | None = None,
        state: str | None = None,
        approval: str | None = None,
        action: str | None = None,
        focus: str | None = None,
        category: str | None = None,
        page: int | None = None,
        match_id: int | None = None,
        day: str | None = None,
        reschedule_request: int | None = None,
        comments: int | None = None,
        qualifier: int | None = None,
        tab: str | None = None,
        ada_request: int | None = None,
        community_event: int | None = None,
        pane: str | None = None,
    ) -> None:
        ui.page_title(f'{await TenantService.current_community_name() or "Wizzrobe"} — Admin')
        signed_in = app.storage.user.get('authenticated', False)
        if not signed_in:
            with ui.row():
                ui.label('You must be logged in to view this page.').classes('text-error')
            return

        # get_user_from_session enforces is_active, so a deactivated account
        # loses admin access on its next request — consistent with the REST API
        # and the login flow, and not bypassed by this non-role-gated page.
        user = await get_user_from_session(app.storage.user)
        if user is None:
            with ui.row():
                ui.label('User not found in the database.').classes('text-error')
            return

        roles = await AuthService.get_roles(user)
        # Tenant-filtered inside the service: these reverse relations hang off
        # the *global* User, so unfiltered they let a tournament admin in one
        # community through this gate in every other one. The ids (not just
        # "any?") because the Schedule board scopes to them.
        admin_tournament_ids, cc_tournament_ids = await AuthService.tournament_scope(user)
        access = AdminAccess(
            # Staff-equivalence (not the literal grant) so a platform super-admin
            # has full authority in this tenant.
            is_staff=await AuthService.is_staff(user),
            is_stream_manager=Role.STREAM_MANAGER in roles,
            is_volunteer_coordinator=Role.VOLUNTEER_COORDINATOR in roles,
            is_equipment_manager=Role.EQUIPMENT_MANAGER in roles,
            is_preset_manager=Role.PRESET_MANAGER in roles,
            is_sync_admin=Role.SYNC_ADMIN in roles,
            is_qualifier_admin=Role.QUALIFIER_ADMIN in roles,
            is_tournament_admin=bool(admin_tournament_ids),
            is_crew_coordinator=bool(cc_tournament_ids),
            is_qualifier_owner=await user.admin_async_qualifiers.filter(
                tenant_id=get_current_tenant_id()).exists(),
            operated_tournament_ids=frozenset(admin_tournament_ids | cc_tournament_ids),
            admin_tournament_ids=frozenset(admin_tournament_ids),
            crew_tournament_ids=frozenset(cc_tournament_ids),
        )

        # Per-tenant feature flags: a subsystem's tab only appears when the
        # tenant has that feature live (available AND enabled), in addition to
        # the role gate. Loaded once for all tabs, as is the setup checklist.
        live = await FeatureFlagService().enabled_flags()
        setup_steps = await TenantSetupService().status()

        reports_kwargs = {
            'report': report,
            'start': start,
            'end': end,
            'bucket': bucket,
            'tournament_id': tournament_id,
            'user_id': user_id,
            'stage_id': stage_id,
            'state': state,
            'approval': approval,
            'action': action,
            'focus': focus,
            'category': category,
            'page': page,
        }

        base_path = f"{request.scope.get('root_path', '')}/admin" if request else '/admin'
        tabs = build_admin_tabs(
            access, live, reports_kwargs, setup_steps, base_path,
            match_id=match_id, day=day, reschedule_request=reschedule_request,
            comments=comments,
            qualifier=qualifier, qualifier_tab=tab, ada_request=ada_request,
            community_event=community_event,
            # A retired tab slug (/admin/appearance) names the pane that took it in.
            settings_pane=pane or LEGACY_TAB_PANES.get(section or ''),
        )

        # No tabs is a refusal too: a coordinator or equipment manager whose
        # feature is off holds a role but has nothing here to open.
        if not access.any() or not tabs:
            from theme.error_page import prepared_layout, render_error_page
            render_error_page(
                status_code=403, headline='Forbidden',
                message="You don't have access to the admin area. If you think that's wrong, ask a staff member to check your role.",
                user=user,
                layout=await prepared_layout(user),
            )
            return

        base_layout = BaseLayout(
            tabs=tabs, section=section, base_path=base_path, page_name='admin', user=user,
            show_admin=True,
        )
        await base_layout.render()

    # Settings panes are a path segment below the Settings tab, like the tab is
    # below /admin, so a pane's URL reads like a route and can be bookmarked.
    @protected_page('/admin/settings/{pane}', telemetry_path='/admin')
    async def admin_settings_pane_page(pane: str, request: Request = None) -> None:
        await admin_dashboard_page(section='settings', pane=pane, request=request)
