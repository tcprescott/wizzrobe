"""Tenant Membership Service - Business Logic Layer

Who belongs to a community, and who may change that.

Membership is the **wider set**: every role-holder is a member, not every member
holds a role. That invariant is enforced where roles are written
(``UserService.grant_role``, the Discord role sync) and defended here — removing
a member who still holds roles is refused rather than cascading a revoke.

``TenantMembership`` is exempt from ``check_tenant_scoping`` (cross-tenant by
nature: the row *is* the tenant linkage), so its queries pass tenant ids
explicitly rather than going through ``scoped(...)``.
"""

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Iterable, List, Optional

from application.errors import NotFoundError
from application.events import Event, EventType, event_bus
from application.repositories.api_token_repository import ApiTokenRepository
from application.repositories.tenant_join_request_repository import TenantJoinRequestRepository
from application.repositories.tenant_membership_repository import TenantMembershipRepository
from application.repositories.user_repository import UserRepository
from application.repositories.user_role_repository import UserRoleRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import require_tenant_id, tenant_scope
from application.utils.discord_embeds import (
    COLOR_CANCELLED,
    COLOR_CONFIRMED,
    COLOR_JOIN_REQUEST,
    notification_embed,
)
from application.utils.discord_messages_tenant import (
    join_decided_dm,
    join_requested_dm,
    member_added_dm,
    member_removed_dm,
)
from models import (
    JoinRequestStatus,
    MembershipSource,
    Role,
    StaffRequestKind,
    TenantJoinRequest,
    TenantMembership,
    User,
)

logger = logging.getLogger(__name__)

#: How long a declined requester waits before the door takes another request.
#: Without it, request → decline → request again took ten seconds and DM'd
#: every staff member each time.
JOIN_REQUEST_COOLDOWN = timedelta(days=7)


class JoinMode(str, Enum):
    """Who can become a member of a community, as one choice.

    Stored as three booleans (``KEY_OPEN_MEMBERSHIP``, ``KEY_DISCORD_AUTO_JOIN``,
    ``KEY_JOIN_REQUESTS``) that the door and the services already read; this is
    the only shape staff are offered, so the three can't be set into a
    combination nobody meant (open *and* closed to requests, say).
    """

    OPEN = 'open'                            # anyone who signs in
    DISCORD_OR_REQUEST = 'discord_or_request'  # server members in; others ask
    DISCORD = 'discord'                      # server members only
    REQUEST = 'request'                      # everyone asks staff
    STAFF_ONLY = 'staff_only'                # staff add people by hand

    @property
    def flags(self) -> tuple[bool, bool, bool]:
        """``(open, discord_auto_join, join_requests)`` for this mode."""
        return {
            JoinMode.OPEN: (True, False, False),
            JoinMode.DISCORD_OR_REQUEST: (False, True, True),
            JoinMode.DISCORD: (False, True, False),
            JoinMode.REQUEST: (False, False, True),
            JoinMode.STAFF_ONLY: (False, False, False),
        }[self]

    @classmethod
    def from_flags(cls, open_: bool, auto_join: bool, requests: bool) -> 'JoinMode':
        if open_:
            return cls.OPEN
        if auto_join:
            return cls.DISCORD_OR_REQUEST if requests else cls.DISCORD
        return cls.REQUEST if requests else cls.STAFF_ONLY

    @property
    def uses_discord(self) -> bool:
        return self in (JoinMode.DISCORD_OR_REQUEST, JoinMode.DISCORD)


async def close_pending_request(
    user: User, tenant_id: int, decided_by: Optional[User],
) -> Optional[TenantJoinRequest]:
    """Close ``user``'s pending request here as approved, if they have one.

    For every way into a community other than the queue itself: someone let in
    by Add Member, a role grant or Discord would otherwise sit in the queue,
    where a Decline would tell a member they weren't approved. ``decided_by``
    is the staff member who let them in, or ``None`` when nobody did.

    The approval is published as ``TENANT_JOIN_APPROVED`` here, bare: the
    audit row belongs to whichever path let them in (``tenant.member_added``,
    ``user.role_granted``…), and carries ``closed_request_id``.
    """
    request = await TenantJoinRequestRepository.get(user.id, tenant_id)
    if request is None or request.status is not JoinRequestStatus.PENDING:
        return None
    await TenantJoinRequestRepository.decide(
        request, JoinRequestStatus.APPROVED, decided_by, datetime.now(timezone.utc),
    )
    event_bus.publish(Event.create(
        EventType.TENANT_JOIN_APPROVED,
        {'target_user_id': user.id, 'request_id': request.id, 'closed_by_membership': True},
        decided_by,
    ))
    with tenant_scope(tenant_id):
        await _mark_staff_dms(request, user, decided_by, approved=True)
    return request


