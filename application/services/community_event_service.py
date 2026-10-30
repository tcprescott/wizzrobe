"""
Community Event Service - Business Logic Layer

Meetups, panels, side tournaments and anything else members run for each other
during the onsite event. A member submits one and staff approve it; an event
staff submit is approved as it's saved. Only approved events are listed, and
the listing says plainly that none of it is endorsed by event staff.

Members can subscribe to an approved event and get a Discord DM shortly before
it starts (the lead time is a community setting; see
:mod:`application.services.community_event_reminder`). Subscribers also hear
when an approved event moves or is called off.

Edits by the host go live without another review: staff can take a listed event
down (``reject`` on an approved event) if an edit crosses a line. Taking one down
tells its subscribers it's cancelled and drops their subscriptions and RSVPs, so
if it's approved again they start from nothing rather than getting a reminder
for an event they were told was off. A rejected event edited by its host goes
back into the review queue.

Staff can post an event for another member, or hand an existing one over: the
member becomes its host, with the host's edit and cancel rights, and is DM'd.
The host has to belong to the community.

Members only throughout: the listing, the submissions and the subscriptions all
belong to the community. Gated by :attr:`~models.FeatureFlag.COMMUNITY_EVENTS`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

from tortoise.transactions import in_transaction

from application.errors import NotFoundError, require_found
from application.events import EventType
from application.feature_flags import requires_feature
from application.repositories.community_event_repository import (
    CommunityEventRepository,
    CommunityEventSeenRepository,
    CommunityEventSubscriptionRepository,
)
from application.repositories.user_repository import UserRepository
from application.services import _community_event_notifications as notify
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.system_config_service import SystemConfigService
from models import CommunityEvent, CommunityEventStatus, FeatureFlag, User

TITLE_MAX_LENGTH = 120
DESCRIPTION_MAX_LENGTH = 5000
LOCATION_MAX_LENGTH = 200
REVIEW_NOTE_MAX_LENGTH = 1000
MIN_DURATION_MINUTES = 5
#: Three days: long enough for a room that runs the whole weekend.
MAX_DURATION_MINUTES = 72 * 60
MAX_RSVP_CAPACITY = 1000

#: How far back the listing looks for an event that is still running. Matches
#: the longest event allowed, so nothing in progress drops off early.
_RUNNING_LOOKBACK = timedelta(minutes=MAX_DURATION_MINUTES)

_EDITABLE = (CommunityEventStatus.PENDING, CommunityEventStatus.APPROVED, CommunityEventStatus.REJECTED)


@dataclass(frozen=True)
class EventDraft:
    """What a host types into the submit/edit form, already in UTC."""

    title: str
    description: str
    starts_at: datetime
    duration_minutes: int
    location: Optional[str] = None
    rsvp_enabled: bool = False
    #: The most people who can RSVP; ``None`` means no cap. Ignored without RSVPs.
    rsvp_capacity: Optional[int] = None


def ends_at(event: CommunityEvent) -> datetime:
    return event.starts_at + timedelta(minutes=event.duration_minutes)


def listed_at(event: CommunityEvent) -> datetime:
    """When ``event`` went onto the listing: its approval, else its submission.

    A submission listed without review (approval off) has no ``reviewed_at``.
    A re-approval after a takedown moves it, so a returning event is new again.
    """
    return event.reviewed_at or event.created_at


def _announce_listing(event: CommunityEvent) -> None:
    from application.services import opt_in_notifications
    from application.services.discord import discord_queue
    discord_queue.enqueue(opt_in_notifications.event_listed(event.id))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_draft(draft: EventDraft) -> EventDraft:
    title = (draft.title or '').strip()
    if not title:
        raise ValueError('Give the event a title.')
    if len(title) > TITLE_MAX_LENGTH:
        raise ValueError(f'The title must be {TITLE_MAX_LENGTH} characters or fewer.')
    description = (draft.description or '').strip()
    if len(description) > DESCRIPTION_MAX_LENGTH:
        raise ValueError(f'The description must be {DESCRIPTION_MAX_LENGTH} characters or fewer.')
    location = (draft.location or '').strip() or None
    if location and len(location) > LOCATION_MAX_LENGTH:
        raise ValueError(f'The location must be {LOCATION_MAX_LENGTH} characters or fewer.')
    if draft.starts_at is None:
        raise ValueError('Pick when the event starts.')
    if draft.starts_at.tzinfo is None:
        raise ValueError('The start time must carry a timezone.')
    try:
        duration = int(draft.duration_minutes)
    except (TypeError, ValueError):
        raise ValueError('Give the event a length in minutes.') from None
    if not MIN_DURATION_MINUTES <= duration <= MAX_DURATION_MINUTES:
        raise ValueError(
            f'An event must run between {MIN_DURATION_MINUTES} minutes and '
            f'{MAX_DURATION_MINUTES // 60} hours.'
        )
    capacity = None
    if draft.rsvp_enabled and draft.rsvp_capacity not in (None, ''):
        try:
            capacity = int(draft.rsvp_capacity)
        except (TypeError, ValueError):
            raise ValueError('The RSVP cap must be a whole number.') from None
        if not 1 <= capacity <= MAX_RSVP_CAPACITY:
            raise ValueError(f'The RSVP cap must be between 1 and {MAX_RSVP_CAPACITY}.')
    return EventDraft(
        title, description, draft.starts_at.astimezone(timezone.utc), duration, location,
        rsvp_enabled=bool(draft.rsvp_enabled), rsvp_capacity=capacity,
    )


class CommunityEventService:
    """Service for community events and their subscriptions."""

    def __init__(self) -> None:
        self.repository = CommunityEventRepository()
        self.subscriptions = CommunityEventSubscriptionRepository()
        self.seen = CommunityEventSeenRepository()
        self.audit_service = AuditService()

    @staticmethod
    async def _ensure_member(actor: Optional[User]) -> None:
        from application.services.tenant_membership_service import TenantMembershipService

        await AuthService.ensure(
            actor is not None and await TenantMembershipService().is_member(actor),
            'Meetups are for members of this community. Ask to join it first.',
        )

    @staticmethod
    async def _ensure_staff(actor: Optional[User]) -> None:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            'Only staff can review meetups.',
        )

    @staticmethod
    async def _can_manage(actor: Optional[User], event: CommunityEvent) -> bool:
        """Whether ``actor`` may edit or cancel ``event``: its host, or staff."""
        if actor is None:
            return False
        return event.host_id == actor.id or await AuthService.is_staff(actor)

    async def _load_visible(self, actor: User, event_id: int) -> CommunityEvent:
        """An event ``actor`` may see: any approved one, or their own, or any for staff.

        Anything else is reported missing rather than forbidden, so a member
        can't learn which pending submissions exist.
        """
        event = require_found(await self.repository.get_by_id(event_id), 'Community event')
        if event.status is CommunityEventStatus.APPROVED or await self._can_manage(actor, event):
            return event
        raise NotFoundError('Community event not found.')

    @staticmethod
    async def _pick_host(actor: User, host_id: Optional[int]) -> User:
        """Who hosts an event ``actor`` saves: themselves, or a member staff named.

        Refused like an unknown id when the person isn't in this community, so
        the picker can't be used to find accounts elsewhere. A super-admin is
        held to the same rule: a host outside the community couldn't open it.
        """
        from application.services.tenant_membership_service import TenantMembershipService

        if host_id is None or host_id == actor.id:
            return actor
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            'Only staff can post an event for someone else.',
        )
        host = await UserRepository.get_by_id(host_id)
        if host is None or not host.is_active or not await TenantMembershipService().is_member(host):
            raise NotFoundError('That member was not found in this community.')
        return host

    async def _load_managed(self, actor: User, event_id: int) -> CommunityEvent:
        event = await self._load_visible(actor, event_id)
        await AuthService.ensure(
            await self._can_manage(actor, event),
            'Only the host or staff can change this event.',
        )
        return event

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def list_upcoming(self, actor: User) -> List[CommunityEvent]:
        """Approved events that haven't finished yet, soonest first."""
        await self._ensure_member(actor)
        now = _now()
        events = await self.repository.list_by_status(
            [CommunityEventStatus.APPROVED], starts_after=now - _RUNNING_LOOKBACK,
        )
        return [e for e in events if ends_at(e) > now]

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def unseen_ids(self, actor: User) -> Set[int]:
        """Listed events that went up since ``actor`` last opened the listing.

        Their own events never count. Someone who has never opened it has
        everything still to come as new.
        """
        await self._ensure_member(actor)
        seen = await self.seen.seen_at(actor.id)
        return {
            e.id for e in await self.list_upcoming(actor)
            if e.host_id != actor.id and (seen is None or listed_at(e) > seen)
        }

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def mark_seen(self, actor: User) -> None:
        """``actor`` has opened the listing, so nothing on it is new any more."""
        await self._ensure_member(actor)
        await self.seen.mark(actor.id, _now())

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def list_mine(self, actor: User) -> List[CommunityEvent]:
        """Everything ``actor`` has submitted, newest first, whatever its status."""
        await self._ensure_member(actor)
        return await self.repository.list_for_host(actor)

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def get_event(self, actor: User, event_id: int) -> CommunityEvent:
        await self._ensure_member(actor)
        return await self._load_visible(actor, event_id)

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def subscription_state(
        self, actor: User, event_ids: List[int],
    ) -> tuple[Set[int], Dict[int, int]]:
        """Which of ``event_ids`` ``actor`` follows, and how many follow each."""
        await self._ensure_member(actor)
        if not event_ids:
            return set(), {}
        mine = await self.subscriptions.event_ids_for_user(actor.id, event_ids)
        counts = await self.subscriptions.counts_for(event_ids)
        return mine, counts

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def list_attending(
        self, actor: User, start: datetime, end: datetime,
    ) -> List[Tuple[CommunityEvent, str]]:
        """Approved events overlapping ``start``–``end`` that ``actor`` is part of.

        Each comes with how: ``'hosting'`` their own, ``'going'`` an RSVP
        (which only counts while the event takes RSVPs), else ``'reminder'``.
        """
        await self._ensure_member(actor)
        after = start - _RUNNING_LOOKBACK
        attending: Dict[int, Tuple[CommunityEvent, str]] = {}
        for row in await self.subscriptions.list_for_user_starting(actor.id, after, end):
            going = row.going and row.event.rsvp_enabled
            attending[row.event_id] = (row.event, 'going' if going else 'reminder')
        for event in await self.repository.list_hosted_starting(actor.id, after, end):
            attending[event.id] = (event, 'hosting')
        return sorted(
            (pair for pair in attending.values() if ends_at(pair[0]) > start),
            key=lambda pair: (pair[0].starts_at, pair[0].id),
        )

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def submit(
        self, actor: User, draft: EventDraft, *, host_id: Optional[int] = None,
    ) -> CommunityEvent:
        """Submit an event, hosted by ``actor`` or, for staff, by the member ``host_id``.

        Staff's own go straight onto the listing, and so does everyone's when
        the community has turned off staff approval.
        """
        await self._ensure_member(actor)
        host = await self._pick_host(actor, host_id)
        draft = _clean_draft(draft)
        if draft.starts_at <= _now():
            raise ValueError('Pick a start time in the future.')
        is_staff = await AuthService.is_staff(actor)
        listed = is_staff or not await SystemConfigService.community_events_require_approval()
        status = CommunityEventStatus.APPROVED if listed else CommunityEventStatus.PENDING
        event = await self.repository.create(
            host=host,
            title=draft.title,
            description=draft.description,
            location=draft.location,
            starts_at=draft.starts_at,
            duration_minutes=draft.duration_minutes,
            rsvp_enabled=draft.rsvp_enabled,
            rsvp_capacity=draft.rsvp_capacity,
            status=status,
            reviewed_by=actor if is_staff else None,
            reviewed_at=_now() if is_staff else None,
        )
        on_behalf = host.id != actor.id
        await self._record(actor, AuditActions.COMMUNITY_EVENT_SUBMITTED,
                           EventType.COMMUNITY_EVENT_SUBMITTED, event,
                           **({'on_behalf_of': host.id} if on_behalf else {}))
        if on_behalf:
            await notify.notify_host_of_handover(event, host, actor, new_event=True)
        if listed:
            await self._record(actor, AuditActions.COMMUNITY_EVENT_APPROVED,
                               EventType.COMMUNITY_EVENT_APPROVED, event, automatic=True)
            _announce_listing(event)
        else:
            await notify.notify_staff_of_submission(event, actor)
        return event

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def update(
        self, actor: User, event_id: int, draft: EventDraft, *, host_id: Optional[int] = None,
    ) -> CommunityEvent:
        """Edit an event; staff can also hand it to another member with ``host_id``.

        An approved event stays approved; if its start moved, subscribers are
        told and their reminders re-armed for the new time. A rejected event
        edited by its host goes back to staff for review. A new host is DM'd.
        """
        await self._ensure_member(actor)
        event = await self._load_managed(actor, event_id)
        if event.status not in _EDITABLE:
            raise ValueError("A cancelled event can't be edited.")
        new_host = None
        if host_id is not None and host_id != event.host_id:
            new_host = await self._pick_host(actor, host_id)
        draft = _clean_draft(draft)
        moved = draft.starts_at != event.starts_at
        if moved and draft.starts_at <= _now():
            raise ValueError('Pick a start time in the future.')

        fields: Dict[str, object] = dict(
            title=draft.title, description=draft.description, location=draft.location,
            starts_at=draft.starts_at, duration_minutes=draft.duration_minutes,
            rsvp_enabled=draft.rsvp_enabled, rsvp_capacity=draft.rsvp_capacity,
        )
        previous_host_id = event.host_id
        if new_host is not None:
            fields.update(host=new_host)
        resubmitted = (
            event.status is CommunityEventStatus.REJECTED and not await AuthService.is_staff(actor)
        )
        if resubmitted:
            fields.update(status=CommunityEventStatus.PENDING, reviewed_by=None, reviewed_at=None)
        async with in_transaction():
            if draft.rsvp_enabled and draft.rsvp_capacity is not None:
                await self.subscriptions.lock_event(event.id)
                going = (await self.subscriptions.going_counts_for([event.id])).get(event.id, 0)
                if draft.rsvp_capacity < going:
                    raise ValueError(
                        f"{going} people have already RSVP'd, so the cap can't be lower than {going}."
                    )
            await self.repository.update(event, **fields)
        await self._record(actor, AuditActions.COMMUNITY_EVENT_UPDATED,
                           EventType.COMMUNITY_EVENT_UPDATED, event,
                           moved=moved, resubmitted=resubmitted,
                           **({'previous_host_id': previous_host_id} if new_host else {}))
        if new_host is not None and new_host.id != actor.id:
            await notify.notify_host_of_handover(event, new_host, actor, new_event=False)

        if resubmitted:
            await notify.notify_staff_of_submission(event, event.host)
        elif moved and event.status is CommunityEventStatus.APPROVED:
            await self.subscriptions.clear_reminders(event.id)
            await notify.notify_subscribers(event, await self._subscribers(event), cancelled=False)
        return event

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def cancel(self, actor: User, event_id: int) -> CommunityEvent:
        """Call an event off. Its subscribers hear about it if it was listed."""
        await self._ensure_member(actor)
        event = await self._load_managed(actor, event_id)
        if event.status is CommunityEventStatus.CANCELLED:
            return event
        was_pending = event.status is CommunityEventStatus.PENDING
        was_listed = event.status is CommunityEventStatus.APPROVED
        await self.repository.update(event, status=CommunityEventStatus.CANCELLED)
        await self._record(actor, AuditActions.COMMUNITY_EVENT_CANCELLED,
                           EventType.COMMUNITY_EVENT_CANCELLED, event)
        if was_pending:
            await notify.mark_staff_dms_cancelled(event, actor)
        if was_listed and ends_at(event) > _now():
            await notify.notify_subscribers(event, await self._subscribers(event), cancelled=True)
        return event

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def subscribe(self, actor: User, event_id: int) -> None:
        """Follow an approved event for a reminder before it starts. Idempotent."""
        await self._ensure_member(actor)
        event = await self._load_visible(actor, event_id)
        if event.status is not CommunityEventStatus.APPROVED:
            raise ValueError("You can only subscribe to an event once it's listed.")
        if ends_at(event) <= _now():
            raise ValueError('This event is over.')
        if await self.subscriptions.get_for(event.id, actor.id) is None:
            await self.subscriptions.create(event=event, user=actor)

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def unsubscribe(self, actor: User, event_id: int) -> None:
        """Stop following an event. Idempotent."""
        await self._ensure_member(actor)
        subscription = await self.subscriptions.get_for(event_id, actor.id)
        if subscription is not None:
            await self.subscriptions.delete(subscription)

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def rsvp_state(
        self, actor: User, event_ids: List[int],
    ) -> tuple[Set[int], Dict[int, int]]:
        """Which of ``event_ids`` ``actor`` RSVP'd to, and how many are going to each."""
        await self._ensure_member(actor)
        if not event_ids:
            return set(), {}
        mine = await self.subscriptions.going_event_ids_for_user(actor.id, event_ids)
        counts = await self.subscriptions.going_counts_for(event_ids)
        return mine, counts

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def rsvp(self, actor: User, event_id: int) -> None:
        """Say you're coming, which also gets you the reminder. Idempotent.

        Refused once the host's cap is reached. The event row is locked while
        the count is checked, so two people can't both take the last place.
        """
        await self._ensure_member(actor)
        event = await self._load_visible(actor, event_id)
        if event.status is not CommunityEventStatus.APPROVED:
            raise ValueError("You can only RSVP once the event is listed.")
        if not event.rsvp_enabled:
            raise ValueError("This event isn't taking RSVPs.")
        if ends_at(event) <= _now():
            raise ValueError('This event is over.')
        async with in_transaction():
            await self.subscriptions.lock_event(event.id)
            existing = await self.subscriptions.get_for(event.id, actor.id)
            if existing is not None and existing.going:
                return
            if event.rsvp_capacity is not None:
                going = (await self.subscriptions.going_counts_for([event.id])).get(event.id, 0)
                if going >= event.rsvp_capacity:
                    raise ValueError("This event is full.")
            if existing is None:
                await self.subscriptions.create(event=event, user=actor, going=True)
            else:
                await self.subscriptions.update(existing, going=True)

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def cancel_rsvp(self, actor: User, event_id: int) -> None:
        """Take back an RSVP, and the reminder with it. Idempotent."""
        await self.unsubscribe(actor, event_id)

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def list_attendees(self, actor: User, event_id: int) -> List[User]:
        """Who RSVP'd, in the order they did. The host and staff only."""
        await self._ensure_member(actor)
        event = await self._load_managed(actor, event_id)
        return [s.user for s in await self.subscriptions.list_going(event.id)]

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def list_for_review(
        self, actor: User, statuses: Optional[List[CommunityEventStatus]] = None,
    ) -> List[CommunityEvent]:
        """Events in ``statuses`` (every status when omitted), soonest first."""
        await self._ensure_staff(actor)
        return await self.repository.list_by_status(statuses or list(CommunityEventStatus))

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def pending_count(self, actor: User) -> int:
        await self._ensure_staff(actor)
        return await self.repository.count_by_status(CommunityEventStatus.PENDING)

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def approve(self, actor: User, event_id: int) -> CommunityEvent:
        """List a pending event, or put back one staff took down."""
        await self._ensure_staff(actor)
        event = require_found(await self.repository.get_by_id(event_id), 'Community event')
        if event.status is CommunityEventStatus.APPROVED:
            return event
        if event.status is CommunityEventStatus.CANCELLED:
            raise ValueError("This event was cancelled, so it can't be approved.")
        first_listing = event.status is CommunityEventStatus.PENDING
        await self.repository.update(
            event, status=CommunityEventStatus.APPROVED,
            reviewed_by=actor, reviewed_at=_now(), review_note=None,
        )
        await self._record(actor, AuditActions.COMMUNITY_EVENT_APPROVED,
                           EventType.COMMUNITY_EVENT_APPROVED, event)
        await notify.notify_host_of_decision(event, event.host, approved=True)
        await notify.mark_staff_dms_decided(event, actor, approved=True)
        if first_listing:
            _announce_listing(event)
        return event

    @requires_feature(FeatureFlag.COMMUNITY_EVENTS)
    async def reject(self, actor: User, event_id: int, reason: str) -> CommunityEvent:
        """Turn down a submission, or take a listed event down, with a reason for the host."""
        await self._ensure_staff(actor)
        reason = (reason or '').strip()
        if not reason:
            raise ValueError('Tell the host why, so they can fix it.')
        if len(reason) > REVIEW_NOTE_MAX_LENGTH:
            raise ValueError(f'The reason must be {REVIEW_NOTE_MAX_LENGTH} characters or fewer.')
        event = require_found(await self.repository.get_by_id(event_id), 'Community event')
        if event.status is CommunityEventStatus.CANCELLED:
            raise ValueError('This event was already cancelled.')
        was_listed = event.status is CommunityEventStatus.APPROVED
        # Taking a listed event down drops its subscriptions and RSVPs: they've
        # just been told it's cancelled, so a later re-approval must not remind them.
        subscribers = await self._subscribers(event) if was_listed else []
        async with in_transaction():
            await self.repository.update(
                event, status=CommunityEventStatus.REJECTED,
                reviewed_by=actor, reviewed_at=_now(), review_note=reason,
            )
            if was_listed:
                await self.subscriptions.delete_for_event(event.id)
        await self._record(actor, AuditActions.COMMUNITY_EVENT_REJECTED,
                           EventType.COMMUNITY_EVENT_REJECTED, event, was_listed=was_listed)
        await notify.notify_host_of_decision(event, event.host, approved=False, was_listed=was_listed)
        await notify.mark_staff_dms_decided(event, actor, approved=False, was_listed=was_listed)
        if subscribers and ends_at(event) > _now():
            await notify.notify_subscribers(event, subscribers, cancelled=True)
        return event

    async def _subscribers(self, event: CommunityEvent) -> List[User]:
        return [s.user for s in await self.subscriptions.list_for_event(event.id)]

    async def _record(self, actor: User, action: str, event_type: str,
                      event: CommunityEvent, **extra) -> None:
        details = {
            'community_event_id': event.id,
            'title': event.title,
            'starts_at': event.starts_at.isoformat(),
            'status': event.status.value,
            **extra,
        }
        await self.audit_service.write_and_publish(
            actor, action, details, event_type, event_extra={'host_id': event.host_id},
        )
