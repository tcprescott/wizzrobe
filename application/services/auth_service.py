"""
Authorization Service - Business Logic Layer

Stateless policy helpers for role-based access control. All checks accept
a User model (or None). UI code calls get_user_from_session(app.storage.user) once at page entry to
resolve the signed-in User, then passes it into the helpers.
"""

from typing import Any, ClassVar, Optional

from application.repositories.user_role_repository import EventChoice, RoleScope, role_event_q
from application.tenant_context import get_current_tenant_id
from models import SYSTEM_USER_DISCORD_ID, Match, Role, Tournament, User, UserRole


class AuthService:
    """Stateless authorization helpers.

    Role checks are **per tenant**: ``get_roles``/``has_role`` scope every
    ``UserRole`` query to the current tenant, so a STAFF grant in one tenant does
    not carry into another. The one exception is ``SUPER_ADMIN`` — a global
    platform role whose ``UserRole`` row carries ``tenant=NULL``; it is checked
    with :meth:`is_super_admin` (never through the tenant-scoped path).

    Inside a community, the on-site roles are held **per event**
    (``Role.is_per_event``). A check counts the community-wide grants plus the
    grants in one event: ``event`` names it, a check about a row passes the
    row's event (a match's, a tournament's), and the default is the event in
    scope, so a page checks the event it shows. With no event in scope, or
    ``RoleScope.LIVE``, a grant in any live event counts: that is how a
    community-wide page (Equipment) admits its event-held role. A grant in an
    archived event can still read that event, but its rows refuse every write.

    A super-admin has **full authority inside every tenant**, implemented once in
    :meth:`is_staff`: STAFF is the override term in essentially every
    ``can_*``/``ensure_*`` helper below, so widening it there carries the platform
    role through the whole policy surface instead of scattering
    ``is_super_admin`` across dozens of call sites. Two deliberate limits:

    * ``get_roles``/``has_role`` stay literal — they answer "which grants does
      this user hold *here*", which is what role-management UI displays and what
      per-role (not staff-equivalent) surfaces key off.
    * Feature flags are **not** bypassed. Authority is over what a community has
      turned on; a flag that is off hides its subsystem from the super-admin too,
      so they never operate a feature the tenant has not enabled.
    """

    @staticmethod
    def is_system(user: Optional[User]) -> bool:
        """The reserved automation actor (``User.is_system``).

        A field check, not a role query — the system user is trusted
        infrastructure that acts within a ``tenant_scope`` and is authorized for
        automation actions regardless of any granted roles. Gated helpers below
        short-circuit on it so workers/bots never hit a ``PermissionError``.
        """
        return user is not None and getattr(user, 'is_system', False)

    @staticmethod
    async def is_super_admin(user: Optional[User]) -> bool:
        """The global platform role (``UserRole`` with ``tenant=NULL``)."""
        if user is None:
            return False
        return await UserRole.filter(user=user, role=Role.SUPER_ADMIN, tenant=None, event_id=None).exists()

    @staticmethod
    async def get_roles(user: Optional[User], event: EventChoice = None) -> set[Role]:
        """The user's roles **in the current tenant** (excludes SUPER_ADMIN):
        the community-wide ones plus those held in ``event``."""
        if user is None:
            return set()
        tid = get_current_tenant_id()
        if tid is None:
            # No tenant in context (platform surface) — no tenant roles apply.
            return set()
        rows = await UserRole.filter(role_event_q(event), user=user, tenant_id=tid).values_list('role', flat=True)
        return {Role(r) for r in rows}

    @staticmethod
    async def has_role(user: Optional[User], role: Role, event: EventChoice = None) -> bool:
        if user is None:
            return False
        tid = get_current_tenant_id()
        if tid is None:
            return False
        return await UserRole.filter(role_event_q(event), user=user, role=role, tenant_id=tid).exists()

    @staticmethod
    async def event_ids_with_grants(user: Optional[User]) -> set[int]:
        """The current community's events ``user`` holds a per-event role in."""
        if user is None or get_current_tenant_id() is None:
            return set()
        from application.repositories.user_role_repository import UserRoleRepository

        return await UserRoleRepository.event_ids_for_user(user)

    @staticmethod
    async def holds_any_role(user: Optional[User]) -> bool:
        """Whether ``user`` holds any role here that still counts: community-wide,
        or in a live or draft event. The "a role implies membership" side."""
        return bool(await AuthService.get_roles(user, RoleScope.OPEN))

    @staticmethod
    async def is_staff(user: Optional[User]) -> bool:
        """Holds STAFF in the current tenant, or is the global super-admin.

        The single place the platform role becomes in-tenant authority — see the
        class docstring. Callers that need the literal grant (role management,
        "which roles does this user hold") must use ``has_role``/``get_roles``.
        """
        if await AuthService.is_super_admin(user):
            return True
        return await AuthService.has_role(user, Role.STAFF)

    @staticmethod
    async def is_proctor(user: Optional[User], event: EventChoice = None) -> bool:
        return await AuthService.has_role(user, Role.PROCTOR, event)

    @staticmethod
    async def is_stream_manager(user: Optional[User], event: EventChoice = None) -> bool:
        return await AuthService.has_role(user, Role.STREAM_MANAGER, event)

    @staticmethod
    async def is_triforce_submitter(user: Optional[User], event: EventChoice = None) -> bool:
        return await AuthService.has_role(user, Role.TRIFORCE_SUBMITTER, event)

    @staticmethod
    async def can_submit_triforce_text(user: Optional[User], tournament: Tournament) -> bool:
        """Submitting requires the paid Triforce Submitter role (staff override),
        and the tournament must be active with a generator that supports texts."""
        from application.services.seedgen_service import SeedGenerationService

        if not tournament.is_active:
            return False
        if not SeedGenerationService.supports_triforce_texts(tournament.seed_generator):
            return False
        return await AuthService.is_staff(user) or await AuthService.is_triforce_submitter(
            user, getattr(tournament, 'event_id', None),
        )

    @staticmethod
    async def is_tournament_admin(user: Optional[User], tournament_id: int) -> bool:
        # PKs are global, so filter by tenant too: a same-id tournament in another
        # tenant must not satisfy the check.
        if user is None:
            return False
        query = Tournament.filter(id=tournament_id, admins__id=user.id)
        tid = get_current_tenant_id()
        if tid is not None:
            query = query.filter(tenant_id=tid)
        return await query.exists()

    @staticmethod
    async def is_crew_coordinator_of(user: Optional[User], tournament_id: int) -> bool:
        if user is None:
            return False
        query = Tournament.filter(id=tournament_id, crew_coordinators__id=user.id)
        tid = get_current_tenant_id()
        if tid is not None:
            query = query.filter(tenant_id=tid)
        return await query.exists()

    @staticmethod
    async def can_view_admin(user: Optional[User]) -> bool:
        """Staff, or a TA/CC of any tournament in this tenant: the people the
        Reports tab and the per-tournament admin reads are for. A platform
        SUPER_ADMIN passes everywhere.

        Gates the admin REST API, the MCP admin scope, the reports and
        ``audience: staff`` help. The other admin roles each own one or two
        Admin tabs and are held to those tabs' own gates instead
        (``can_manage_volunteers``, ``can_manage_equipment``, ``can_manage_stages``…),
        so none of them reads the community's reports or crew lists through it.
        """
        if user is None:
            return False
        if await AuthService.is_staff(user):
            return True
        admin_q, cc_q = AuthService._tournament_relations(user)
        if await admin_q.exists():
            return True
        return await cc_q.exists()

    @staticmethod
    async def can_use_staff_scopes(user: Optional[User]) -> bool:
        """Whether a consent screen offers ``staff:*`` here: any admin-side role
        (staff, a tournament admin or crew coordinator, or one of the narrower
        admin roles). A plain volunteer is not one."""
        if await AuthService.can_view_admin(user):
            return True
        return bool(await AuthService.get_roles(user) - {Role.VOLUNTEER})

    @staticmethod
    async def can_view_volunteer_admin(user: Optional[User]) -> bool:
        """The admin-side volunteer reads: positions, shifts, coverage and hours.

        ``can_view_admin`` (whose Reports tab carries Volunteer Coverage and the
        hours insight) plus a volunteer coordinator, who manages exactly these
        in Admin → Volunteers and Volunteer Shifts.
        """
        return (
            await AuthService.can_view_admin(user)
            or await AuthService.can_manage_volunteers(user)
        )

    @staticmethod
    async def can_open_admin(user: Optional[User]) -> bool:
        """Whether ``/admin`` has at least one tab for ``user`` here: the nav's gate.

        Deliberately not ``can_view_admin``. That one also admits callers to the
        admin REST API, the MCP admin scope, the reports and ``audience: staff``
        help, none of which the online-play roles should gain. Those roles each
        own one or two Admin surfaces, and this mirrors the tab gates in
        ``pages/admin.py`` (``build_admin_tabs`` / ``build_settings_panes``),
        flags included, so the link is offered exactly when the page would show
        something rather than an empty drawer.
        """
        if user is None:
            return False
        if await AuthService.is_super_admin(user):
            return True
        roles = await AuthService.get_roles(user)
        # Schedule has no flag: staff, a broadcast tech, a TA and a CC always land on it.
        if roles & {Role.STAFF, Role.STREAM_MANAGER}:
            return True
        admin_q, cc_q = AuthService._tournament_relations(user)
        if await admin_q.exists() or await cc_q.exists():
            return True
        # Presets and Settings → Integrations/Discord carry no flag either.
        if roles & {Role.PRESET_MANAGER, Role.SYNC_ADMIN}:
            return True
        from application.services.feature_flag_service import FeatureFlagService
        from models import FeatureFlag

        live = await FeatureFlagService().enabled_flags()
        if Role.VOLUNTEER_COORDINATOR in roles and FeatureFlag.VOLUNTEERS in live:
            return True
        if FeatureFlag.EQUIPMENT in live and await AuthService.is_equipment_manager(user):
            return True
        if FeatureFlag.ASYNC_QUALIFIERS not in live:
            return False
        if Role.QUALIFIER_ADMIN in roles:
            return True
        query = user.admin_async_qualifiers.all()  # type: ignore[attr-defined]
        tid = get_current_tenant_id()
        if tid is not None:
            query = query.filter(tenant_id=tid)
        return await query.exists()

    @staticmethod
    def _tournament_relations(user: User) -> tuple[Any, Any]:
        """This tenant's slice of ``user``'s TA and CC tournament relations.

        ``user.admin_tournaments`` / ``crew_coordinated_tournaments`` hang off the
        *global* ``User``, so unfiltered they carry a grant made in one community
        into every other one.
        """
        tid = get_current_tenant_id()
        admin_q = user.admin_tournaments.all()
        cc_q = user.crew_coordinated_tournaments.all()
        if tid is not None:
            admin_q = admin_q.filter(tenant_id=tid)
            cc_q = cc_q.filter(tenant_id=tid)
        return admin_q, cc_q

    @staticmethod
    async def tournament_scope(user: Optional[User]) -> tuple[set[int], set[int]]:
        """``(admin_ids, coordinated_ids)`` — the tournaments ``user`` operates here.

        The *ids*, not just whether any exist, because a non-staff operator's
        surfaces should be scoped to them: a crew coordinator of one tournament
        has no business reading the whole community's board. Staff and the
        globally-scoped roles (STREAM_MANAGER) are deliberately not expressed
        here — their authority is community-wide, so they have no id set.
        """
        if user is None:
            return set(), set()
        admin_q, cc_q = AuthService._tournament_relations(user)
        return (
            {t.id for t in await admin_q},
            {t.id for t in await cc_q},
        )

    @staticmethod
    async def can_view_schedule_board(user: Optional[User]) -> bool:
        """Whether the admin Schedule tab is reachable for ``user``.

        Staff, a STREAM_MANAGER, any tournament admin, or any crew coordinator in
        this tenant — the same predicates ``pages/admin.py`` appends the Schedule
        tab on. The reports resolve their drill-out links against this so they
        never offer a route the destination would refuse. (What the board *lets*
        them do once there is a separate question, answered per capability by
        ``MatchBoardAccess``.)
        """
        if user is None:
            return False
        if await AuthService.is_staff(user):
            return True
        if await AuthService.has_role(user, Role.STREAM_MANAGER):
            return True
        admin_q, cc_q = AuthService._tournament_relations(user)
        if await admin_q.exists():
            return True
        return await cc_q.exists()

    @staticmethod
    async def schedule_board_scope(user: Optional[User]) -> Optional[frozenset[int]]:
        """The tournaments whose matches ``user`` can open on the admin Schedule board.

        ``None`` means every match (staff, STREAM_MANAGER); a set means only those
        tournaments' matches, because a TA/CC's board is narrowed to what they
        operate (``pages/admin.py``). An empty set means no board at all. A
        per-match "Open in Schedule" link checks this so it never lands someone
        on a board that cannot show that match.
        """
        if user is None:
            return frozenset()
        if await AuthService.is_staff(user) or await AuthService.has_role(user, Role.STREAM_MANAGER):
            return None
        admin_ids, cc_ids = await AuthService.tournament_scope(user)
        return frozenset(admin_ids | cc_ids)

    @staticmethod
    async def report_tournament_scope(user: Optional[User]) -> Optional[frozenset[int]]:
        """The tournaments whose data ``user`` reads in a tournament-scoped report.

        ``None`` means the whole community (staff, the system actor). A
        tournament admin or crew coordinator gets only the tournaments they run.
        """
        if AuthService.is_system(user) or await AuthService.is_staff(user):
            return None
        admin_ids, cc_ids = await AuthService.tournament_scope(user)
        return frozenset(admin_ids | cc_ids)

    # Roles that have something to do on the Volunteer hub's volunteer tabs. The
    # hub's ``roles=`` also admits CHECK_IN_DESK, for its Check-in Desk tab,
    # which :meth:`can_view_check_in_desk` decides. A volunteer coordinator
    # builds the schedule in Admin, so they read the published one here too.
    _VOLUNTEER_ROLES: ClassVar[set[Role]] = {
        Role.VOLUNTEER, Role.PROCTOR, Role.STAFF, Role.VOLUNTEER_COORDINATOR,
    }

    @staticmethod
    async def can_view_volunteer(user: Optional[User]) -> bool:
        """Whether the Volunteer hub is reachable for ``user`` in this tenant.

        The nav's single source of truth, so the header link and the page gate
        cannot drift: previously every layout offered "Volunteer" to any signed-in
        user, which dead-ended on a 404 where the tenant had the feature off and a
        403 where the user held none of the roles.

        Reads the feature flag (lazily imported — ``FeatureFlagService`` imports
        this module), which is a gate at an entry surface, not a service
        transaction.
        """
        if user is None:
            return False
        from application.services.feature_flag_service import FeatureFlagService
        from models import FeatureFlag

        if not await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS):
            return False
        if await AuthService.is_super_admin(user):
            return True
        return bool(await AuthService.get_roles(user) & AuthService._VOLUNTEER_ROLES)

    @staticmethod
    async def can_open_proctor_station(user: Optional[User]) -> bool:
        """Whether the Volunteer hub's Proctor Station tab is offered to ``user``.

        A proctor or a Broadcast Tech gets it whether or not VOLUNTEERS is on:
        ``can_run_match`` admits them with no flag, because running matches isn't
        volunteer scheduling. A Broadcast Tech's board is narrowed to matches on
        a stage (:meth:`proctor_station_on_stage_only`). Staff run matches from
        the Admin board, so for them it follows the hub as before.
        """
        if user is None:
            return False
        if await AuthService.get_roles(user) & {Role.PROCTOR, Role.STREAM_MANAGER}:
            return True
        return await AuthService.is_staff(user) and await AuthService.can_view_volunteer(user)

    @staticmethod
    async def proctor_station_on_stage_only(user: Optional[User]) -> bool:
        """Whether ``user``'s Proctor Station lists only matches on a stage.

        True for a Broadcast Tech who is neither staff nor a proctor:
        ``can_run_match`` lets them run a match only while it is on a stage, so
        the rest of the room is left off their board rather than shown as rows
        they can't act on.
        """
        if user is None or await AuthService.is_staff(user):
            return False
        roles = await AuthService.get_roles(user)
        return Role.STREAM_MANAGER in roles and Role.PROCTOR not in roles

    @staticmethod
    async def can_view_volunteer_schedule(user: Optional[User]) -> bool:
        """Read who works which shift: the Volunteer hub's people, plus the
        coordinators who run the schedule from Admin."""
        return (
            await AuthService.can_view_volunteer(user)
            or await AuthService.can_manage_volunteers(user)
        )

    @staticmethod
    async def can_edit_tournament(user: Optional[User], tournament: Tournament) -> bool:
        if await AuthService.is_staff(user):
            return True
        return await AuthService.is_tournament_admin(user, tournament.id)

    @staticmethod
    async def can_manage_payouts(user: Optional[User], tournament: Tournament) -> bool:
        """Read and edit one tournament's prize split.

        The same people ``can_edit_tournament`` admits — staff, a super-admin,
        or this tournament's admin — because the tournament admin is who the
        event's organizer asks for the payout block today. Reads are gated
        alongside writes: an unannounced split is admin data.
        """
        if await AuthService.is_staff(user):
            return True
        return await AuthService.is_tournament_admin(user, tournament.id)

    @staticmethod
    async def can_crud_match(user: Optional[User], match: Match) -> bool:
        if await AuthService.is_staff(user):
            return True
        return await AuthService.is_tournament_admin(user, match.tournament_id)

    @staticmethod
    async def can_run_match(user: Optional[User], match: Match) -> bool:
        """Run a match on the floor: seat, start, finish, record the result,
        roll its seed, assign stations.

        Admits PROCTOR — this is the proctor's whole job — and a Broadcast Tech
        (STREAM_MANAGER) for a match on a stage, since running the broadcast
        match is theirs. Confirming is deliberately *not* here; see
        ``can_confirm_match``.
        """
        if AuthService.is_system(user):
            return True
        event_id = getattr(match, 'event_id', None)
        if await AuthService.is_staff(user) or await AuthService.is_proctor(user, event_id):
            return True
        if match.stage_id is not None and await AuthService.is_stream_manager(user, event_id):  # type: ignore[attr-defined]
            return True
        return await AuthService.is_tournament_admin(user, match.tournament_id)

    @staticmethod
    async def can_confirm_match(user: Optional[User], match: Match) -> bool:
        """Officially record a result — staff or the tournament's admin only.

        Excludes PROCTOR by design: confirming advances the native bracket and
        pushes to Challonge, which is the admin's verification step. A proctor's
        workflow ends at recording the winner.
        """
        if await AuthService.is_staff(user):
            return True
        return await AuthService.is_tournament_admin(user, match.tournament_id)

    @staticmethod
    async def can_approve_crew(user: Optional[User], match: Match) -> bool:
        if await AuthService.is_staff(user):
            return True
        if await AuthService.is_tournament_admin(user, match.tournament_id):
            return True
        return await AuthService.is_crew_coordinator_of(user, match.tournament_id)

    @staticmethod
    async def can_manage_stages(user: Optional[User], event: EventChoice = None) -> bool:
        """CRUD on Stage records (the stages themselves), in ``event``."""
        return await AuthService.is_staff(user) or await AuthService.is_stream_manager(user, event)

    @staticmethod
    async def is_volunteer_coordinator(user: Optional[User], event: EventChoice = None) -> bool:
        return await AuthService.has_role(user, Role.VOLUNTEER_COORDINATOR, event)

    @staticmethod
    async def can_manage_volunteers(user: Optional[User], event: EventChoice = None) -> bool:
        """Manage volunteer positions, shifts, and assignments (admin side)."""
        return await AuthService.is_staff(user) or await AuthService.is_volunteer_coordinator(user, event)

    @staticmethod
    async def is_equipment_manager(user: Optional[User]) -> bool:
        """Equipment is the community's, not one event's, so the role held in
        any live event counts."""
        return await AuthService.has_role(user, Role.EQUIPMENT_MANAGER, RoleScope.LIVE)

    @staticmethod
    async def is_volunteer(user: Optional[User], event: EventChoice = None) -> bool:
        return await AuthService.has_role(user, Role.VOLUNTEER, event)

    @staticmethod
    async def can_manage_equipment(user: Optional[User]) -> bool:
        """Create/edit/delete lending assets and view private notes/owner."""
        return await AuthService.is_staff(user) or await AuthService.is_equipment_manager(user)

    @staticmethod
    async def can_checkout_equipment(user: Optional[User]) -> bool:
        """Borrow an asset. Any signed-in member may, **to themselves only**.

        Deliberately role-less: a loaner device's QR code is scanned by whoever
        is holding it, and at a live event that is usually a player with no roles
        at all. Checking out *on behalf of* someone else is the manager action
        and stays on :meth:`can_manage_equipment` — ``EquipmentService.checkout``
        is where the two split. The system account is excluded for the same
        reason it is excluded from the borrower picker.
        """
        if user is None or not user.is_active:
            return False
        return str(user.discord_id) != str(SYSTEM_USER_DISCORD_ID)

    @staticmethod
    async def can_checkin_equipment(user: Optional[User]) -> bool:
        """Check equipment back in."""
        return await AuthService.can_manage_equipment(user)

    @staticmethod
    async def is_check_in_desk(user: Optional[User], event: EventChoice = None) -> bool:
        return await AuthService.has_role(user, Role.CHECK_IN_DESK, event)

    @staticmethod
    async def can_manage_check_in(user: Optional[User]) -> bool:
        """Set up check-in events, add walk-ups, and remove roster rows."""
        return await AuthService.is_staff(user)

    @staticmethod
    async def can_run_check_in_desk(user: Optional[User]) -> bool:
        """Work the desk: check people in, link registrants, run a sync."""
        if AuthService.is_system(user):
            return True
        return await AuthService.is_staff(user) or await AuthService.is_check_in_desk(user)

    @staticmethod
    async def can_view_check_in_desk(user: Optional[User]) -> bool:
        """Whether the check-in desk is reachable for ``user`` in this tenant.

        The nav's source of truth, so the header link and the page gate agree:
        the feature must be live and the viewer must be able to work the desk.
        Reads the flag lazily, as :meth:`can_view_volunteer` does.
        """
        if user is None:
            return False
        from application.services.feature_flag_service import FeatureFlagService
        from models import FeatureFlag

        if not await FeatureFlagService().is_enabled(FeatureFlag.EVENT_CHECK_IN):
            return False
        return await AuthService.can_run_check_in_desk(user)

    @staticmethod
    async def can_assign_match_stream(user: Optional[User], match: Match) -> bool:
        """Set a match's stage or is_stream_candidate flag.
        Broadcast Techs (STREAM_MANAGER) can do this globally; TAs can do it for their own tournaments.
        """
        if await AuthService.can_manage_stages(user, getattr(match, 'event_id', None)):
            return True
        return await AuthService.is_tournament_admin(user, match.tournament_id)

    @staticmethod
    async def _system_admin_staff_or(user: Optional[User], role: Role) -> bool:
        """Shared cascade: system actor, super-admin, staff, or the given role.

        The common ``is_system → is_super_admin → is_staff → has_role`` ladder used
        by the gated ``can_manage_*`` / ``can_admin_*`` helpers.
        """
        if AuthService.is_system(user):
            return True
        if await AuthService.is_super_admin(user):
            return True
        if await AuthService.is_staff(user):
            return True
        return await AuthService.has_role(user, role)

    @staticmethod
    async def can_manage_presets(user: Optional[User]) -> bool:
        """Author/edit the tenant's seed-rolling presets (PR 1+)."""
        return await AuthService._system_admin_staff_or(user, Role.PRESET_MANAGER)

    @staticmethod
    async def can_manage_sync(user: Optional[User]) -> bool:
        """Manage upstream sync config: SpeedGaming links, Discord events, and
        racetime bot/room configuration (PR 3+/7+)."""
        return await AuthService._system_admin_staff_or(user, Role.SYNC_ADMIN)

    @staticmethod
    async def can_admin_qualifier(user: Optional[User], qualifier: Optional[Any] = None) -> bool:
        """Administer async qualifiers (PR 9+).

        Grants to STAFF/super-admin, the global ``QUALIFIER_ADMIN`` role, or — when
        a specific ``qualifier`` is supplied — a per-entity admin listed on its
        ``admins`` M2M. The per-entity path is forward-compatible: the
        ``AsyncQualifier`` model lands in a later PR, so this only dereferences
        ``qualifier.admins`` when a qualifier is actually passed.
        """
        if await AuthService._system_admin_staff_or(user, Role.QUALIFIER_ADMIN):
            return True
        if qualifier is not None and user is not None:
            return await qualifier.admins.filter(id=user.id).exists()
        return False

    @staticmethod
    async def can_grant_roles(user: Optional[User]) -> bool:
        """Grant/revoke UserRole rows and Tournament.admins / crew_coordinators."""
        return await AuthService.is_staff(user)

    @staticmethod
    async def ensure(allowed: bool, message: str = "Permission denied") -> None:
        if not allowed:
            raise PermissionError(message)

    @staticmethod
    async def ensure_super_admin(user: Optional[User]) -> None:
        """Raise ``PermissionError`` unless ``user`` is the global super-admin."""
        await AuthService.ensure(
            await AuthService.is_super_admin(user),
            "Super-admin privileges required",
        )

    @staticmethod
    async def ensure_can_manage_presets(user: Optional[User]) -> None:
        """Raise ``PermissionError`` unless ``user`` may manage seed presets."""
        await AuthService.ensure(
            await AuthService.can_manage_presets(user),
            "You do not have permission to manage presets",
        )

    @staticmethod
    async def ensure_can_roll_free_seed(user: Optional[User]) -> None:
        """Raise ``PermissionError`` unless ``user`` may roll a seed outside any
        match on the community's randomizer keys: the people Seed Presets' Test
        roll is offered to. A match's seed has its own gate (``can_run_match``)."""
        await AuthService.ensure(
            await AuthService.can_manage_presets(user),
            "Only staff and preset managers can roll a seed outside a match.",
        )

    @staticmethod
    async def ensure_can_manage_sync(user: Optional[User]) -> None:
        """Raise ``PermissionError`` unless ``user`` may manage sync config."""
        await AuthService.ensure(
            await AuthService.can_manage_sync(user),
            "You do not have permission to manage sync configuration",
        )

    @staticmethod
    async def ensure_can_admin_qualifier(
        user: Optional[User], qualifier: Optional[Any] = None
    ) -> None:
        """Raise ``PermissionError`` unless ``user`` may administer qualifiers."""
        await AuthService.ensure(
            await AuthService.can_admin_qualifier(user, qualifier),
            "You do not have permission to administer this qualifier",
        )


