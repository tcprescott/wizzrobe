"""
Accommodation Service - Business Logic Layer

ADA accommodation requests: a member ticks a box on their profile (with
optional details), and the community's STAFF track each request through
``NEW → ACKNOWLEDGED → ARRANGED`` with their own private notes.

Who sees what is the whole design. The requester sees their own checkbox,
details and status, never the staff notes. STAFF (and super-admins, via
``AuthService.is_staff``) see everything for their community. PROCTORs learn
only that an Arranged request exists (the icon on their match boards), never the
text. Nobody else sees anything, and no free text leaves the app: audit rows
carry ids and status only, nothing is published on the event bus, the staff DMs
name the member and nothing more, and there is no REST or MCP surface.

Gated by :attr:`~models.FeatureFlag.ADA_ACCOMMODATIONS`.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories.accommodation_repository import AccommodationRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.notification_catalog import ADA_CHANGED
from application.services.tenant_membership_service import TenantMembershipService
from application.utils.discord_messages_accommodation import (
    accommodation_changed_dm,
    accommodation_requested_dm,
)
from models import (
    AccommodationRequest,
    AccommodationStatus,
    FeatureFlag,
    Role,
    StaffRequestKind,
    User,
)

logger = logging.getLogger(__name__)

DETAILS_MAX_LENGTH = 2000
STAFF_NOTES_MAX_LENGTH = 4000

#: At most one "request opened" DM to staff per request in this window.
STAFF_DM_WINDOW = timedelta(hours=24)

OPEN_STATUSES = (
    AccommodationStatus.NEW,
    AccommodationStatus.ACKNOWLEDGED,
    AccommodationStatus.ARRANGED,
)


def _clean(text: Optional[str], limit: int, what: str) -> Optional[str]:
    text = (text or '').strip()
    if len(text) > limit:
        raise ValueError(f'{what} must be {limit} characters or fewer.')
    return text or None


class AccommodationService:
    """Service for ADA accommodation requests."""

    def __init__(self) -> None:
        self.repository = AccommodationRepository()
        self.audit_service = AuditService()

    @staticmethod
    async def _ensure_staff(actor: Optional[User]) -> None:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            'Only staff can view accommodation requests.',
        )

    @requires_feature(FeatureFlag.ADA_ACCOMMODATIONS)
    async def get_mine(self, actor: User) -> Optional[AccommodationRequest]:
        """``actor``'s own request in this community, withdrawn or not."""
        return await self.repository.get_for_user(actor)

    @requires_feature(FeatureFlag.ADA_ACCOMMODATIONS)
    async def set_my_request(
        self, actor: User, requested: bool, details: Optional[str] = None,
    ) -> Optional[AccommodationRequest]:
        """Ask for accommodation, change the details, or withdraw.

        Withdrawing clears the details but keeps the row and its staff notes.
        Asking again after a withdrawal, or changing the details on a request
        staff only acknowledged, puts it back to ``NEW`` so the change is not
        missed. Changing the details on an ``ARRANGED`` request keeps it
        Arranged, so the proctors' icon stays on the boards, and flags it
        ``changed_since_arranged`` until staff review it. Re-saving identical
        details is a no-op.

        Staff are DM'd when a request opens and when an Arranged one first
        changes; later autosaves of the same edit stay quiet.
        """
        existing = await self.repository.get_for_user(actor)

        if not requested:
            if existing is None or existing.status is AccommodationStatus.WITHDRAWN:
                return existing
            await self.repository.update(
                existing, status=AccommodationStatus.WITHDRAWN, details=None,
                changed_since_arranged=False, arranged_details=None,
            )
            await self._audit(actor, AuditActions.ACCOMMODATION_WITHDRAWN, existing)
            await self._mark_staff_dms(actor, existing, withdrawn=True)
            return existing

        details = _clean(details, DETAILS_MAX_LENGTH, 'Accommodation details')

        if existing is None:
            if not await TenantMembershipService().is_member(actor):
                raise ValueError('Join this community before requesting accommodation.')
            created = await self.repository.create(user=actor, details=details)
            await self._audit(actor, AuditActions.ACCOMMODATION_REQUESTED, created)
            await self._notify_staff(actor, created, changed=False)
            return created

        if existing.status is AccommodationStatus.WITHDRAWN:
            await self.repository.update(existing, status=AccommodationStatus.NEW, details=details)
            await self._audit(actor, AuditActions.ACCOMMODATION_REQUESTED, existing)
            await self._notify_staff(actor, existing, changed=False)
            return existing

        if details == existing.details:
            return existing

        if existing.status is AccommodationStatus.ARRANGED:
            already_flagged = existing.changed_since_arranged
            arranged = existing.arranged_details if already_flagged else existing.details
            # Typing it back to what staff arranged against is no change at all.
            flagged = details != arranged
            await self.repository.update(
                existing, details=details,
                changed_since_arranged=flagged,
                arranged_details=arranged if flagged else None,
            )
            await self._audit(actor, AuditActions.ACCOMMODATION_UPDATED, existing)
            if flagged and not already_flagged:
                await self._notify_staff(actor, existing, changed=True)
            return existing

        reopened = existing.status is not AccommodationStatus.NEW
        await self.repository.update(existing, status=AccommodationStatus.NEW, details=details)
        await self._audit(actor, AuditActions.ACCOMMODATION_UPDATED, existing)
        if reopened:
            await self._restore_staff_dms(actor, existing)
        return existing

    # feature-gate: exempt — soft integration point called from removing a member.
    async def close_for_removed_member(self, actor: User, member: User) -> None:
        """Withdraw a removed member's open request, clearing their details.

        Staff must not keep reading the disability details of someone who is no
        longer in the community, and the boards must not keep their icon. Runs
        whatever the flag says: it only ever removes data. Staff notes stay, as
        on any withdrawal.
        """
        request = await self.repository.get_for_user(member)
        if request is None or request.status is AccommodationStatus.WITHDRAWN:
            return
        await self.repository.update(
            request, status=AccommodationStatus.WITHDRAWN, details=None,
            changed_since_arranged=False, arranged_details=None,
        )
        await self._audit(
            actor, AuditActions.ACCOMMODATION_WITHDRAWN, request, source='member_removed',
        )
        await self._mark_staff_dms(member, request, withdrawn=True)

    @requires_feature(FeatureFlag.ADA_ACCOMMODATIONS)
    async def list_requests(
        self, actor: User, include_withdrawn: bool = False,
    ) -> List[AccommodationRequest]:
        await self._ensure_staff(actor)
        statuses = list(OPEN_STATUSES)
        if include_withdrawn:
            statuses.append(AccommodationStatus.WITHDRAWN)
        return await self.repository.list_by_status(statuses)

    @requires_feature(FeatureFlag.ADA_ACCOMMODATIONS)
    async def get_request(self, actor: User, request_id: int) -> Optional[AccommodationRequest]:
        """One request in this community, for a deep link from a staff DM."""
        await self._ensure_staff(actor)
        return await self.repository.get_by_id(request_id)

    @requires_feature(FeatureFlag.ADA_ACCOMMODATIONS)
    async def requesting_user_ids(self, actor: User) -> Set[int]:
        """Members with an open (not withdrawn) request, for the Users-tab filter."""
        await self._ensure_staff(actor)
        return await self.repository.user_ids_with_status(OPEN_STATUSES)

    @requires_feature(FeatureFlag.ADA_ACCOMMODATIONS)
    async def open_requests_by_user(self, actor: User) -> Dict[int, AccommodationRequest]:
        """Each member's open request, keyed by user id, for the Users-tab ADA column."""
        await self._ensure_staff(actor)
        return {r.user_id: r for r in await self.repository.list_by_status(OPEN_STATUSES)}

    @requires_feature(FeatureFlag.ADA_ACCOMMODATIONS)
    async def action_needed_count(self, actor: User) -> int:
        """Requests staff still have to do something about, for the sub-tab label.

        New and Acknowledged ones, plus Arranged ones the member has changed
        since. A request that's arranged and unchanged is done, so the count can
        reach zero.
        """
        await self._ensure_staff(actor)
        return await self.repository.count_needing_action()

    # feature-gate: exempt — soft integration point; see the docstring below.
    async def arranged_notes_for(
        self, actor: Optional[User], user_ids: Iterable[int],
    ) -> Dict[int, Optional[str]]:
        """Players whose request is ``ARRANGED``, keyed by user id.

        Feeds the accessibility icon on the admin Schedule and Proctor Station
        boards, which are not themselves flag-gated and are also open to roles
        that must not see this (tournament admins, stream managers). So it is
        soft: it returns ``{}`` rather than raising when the feature is off or
        the viewer is neither STAFF nor a PROCTOR, and every board can call it
        unconditionally.

        STAFF get the staff notes (``''`` when arranged with no note). A
        PROCTOR gets ``None`` for each: they see that something is arranged and
        are sent to staff for what. Nobody here gets the requester's own
        details. The notes are written under a disclaimer that says only staff
        read them, and this is where that promise is kept.
        """
        ids = {i for i in user_ids if i is not None}
        if not ids or actor is None:
            return {}
        if not await FeatureFlagService().is_enabled(FeatureFlag.ADA_ACCOMMODATIONS):
            return {}
        is_staff = await AuthService.is_staff(actor)
        if not (is_staff or await AuthService.has_role(actor, Role.PROCTOR)):
            return {}
        requests = await self.repository.list_for_users(ids, AccommodationStatus.ARRANGED)
        if not is_staff:
            return {r.user_id: None for r in requests}
        return {r.user_id: r.staff_notes or '' for r in requests}

    @requires_feature(FeatureFlag.ADA_ACCOMMODATIONS)
    async def update_request(
        self,
        actor: User,
        request_id: int,
        status: AccommodationStatus,
        staff_notes: Optional[str],
    ) -> AccommodationRequest:
        """Set a request's status and staff notes in one save.

        Staff cannot withdraw on someone's behalf (only the requester can), and
        cannot move a withdrawn request out of ``WITHDRAWN``: reopening is the
        requester ticking the box again. Notes stay editable either way.

        Saving a request the member changed after it was arranged marks the
        change reviewed, even with nothing else edited: the save is staff saying
        they've read it.
        """
        await self._ensure_staff(actor)
        request = require_found(await self.repository.get_by_id(request_id), 'Accommodation request')
        status = AccommodationStatus(status)
        staff_notes = _clean(staff_notes, STAFF_NOTES_MAX_LENGTH, 'Staff notes')

        if request.status is AccommodationStatus.WITHDRAWN:
            if status is not AccommodationStatus.WITHDRAWN:
                raise ValueError('This request was withdrawn. It reopens when the attendee asks again.')
        elif status is AccommodationStatus.WITHDRAWN:
            raise ValueError('Only the attendee can withdraw their request.')

        status_changed = status is not request.status
        notes_changed = staff_notes != request.staff_notes
        reviewed = request.changed_since_arranged
        if not (status_changed or notes_changed or reviewed):
            return request

        previous = request.status
        await self.repository.update(
            request, status=status, staff_notes=staff_notes,
            changed_since_arranged=False, arranged_details=None,
        )
        if reviewed:
            await self._audit(actor, AuditActions.ACCOMMODATION_CHANGE_REVIEWED, request)
        if status_changed:
            await self._audit(
                actor, AuditActions.ACCOMMODATION_STATUS_CHANGED, request,
                previous_status=previous.value,
            )
        if notes_changed:
            await self._audit(actor, AuditActions.ACCOMMODATION_NOTES_UPDATED, request)
        if status_changed and status is AccommodationStatus.NEW:
            await self._restore_staff_dms(request.user, request)
        elif status_changed:
            await self._mark_staff_dms(actor, request)
        elif reviewed:
            await self._mark_staff_dms(actor, request, reviewed=True)
        return request

    async def _notify_staff(
        self, member: User, request: AccommodationRequest, *, changed: bool,
    ) -> None:
        """DM the community's staff, with a button that opens this request.

        Best-effort: a Discord failure never blocks the member's save. An
        opened request DMs at most once per :data:`STAFF_DM_WINDOW`: the profile
        checkbox saves on every toggle, and unticking and re-ticking it shouldn't
        DM every staff member each time.
        """
        now = datetime.now(timezone.utc)
        if (
            not changed
            and request.staff_notified_at is not None
            and now - request.staff_notified_at < STAFF_DM_WINDOW
        ):
            # Reopened inside the window: no new DM, so the copies staff
            # already have must stop saying it was withdrawn.
            await self._restore_staff_dms(member, request)
            return
        try:
            from application.repositories.user_role_repository import UserRoleRepository
            from application.services import notification_links, staff_request_dms

            body, embed = await self._staff_card(member, changed=changed)
            link = await notification_links.admin_ada_request(request.id)
            staff = [
                u for u in await UserRoleRepository.list_users_with_role(Role.STAFF, active_only=True)
                if u.id != member.id
            ]
            await staff_request_dms.send(
                StaffRequestKind.ADA_REQUEST, request.id, staff, body, embed=embed, link=link,
                notice=ADA_CHANGED if changed else None,
            )
            await self.repository.update(request, staff_notified_at=now)
        except Exception:
            logger.exception('ADA request %s: staff notification failed', request.id)

    @staticmethod
    async def _staff_card(member: User, *, changed: bool):
        """The staff DM's text and card: a new request, or a change to an arranged one."""
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import (
            COLOR_JOIN_REQUEST,
            COLOR_RESCHEDULED,
            notification_embed,
        )

        community = await TenantService.current_community_name()
        name = member.preferred_name
        if changed:
            body = accommodation_changed_dm(community, name)
            title, color = '♿ ADA request changed', COLOR_RESCHEDULED
        else:
            body = accommodation_requested_dm(community, name)
            title, color = '♿ ADA request', COLOR_JOIN_REQUEST
        embed = notification_embed(
            title=title, color=color, community_name=community, description=body,
        )
        return body, embed

    async def _mark_staff_dms(
        self, actor: User, request: AccommodationRequest, *,
        reviewed: bool = False, withdrawn: bool = False,
    ) -> None:
        """Edit the DMs staff got so they can see someone already picked it up."""
        try:
            from application.services import notification_links, staff_request_dms
            from application.services.tenant_service import TenantService
            from application.utils import discord_messages_staff_requests as copy
            from application.utils.discord_embeds import COLOR_CONFIRMED, COLOR_FINISHED

            community = await TenantService.current_community_name()
            member_name = (await request.user).preferred_name
            when = datetime.now(timezone.utc)
            staff_name = actor.preferred_name
            label = request.status.value.title()

            def build(you: bool):
                if withdrawn:
                    card = copy.ada_withdrawn(member_name, when)
                elif reviewed:
                    card = copy.ada_reviewed(you, staff_name, member_name, when)
                else:
                    card = copy.ada_status(you, staff_name, member_name, label, when)
                return staff_request_dms.card(
                    card, color=COLOR_FINISHED if withdrawn else COLOR_CONFIRMED,
                    community_name=community,
                )

            link = await notification_links.admin_ada_request(request.id)
            staff_request_dms.mark_handled(
                StaffRequestKind.ADA_REQUEST, request.id, build, link, actor_id=actor.id,
            )
        except Exception:
            logger.exception('ADA request %s: staff DM edit failed', request.id)

    async def _restore_staff_dms(self, member: User, request: AccommodationRequest) -> None:
        """Put the staff DMs back to "needs looking at" when a request opens again."""
        try:
            from application.services import notification_links, staff_request_dms

            _body, embed = await self._staff_card(member, changed=False)
            link = await notification_links.admin_ada_request(request.id)
            staff_request_dms.mark_handled(
                StaffRequestKind.ADA_REQUEST, request.id, lambda _you: embed, link,
            )
        except Exception:
            logger.exception('ADA request %s: staff DM restore failed', request.id)

    async def _audit(
        self, actor: User, action: str, request: AccommodationRequest, **extra: str,
    ) -> None:
        await self.audit_service.write_log(
            actor,
            action,
            {
                'request_id': request.id,
                'user_id': request.user_id,
                'status': request.status.value,
                **extra,
            },
        )