async def _mark_staff_dms(
    request: TenantJoinRequest, requester: User, decided_by: Optional[User], *, approved: bool,
) -> None:
    """Edit the join-request DMs every staff member got to say it's been decided.

    ``decided_by`` is ``None`` when the requester got in some other way.
    """
    try:
        from application.services import notification_links, staff_request_dms
        from application.services.tenant_service import TenantService
        from application.utils import discord_messages_staff_requests as copy

        community = await TenantService.current_community_name()
        name = requester.display_name or requester.username
        staff_name = decided_by.preferred_name if decided_by else None
        when = datetime.now(timezone.utc)

        def build(you: bool):
            if approved:
                card = copy.join_approved(you, staff_name, name, community, when)
            else:
                card = copy.join_declined(you, staff_name or '', name, when)
            return staff_request_dms.card(
                card, color=COLOR_CONFIRMED if approved else COLOR_CANCELLED,
                community_name=community,
            )

        link = await notification_links.admin_users(label='Open the Users tab')
        staff_request_dms.mark_handled(
            StaffRequestKind.JOIN_REQUEST, request.id, build, link,
            actor_id=decided_by.id if decided_by else None,
        )
    except Exception:
        logger.exception('join request %s: staff DM edit failed', request.id)


class TenantMembershipService:
    """Membership reads and writes for the tenant in scope."""

    def __init__(self):
        self.audit_service = AuditService()

    async def list_members(self) -> List[User]:
        """Everyone who belongs to the tenant in scope, by username."""
        rows = await TenantMembershipRepository.list_for_tenant(require_tenant_id())
        return sorted((m.user for m in rows), key=lambda u: (u.username or '').lower())

    async def memberships_by_user(self) -> Dict[int, TenantMembership]:
        """This community's membership rows keyed by user id: when and how each joined."""
        rows = await TenantMembershipRepository.list_for_tenant(require_tenant_id())
        return {m.user_id: m for m in rows}

    async def addable_users(self) -> List[User]:
        """Accounts that could be added to this community but are not in it yet.

        Deliberately reads the *global* account list: identity is shared across
        communities, so "add an existing person to my community" has to be able
        to see people who are not here yet. That makes it the one person-picker
        that is legitimately platform-wide — which is why it lives here, named
        for what it does, rather than as a bare ``get_all_users()`` call in the
        page.
        """
        members = {u.id for u in await self.list_members()}
        return [
            u for u in await UserRepository.get_all()
            if u.id not in members and not u.is_system
        ]

    async def is_member(self, user: User) -> bool:
        return await TenantMembershipRepository.is_member(user.id, require_tenant_id())

    async def add_member(self, actor: User, user: User) -> None:
        """Put a user in this community. Idempotent."""
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            'Only Staff can manage community membership',
        )
        tenant_id = require_tenant_id()
        if await TenantMembershipRepository.is_member(user.id, tenant_id):
            return
        await TenantMembershipRepository.add(user, tenant_id, MembershipSource.STAFF)
        await TenantMembershipRepository.unblock(user.id, tenant_id)
        closed = await close_pending_request(user, tenant_id, actor)
        details: dict = {'target_user_id': user.id}
        if closed is not None:
            details['closed_request_id'] = closed.id
        await self.audit_service.write_and_publish(
            actor, AuditActions.TENANT_MEMBER_ADDED, details, EventType.TENANT_MEMBER_ADDED,
        )
        await self._notify_member(user, member_added_dm, by_staff=True)

    async def remove_member(self, actor: User, user: User) -> None:
        """Take a user out of this community.

        Refuses while they hold any role here: removing the membership would
        break the "a role implies membership" invariant from the other side, and
        silently revoking their roles because staff clicked Remove on a table row
        is exactly the kind of invisible side effect this codebase avoids.
        """
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            'Only Staff can manage community membership',
        )
        held = await AuthService.get_roles(user)
        if held:
            raise ValueError(
                f"{user.display_name or user.username} holds roles in this "
                'community — revoke them before removing the membership.'
            )
        # Tournament admin and crew coordinator are roles too, just per
        # tournament. Left in place they come back with any later re-admission.
        admin_ids, coordinated_ids = await AuthService.tournament_scope(user)
        if admin_ids or coordinated_ids:
            raise ValueError(
                f"{user.display_name or user.username} is a tournament admin or crew "
                'coordinator here — take them off those tournaments before removing '
                'the membership.'
            )
        tenant_id = require_tenant_id()
        removed = await TenantMembershipRepository.remove(user.id, tenant_id)
        if not removed:
            raise ValueError('That user is not a member of this community.')
        # Without this, open membership and Discord auto-join undid the removal
        # on the person's next page view.
        await TenantMembershipRepository.block(user.id, tenant_id, actor)
        # A personal API token is bound to one community and carries no role of
        # its own, so without this it kept reading the schedule (every player's
        # Discord id included) long after its owner was shown the door.
        revoked_tokens = await ApiTokenRepository.revoke_for_user_in_tenant(
            user.id, tenant_id, datetime.now(timezone.utc),
        )
        await self.audit_service.write_and_publish(
            actor, AuditActions.TENANT_MEMBER_REMOVED,
            {'target_user_id': user.id, 'revoked_tokens': revoked_tokens},
            EventType.TENANT_MEMBER_REMOVED,
        )
        # Local imports: both services import this module.
        from application.services.accommodation_service import AccommodationService
        await AccommodationService().close_for_removed_member(actor, user)
        from application.services.emergency_contact_service import EmergencyContactService
        await EmergencyContactService().remove_for_removed_member(actor, user)
        await self._notify_member(
            user, member_removed_dm,
            can_ask=await self.get_join_mode(tenant_id) in (
                JoinMode.DISCORD_OR_REQUEST, JoinMode.REQUEST,
            ),
        )

    # ---- the door: requests to join --------------------------------------

    async def request_to_join(
        self, user: User, tenant_id: int, message: Optional[str] = None,
    ) -> TenantJoinRequest:
        """Ask to join a community you are not in.

        Takes an explicit ``tenant_id``: this is called from a page the
        requester is *not yet a member of*, so it must not depend on anything
        membership-scoped.

        Refused when staff turned ``KEY_JOIN_REQUESTS`` off. The door hides its
        button then, but a page left open from before the switch still has one.
        Requests already pending are untouched: staff can still decide them.
        """
        from application.services.system_config_service import (
            KEY_JOIN_REQUESTS,
            SystemConfigService,
        )

        if await TenantMembershipRepository.is_member(user.id, tenant_id):
            raise ValueError('You are already a member of this community.')
        with tenant_scope(tenant_id):
            if not await SystemConfigService.get_bool(KEY_JOIN_REQUESTS, default=True):
                raise ValueError('This community isn’t taking join requests right now.')
        text = (message or '').strip() or None
        if text and len(text) > 500:
            raise ValueError("That message is a bit long. Keep it under 500 characters.")
        allowed_at = await self.next_request_allowed_at(user, tenant_id)
        if allowed_at is not None:
            from application.utils.timezone import format_local_display

            raise ValueError(
                'Staff declined your last request recently, so you can’t ask again '
                f'until {format_local_display(allowed_at)}.'
            )

        existing = await TenantJoinRequestRepository.get(user.id, tenant_id)
        already_pending = existing is not None and existing.status is JoinRequestStatus.PENDING
        request = await TenantJoinRequestRepository.upsert_pending(user, tenant_id, text)
        if already_pending:
            # Staff were told when it was opened. Re-asking only updates the
            # note, or a client repeating the click could DM every staff
            # member as often as it liked.
            return request
        # The requester acts on their own behalf, and the row belongs to the
        # *target* tenant — which they are not scoped to, hence the explicit
        # scope, exactly as bootstrap_staff does.
        with tenant_scope(tenant_id):
            await self.audit_service.write_and_publish(
                user, AuditActions.TENANT_JOIN_REQUESTED,
                {'target_user_id': user.id, 'request_id': request.id},
                EventType.TENANT_JOIN_REQUESTED,
            )
            await self._notify_staff_of_request(user, request, tenant_id, text)
        return request

    # ---- how people get in ----------------------------------------------

    @staticmethod
    async def get_join_mode(tenant_id: Optional[int] = None) -> JoinMode:
        """The community's rule for who can join. Defaults to :attr:`JoinMode.REQUEST`."""
        from application.services.system_config_service import (
            KEY_DISCORD_AUTO_JOIN,
            KEY_JOIN_REQUESTS,
            KEY_OPEN_MEMBERSHIP,
            SystemConfigService,
        )

        with tenant_scope(tenant_id if tenant_id is not None else require_tenant_id()):
            return JoinMode.from_flags(
                await SystemConfigService.get_bool(KEY_OPEN_MEMBERSHIP),
                await SystemConfigService.get_bool(KEY_DISCORD_AUTO_JOIN),
                await SystemConfigService.get_bool(KEY_JOIN_REQUESTS, default=True),
            )

    @staticmethod
    async def set_join_mode(actor: User, mode: JoinMode) -> None:
        """Write all three switches behind ``mode`` for the tenant in scope.

        Staff only (``set_raw`` enforces it). A Discord mode is refused while no
        server is linked: it would close the door to everyone and admit nobody.
        """
        from application.services.system_config_service import (
            KEY_DISCORD_AUTO_JOIN,
            KEY_JOIN_REQUESTS,
            KEY_OPEN_MEMBERSHIP,
            SystemConfigService,
        )
        from application.services.tenant_service import TenantService

        if mode.uses_discord:
            tenant = await TenantService.get_by_id(require_tenant_id())
            if tenant is None or tenant.discord_guild_id is None:
                raise ValueError(
                    'Link a Discord server on the Role Mappings tab before letting '
                    'its members in.'
                )
        open_, auto_join, requests = mode.flags
        for key, value in (
            (KEY_OPEN_MEMBERSHIP, open_),
            (KEY_DISCORD_AUTO_JOIN, auto_join),
            (KEY_JOIN_REQUESTS, requests),
        ):
            await SystemConfigService.set_raw(key, 'true' if value else 'false', actor)

    async def join_openly(self, user: User, tenant_id: int) -> bool:
        """Let anyone who signs in straight in, when the community is open.

        The no-strings sibling of :meth:`join_via_discord`, with the same
        contract: True when ``user`` is a member afterwards, never raises, runs
        inside the membership gate on every page a non-member opens.
        """
        from application.services.system_config_service import (
            KEY_OPEN_MEMBERSHIP,
            SystemConfigService,
        )

        try:
            if await TenantMembershipRepository.is_member(user.id, tenant_id):
                return True
            if await TenantMembershipRepository.is_blocked(user.id, tenant_id):
                return False
            with tenant_scope(tenant_id):
                if not await SystemConfigService.get_bool(KEY_OPEN_MEMBERSHIP):
                    return False
                await TenantMembershipRepository.add(user, tenant_id, MembershipSource.OPEN)
                closed = await close_pending_request(user, tenant_id, None)
                details: dict = {'target_user_id': user.id, 'source': 'open'}
                if closed is not None:
                    details['closed_request_id'] = closed.id
                await self.audit_service.write_and_publish(
                    user, AuditActions.TENANT_MEMBER_ADDED, details,
                    EventType.TENANT_MEMBER_ADDED,
                )
            return True
        except Exception:
            logger.exception('Open join failed for user %s in tenant %s', user.id, tenant_id)
            return False

    async def admit(self, user: User, tenant_id: Optional[int] = None) -> bool:
        """Whether ``user`` is a member, letting them in first if the rule allows.

        For entry points that aren't a page, so never pass the gate: a Discord
        button pressed by someone who has never opened the site. Without it,
        open membership and auto-join both refused exactly the person they
        were meant to let in.
        """
        tid = tenant_id if tenant_id is not None else require_tenant_id()
        if await TenantMembershipRepository.is_member(user.id, tid):
            return True
        if not user.is_active:
            return False
        return await self.join_openly(user, tid) or await self.join_via_discord(user, tid)

    async def join_via_discord(self, user: User, tenant_id: int) -> bool:
        """Let a member of the community's linked Discord server straight in.

        The door's alternative to a join request, live only when staff turned on
        ``KEY_DISCORD_AUTO_JOIN`` and the tenant has a guild. True when ``user``
        is a member afterwards. Explicit ``tenant_id`` for the same reason as
        ``request_to_join``.

        Never raises. It runs inside the membership gate on every page a
        non-member opens, and a Discord outage must leave them at the door with
        its Request access button, not on an error page. A bot that cannot say
        whether they are in the server is treated as "not known to be", never as
        yes.
        """
        from application.services.discord import DiscordService
        from application.services.system_config_service import (
            KEY_DISCORD_AUTO_JOIN,
            SystemConfigService,
        )
        from application.services.tenant_service import TenantService

        if user.discord_id is None:
            return False
        try:
            if await TenantMembershipRepository.is_member(user.id, tenant_id):
                return True
            if await TenantMembershipRepository.is_blocked(user.id, tenant_id):
                return False
            with tenant_scope(tenant_id):
                if not await SystemConfigService.get_bool(KEY_DISCORD_AUTO_JOIN):
                    return False
                tenant = await TenantService.get_by_id(tenant_id)
                if tenant is None or tenant.discord_guild_id is None:
                    return False
                ok, in_guild = await DiscordService().is_guild_member(
                    tenant.discord_guild_id, int(user.discord_id),
                )
                if not ok:
                    logger.warning(
                        'Discord auto-join: guild check failed for tenant %s: %s',
                        tenant_id, in_guild,
                    )
                    return False
                if not in_guild:
                    return False

                await TenantMembershipRepository.add(
                    user, tenant_id, MembershipSource.DISCORD_AUTO_JOIN,
                )
                # Closed as approved with no decider: nobody on staff decided it.
                # The person learns they're in from the page they're opening
                # (the gate shows a welcome), so no DM to them. Staff aren't
                # DM'd either: nothing needs doing, and the Users tab marks the
                # member "Joined via Discord".
                closed = await close_pending_request(user, tenant_id, None)
                details: dict = {'target_user_id': user.id, 'source': 'discord_auto_join'}
                if closed is not None:
                    details['closed_request_id'] = closed.id
                await self.audit_service.write_and_publish(
                    user, AuditActions.TENANT_MEMBER_ADDED, details,
                    EventType.TENANT_MEMBER_ADDED,
                )
            return True
        except Exception:
            logger.exception(
                'Discord auto-join failed for user %s in tenant %s', user.id, tenant_id,
            )
            return False

    @staticmethod
    async def was_removed(user: User, tenant_id: int) -> bool:
        """Whether staff removed ``user`` here and nobody on staff has let them back in."""
        return await TenantMembershipRepository.is_blocked(user.id, tenant_id)

    async def get_request(self, user: User, tenant_id: int) -> Optional[TenantJoinRequest]:
        """This user's request in a named tenant, if any.

        Explicit ``tenant_id`` for the same reason ``request_to_join`` takes one:
        the caller is a page the user is not a member of.
        """
        return await TenantJoinRequestRepository.get(user.id, tenant_id)

    async def next_request_allowed_at(self, user: User, tenant_id: int) -> Optional[datetime]:
        """When a recently declined requester may ask again, or ``None`` if now.

        Explicit ``tenant_id``, like :meth:`get_request`: the door asks it.
        """
        request = await TenantJoinRequestRepository.get(user.id, tenant_id)
        if (
            request is None
            or request.status is not JoinRequestStatus.DENIED
            or request.decided_at is None
        ):
            return None
        allowed = request.decided_at + JOIN_REQUEST_COOLDOWN
        return allowed if allowed > datetime.now(timezone.utc) else None

    async def list_pending(self) -> List[TenantJoinRequest]:
        """Pending requests for the tenant in scope — the staff queue."""
        return await TenantJoinRequestRepository.list_pending(require_tenant_id())

    async def list_pending_for(self, actor: User) -> List[TenantJoinRequest]:
        """The staff queue, for a caller that has not already passed a staff gate.

        The admin tab reads :meth:`list_pending` behind its own page gate; the
        REST and MCP surfaces come through here so the check lives with the read.
        """
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            'Only Staff can see join requests',
        )
        return await self.list_pending()

    async def approve_request(self, actor: User, request_id: int) -> TenantJoinRequest:
        request = await self._decidable(actor, request_id)
        await TenantMembershipRepository.add(
            request.user, request.tenant_id, MembershipSource.JOIN_REQUEST,
        )
        await TenantMembershipRepository.unblock(request.user_id, request.tenant_id)
        await TenantJoinRequestRepository.decide(
            request, JoinRequestStatus.APPROVED, actor, datetime.now(timezone.utc),
        )
        await self.audit_service.write_and_publish(
            actor, AuditActions.TENANT_JOIN_APPROVED,
            {'target_user_id': request.user_id, 'request_id': request.id},
            EventType.TENANT_JOIN_APPROVED,
        )
        await self._notify_requester(request.user, approved=True)
        await _mark_staff_dms(request, request.user, actor, approved=True)
        return request

    async def deny_request(self, actor: User, request_id: int) -> TenantJoinRequest:
        request = await self._decidable(actor, request_id)
        # A request left over from before someone was let in another way. A
        # decline would DM a member that they weren't approved.
        if await TenantMembershipRepository.is_member(request.user_id, request.tenant_id):
            raise ValueError(
                f'{request.user.display_name or request.user.username} is already a '
                'member, so there’s nothing to decline. Approve clears the request.'
            )
        await TenantJoinRequestRepository.decide(
            request, JoinRequestStatus.DENIED, actor, datetime.now(timezone.utc),
        )
        await self.audit_service.write_and_publish(
            actor, AuditActions.TENANT_JOIN_DENIED,
            {'target_user_id': request.user_id, 'request_id': request.id},
            EventType.TENANT_JOIN_DENIED,
        )
        # Both outcomes, not just the happy one: notification that only fires on
        # success leaves everyone else wondering whether anyone saw it.
        await self._notify_requester(request.user, approved=False)
        await _mark_staff_dms(request, request.user, actor, approved=False)
        return request

    # ---- notification (best-effort; a DM failure never blocks a decision) --

    async def _notify_staff_of_request(
        self, requester: User, request: TenantJoinRequest, tenant_id: int,
        message: Optional[str],
    ) -> None:
        """DM the community's staff. A request nobody sees is worse than none."""
        from application.services.tenant_service import TenantService

        community = await TenantService.community_name(tenant_id)
        staff = await UserRoleRepository.list_users_with_role(Role.STAFF, active_only=True)
        body = join_requested_dm(
            community, requester.display_name or requester.username, message or '',
        )
        embed = notification_embed(
            title='🚪 Join request',
            color=COLOR_JOIN_REQUEST,
            community_name=community,
            description=body,
        )
        # "Approve or deny it on the Users tab" was the whole call to action and
        # named a page without linking it; the button is now the tab itself.
        from application.services import notification_links, staff_request_dms
        link = await notification_links.admin_users()
        await staff_request_dms.send(
            StaffRequestKind.JOIN_REQUEST, request.id, staff, body, embed=embed, link=link,
        )

    async def _notify_requester(self, requester: User, *, approved: bool) -> None:
        from application.services.discord import DiscordService, discord_queue
        from application.services.tenant_service import TenantService

        if not requester.discord_id:
            return
        community = await TenantService.current_community_name()
        invite = None if approved else await self._invite_link()
        auto_join = False if approved else await self.auto_join_active(require_tenant_id())
        body = join_decided_dm(
            community, approved,
            ask_again_from='' if approved else self._ask_again_markup(),
            has_invite=invite is not None,
            auto_join=auto_join,
        )
        embed = notification_embed(
            title='🚪 Join request ' + ('approved' if approved else 'declined'),
            color=COLOR_JOIN_REQUEST if approved else COLOR_CANCELLED,
            community_name=community,
            description=body,
        )
        # An approval links the next step, finding a tournament; a decline links
        # the Discord invite when there is one.
        from application.services import notification_links
        link = await notification_links.home_tournaments(label='Find a tournament') if approved else None
        if invite is not None:
            link = invite
        discord_queue.enqueue(
            DiscordService().send_dm(
                int(requester.discord_id), body, embed=embed, link=link,
            )
        )

    @staticmethod
    def _ask_again_markup() -> str:
        """Discord's date-and-time markup for a week from now, in their own zone.

        Date and time, not date alone: the cooldown ends at the time of day the
        request was declined.
        """
        when = datetime.now(timezone.utc) + JOIN_REQUEST_COOLDOWN
        return f'<t:{int(when.timestamp())}:f>'

    @staticmethod
    async def _invite_link():
        """The community's Discord invite as a DM button, or ``None``."""
        from application.services.system_config_service import SystemConfigService
        from application.utils.discord_messages import DMLink

        try:
            url = await SystemConfigService.get_discord_invite_url()
        except Exception:
            logger.exception('Join decline: Discord invite lookup failed')
            return None
        return DMLink('Join the Discord server', url) if url else None

    @staticmethod
    async def _notify_member(user: User, build, **kwargs) -> None:
        """DM ``user`` about their own membership, with a link when they're in."""
        from application.services import notification_links
        from application.services.discord import DiscordService, discord_queue
        from application.services.tenant_service import TenantService

        if not user.discord_id:
            return
        try:
            community = await TenantService.current_community_name()
            body = build(community, **kwargs)
            joined = build is not member_removed_dm
            embed = notification_embed(
                title='🚪 Welcome in' if joined else '🚪 Membership removed',
                color=COLOR_JOIN_REQUEST if joined else COLOR_CANCELLED,
                community_name=community,
                description=body,
            )
            link = await notification_links.home_tournaments(label='Find a tournament') if joined else None
            discord_queue.enqueue(
                DiscordService().send_dm(int(user.discord_id), body, embed=embed, link=link)
            )
        except Exception:
            logger.exception('Membership DM to user %s failed', user.id)

    @staticmethod
    async def auto_join_active(tenant_id: int) -> bool:
        """Whether the linked Discord server lets its members straight in here.

        When it does, being in the server *is* the membership rule: a removal or
        a decline doesn't stick against it, and the copy around both says so.
        Never raises; an unreadable setting reads as off.
        """
        from application.services.system_config_service import (
            KEY_DISCORD_AUTO_JOIN,
            SystemConfigService,
        )
        from application.services.tenant_service import TenantService

        try:
            with tenant_scope(tenant_id):
                if not await SystemConfigService.get_bool(KEY_DISCORD_AUTO_JOIN):
                    return False
            tenant = await TenantService.get_by_id(tenant_id)
            return bool(tenant and tenant.discord_guild_id)
        except Exception:
            logger.exception('Auto-join setting lookup failed for tenant %s', tenant_id)
            return False

    async def _decidable(self, actor: User, request_id: int) -> TenantJoinRequest:
        """Load a request this actor may decide, in the tenant in scope."""
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            'Only Staff can decide join requests',
        )
        request = await TenantJoinRequestRepository.get_by_id(request_id)
        # not_found rather than forbidden for another community's request: the
        # message must not confirm that it exists.
        if request is None or request.tenant_id != require_tenant_id():
            raise NotFoundError('That join request no longer exists.')
        if request.status is not JoinRequestStatus.PENDING:
            raise ValueError('That request has already been decided.')
        return request

    @staticmethod
    async def require_community_users(
        user_ids: Iterable[int], actor: Optional[User],
    ) -> None:
        """Refuse any id in ``user_ids`` who isn't a member of this community.

        ``User`` is global, so a write that takes someone's id from the caller
        (a match roster, a shift assignment, a bracket entrant) would otherwise
        reach every account on the platform: it enrols them, names them in the
        response and has the bot DM them. The pickers already offer only
        members; this holds every other caller (REST, MCP) to the same set.

        The refusal reads exactly like an unknown id, so it can't be used to
        learn who exists elsewhere. A super-admin belongs to no community and
        may name anyone, as they can everywhere else. ``actor=None`` is a
        system path (a worker or import) acting on rows it already holds.
        """
        wanted = [uid for uid in dict.fromkeys(user_ids) if uid is not None]
        if not wanted or actor is None:
            return
        if await AuthService.is_super_admin(actor):
            return
        members = await TenantMembershipRepository.member_ids(wanted, require_tenant_id())
        for uid in wanted:
            if uid not in members:
                raise NotFoundError(f"User {uid} not found")

    @staticmethod
    async def ensure_member(
        user: User,
        *,
        actor: Optional[User] = None,
        source: Optional[MembershipSource] = None,
    ) -> Optional[TenantJoinRequest]:
        """Idempotent, unaudited membership for the in-scope tenant.

        The hook for "a role in a tenant implies membership in it": called from a
        role grant that is audited in its own right, so a second audit row would
        only be noise. Returns the pending join request it closed, if any, so
        that caller's audit row can name it (``closed_request_id``); ``actor``
        is recorded as its decider (``None`` for the Discord sync).

        A closed request also DMs the person that they're in, since the door
        promised a message either way. Nobody else is DM'd: the role sync and
        SpeedGaming import call this for people who never asked.
        """
        tenant_id = require_tenant_id()
        if actor is not None:
            await TenantMembershipRepository.unblock(user.id, tenant_id)
        if not await TenantMembershipRepository.add_returning_created(user, tenant_id, source):
            return None
        closed = await close_pending_request(user, tenant_id, actor)
        if closed is not None:
            await TenantMembershipService._notify_member(user, member_added_dm, by_staff=False)
        return closed