async def get_user_from_discord_id(discord_id: str | None) -> Optional[User]:
    """Resolve a discord_id (from app.storage.user) to a User model.

    Returns None when discord_id is absent, the user is no longer in the
    database, or the account has been deactivated (``is_active`` is False) —
    so a deactivated user loses page/role access on their next request, the
    same way the REST API already rejects them.
    Call once at page entry and pass the result into AuthService helpers.
    """
    if not discord_id:
        return None
    user = await User.get_or_none(discord_id=discord_id)
    if user is not None and not user.is_active:
        return None
    return user


#: Session keys every sign-in writes into ``app.storage.user``. ``user_id`` is the
#: identity; ``discord_id`` stays for Discord sessions (and sessions from before
#: ``user_id`` existed). A password session also carries ``auth_method`` and
#: ``password_stamp``, so changing or removing the password ends it.
SESSION_USER_ID = 'user_id'
SESSION_AUTH_METHOD = 'auth_method'
SESSION_PASSWORD_STAMP_FIELD = 'password_stamp'
AUTH_METHOD_PASSWORD = 'password'


def password_session_stamp(user: User) -> Optional[str]:
    """The value a password session records; a password change or removal moves it."""
    return user.password_changed_at.isoformat() if user.password_changed_at else None


async def get_user_from_session(storage: Any) -> Optional[User]:
    """Resolve the signed-in :class:`User` from a session mapping (``app.storage.user``).

    The one way presentation code learns who is signed in. ``None`` for a
    signed-out session, a missing or deactivated account, or a password session
    whose password has since been changed or removed. A session from before
    ``user_id`` was recorded resolves by its Discord id and gains ``user_id``.
    """
    if not storage:
        return None
    user_id = storage.get(SESSION_USER_ID)
    if not user_id:
        user = await get_user_from_discord_id(storage.get('discord_id'))
        if user is not None:
            try:
                storage[SESSION_USER_ID] = user.id
            except Exception:  # a read-only mapping, or storage written outside its context
                pass
        return user
    user = await User.get_or_none(id=user_id)
    if user is None or not user.is_active:
        return None
    if storage.get(SESSION_AUTH_METHOD) == AUTH_METHOD_PASSWORD and (
        not user.password_hash
        or storage.get(SESSION_PASSWORD_STAMP_FIELD) != password_session_stamp(user)
        # Made a platform admin after signing in by password: that session
        # can't carry the role (see PasswordAuthService.authenticate).
        or await AuthService.is_super_admin(user)
    ):
        return None
    return user


def session_user_id(storage: Any) -> Optional[int]:
    """The signed-in account's id, without a query (for "is this row mine?")."""
    try:
        value = storage.get(SESSION_USER_ID) if storage else None
        return int(value) if value else None
    except (TypeError, ValueError):
        return None
