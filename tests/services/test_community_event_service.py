"""CommunityEventService: submission, review, edits, cancellation, subscriptions, and who hears what."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from application.errors import FeatureDisabledError, NotFoundError
from application.events import EventType
from application.services.audit_service import AuditActions
from application.services.community_event_service import CommunityEventService, EventDraft
from application.services.system_config_service import KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL, SystemConfigService
from models import (
    AuditLog,
    CommunityEvent,
    CommunityEventStatus,
    CommunityEventSubscription,
    FeatureFlag,
    Role,
    TenantFeatureFlag,
    TenantMembership,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

pytestmark = pytest.mark.usefixtures("db")


def _soon(hours: float = 5) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _draft(**overrides) -> EventDraft:
    fields = dict(title='Board games', description='**Bring** a game', starts_at=_soon(),
                  duration_minutes=120, location='Room 2')
    fields.update(overrides)
    return EventDraft(**fields)


@pytest.fixture
async def member():
    user = await make_user(discord_id=600, username='host')
    await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
async def other_member():
    user = await make_user(discord_id=602, username='fan')
    await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
async def staff():
    user = await make_user(discord_id=601, username='staffer')
    await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
def dms(monkeypatch):
    """Each DM's recipient, text and button, recorded as the queue would send them."""
    sent: list = []
    from application.services import discord as discord_pkg
    from application.services.discord import DiscordService

    async def fake_send(self, user_id, message, view_factory=None, embed=None, link=None,
                        on_sent=None, notice=None):
        sent.append({'to': user_id, 'text': message, 'link': link, 'notice': notice})
        if on_sent is not None:
            await on_sent(user_id, 1000 + len(sent))
        return True, ''

    monkeypatch.setattr(DiscordService, 'send_dm', fake_send)
    pending: list = []
    monkeypatch.setattr(discord_pkg.discord_queue, 'enqueue', pending.append)

    async def flush() -> list:
        """The DMs sent since the last flush."""
        while pending:
            await pending.pop(0)
        batch = list(sent)
        sent.clear()
        return batch

    return flush


async def _actions() -> list[str]:
    return [a.action for a in await AuditLog.all().order_by('id')]


# --- submitting ---------------------------------------------------------------


async def test_a_member_submission_waits_for_review_and_staff_are_told(member, staff, dms):
    event = await CommunityEventService().submit(member, _draft(title='  Board games  '))

    assert event.status is CommunityEventStatus.PENDING
    assert event.title == 'Board games'
    assert await _actions() == [AuditActions.COMMUNITY_EVENT_SUBMITTED]
    sent = await dms()
    assert [d['to'] for d in sent] == [601]
    assert 'Board games' in sent[0]['text']
    assert sent[0]['link'].url.endswith(f'/admin/community-events?community_event={event.id}')


async def test_a_staff_submission_is_listed_straight_away(staff, dms):
    event = await CommunityEventService().submit(staff, _draft())

    assert event.status is CommunityEventStatus.APPROVED
    assert event.reviewed_by_id == staff.id
    assert await _actions() == [
        AuditActions.COMMUNITY_EVENT_SUBMITTED, AuditActions.COMMUNITY_EVENT_APPROVED,
    ]
    assert await dms() == []


# --- posting for someone else ---------------------------------------------------


async def test_staff_can_post_an_event_with_a_member_as_host(member, staff, dms):
    event = await CommunityEventService().submit(staff, _draft(), host_id=member.id)

    assert event.host_id == member.id
    assert event.status is CommunityEventStatus.APPROVED
    assert event.reviewed_by_id == staff.id
    submitted = await AuditLog.filter(action=AuditActions.COMMUNITY_EVENT_SUBMITTED).first()
    assert submitted.user_id == staff.id
    assert json.loads(submitted.details)['on_behalf_of'] == member.id
    sent = await dms()
    assert [d['to'] for d in sent] == [600]
    assert 'staffer' in sent[0]['text'] and 'Board games' in sent[0]['text']
    assert sent[0]['notice'].key == 'community_event_posted_for_you'
    assert sent[0]['link'].url.endswith(f'/community-events/{event.id}')


async def test_the_member_staff_posted_for_can_edit_and_cancel_it(member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(staff, _draft(), host_id=member.id)

    await service.update(member, event.id, _draft(title='Card swap'))
    assert (await service.cancel(member, event.id)).status is CommunityEventStatus.CANCELLED
    assert [e.id for e in await service.list_mine(member)] == [event.id]


async def test_a_member_cannot_post_for_someone_else(member, other_member):
    with pytest.raises(PermissionError):
        await CommunityEventService().submit(member, _draft(), host_id=other_member.id)
    assert await CommunityEvent.all().count() == 0


async def test_naming_yourself_as_host_is_an_ordinary_submission(member, staff, dms):
    event = await CommunityEventService().submit(member, _draft(), host_id=member.id)
    assert event.host_id == member.id
    assert event.status is CommunityEventStatus.PENDING


async def test_the_host_must_belong_to_the_community(staff):
    outsider = await make_user(discord_id=700, username='outsider')
    with pytest.raises(NotFoundError):
        await CommunityEventService().submit(staff, _draft(), host_id=outsider.id)
    with pytest.raises(NotFoundError):
        await CommunityEventService().submit(staff, _draft(), host_id=999_999)
    assert await CommunityEvent.all().count() == 0


async def test_staff_can_hand_an_event_to_another_member(member, other_member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    await service.approve(staff, event.id)
    await dms()

    saved = await service.update(staff, event.id, _draft(), host_id=other_member.id)

    assert saved.host_id == other_member.id
    assert (await CommunityEvent.get(id=event.id)).host_id == other_member.id
    updated = await AuditLog.filter(action=AuditActions.COMMUNITY_EVENT_UPDATED).first()
    assert json.loads(updated.details)['previous_host_id'] == member.id
    sent = await dms()
    assert [d['to'] for d in sent] == [602]
    assert 'handed you' in sent[0]['text']


async def test_a_host_cannot_hand_their_event_to_someone_else(member, other_member):
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    with pytest.raises(PermissionError):
        await service.update(member, event.id, _draft(), host_id=other_member.id)
    assert (await CommunityEvent.get(id=event.id)).host_id == member.id


async def test_with_approval_turned_off_a_member_submission_is_listed_straight_away(member, staff, dms):
    await SystemConfigService.set_raw(KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL, 'false', staff)
    await AuditLog.all().delete()

    event = await CommunityEventService().submit(member, _draft())

    assert event.status is CommunityEventStatus.APPROVED
    assert event.reviewed_by_id is None
    assert await _actions() == [
        AuditActions.COMMUNITY_EVENT_SUBMITTED, AuditActions.COMMUNITY_EVENT_APPROVED,
    ]
    assert await dms() == []


async def test_with_approval_turned_off_a_taken_down_event_still_goes_back_to_review(member, staff, dms):
    await SystemConfigService.set_raw(KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL, 'false', staff)
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    await service.reject(staff, event.id, 'Wrong room')

    await service.update(member, event.id, _draft(description='Fixed'))

    await event.refresh_from_db()
    assert event.status is CommunityEventStatus.PENDING


async def test_a_non_member_cannot_submit():
    outsider = await make_user(discord_id=603, username='outsider')
    with pytest.raises(PermissionError):
        await CommunityEventService().submit(outsider, _draft())


async def test_a_super_admin_joins_first_like_everyone():
    admin = await make_user(discord_id=604, username='platform')
    await UserRole.create(user=admin, role=Role.SUPER_ADMIN, tenant=None)
    with pytest.raises(PermissionError, match='members'):
        await CommunityEventService().submit(admin, _draft())


@pytest.mark.parametrize('overrides, message', [
    ({'title': '   '}, 'title'),
    ({'title': 'x' * 121}, 'title'),
    ({'duration_minutes': 2}, 'between'),
    ({'duration_minutes': 73 * 60}, 'between'),
    ({'starts_at': datetime.now(timezone.utc) - timedelta(minutes=1)}, 'future'),
    ({'starts_at': datetime(2030, 1, 1, 12, 0)}, 'timezone'),
    ({'description': 'x' * 5001}, 'description'),
])
async def test_bad_submissions_are_refused(member, overrides, message):
    with pytest.raises(ValueError, match=message):
        await CommunityEventService().submit(member, _draft(**overrides))
    assert not await CommunityEvent.exists()


# --- visibility -----------------------------------------------------------------


async def test_only_approved_events_are_listed_and_pending_ones_stay_private(member, other_member, staff):
    service = CommunityEventService()
    pending = await service.submit(member, _draft(title='Pending'))
    listed = await service.submit(staff, _draft(title='Listed', starts_at=_soon(2)))

    assert [e.id for e in await service.list_upcoming(other_member)] == [listed.id]
    with pytest.raises(NotFoundError):
        await service.get_event(other_member, pending.id)
    assert (await service.get_event(member, pending.id)).id == pending.id
    assert (await service.get_event(staff, pending.id)).id == pending.id


async def test_an_event_in_progress_stays_listed_until_it_ends(staff, member):
    service = CommunityEventService()
    event = await service.submit(staff, _draft())
    await CommunityEvent.filter(id=event.id).update(
        starts_at=datetime.now(timezone.utc) - timedelta(minutes=30), duration_minutes=60,
    )
    assert [e.id for e in await service.list_upcoming(member)] == [event.id]

    await CommunityEvent.filter(id=event.id).update(duration_minutes=20)
    assert await service.list_upcoming(member) == []


# --- review -------------------------------------------------------------------


async def test_approving_lists_it_and_tells_the_host(member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    await dms()

    await service.approve(staff, event.id)

    await event.refresh_from_db()
    assert event.status is CommunityEventStatus.APPROVED
    sent = await dms()
    assert [d['to'] for d in sent] == [600]
    assert sent[0]['link'].url.endswith(f'/community-events/{event.id}')


async def test_rejecting_needs_a_reason_and_sends_it_to_the_host(member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    await dms()

    with pytest.raises(ValueError):
        await service.reject(staff, event.id, '  ')
    await service.reject(staff, event.id, 'Pick another room')

    await event.refresh_from_db()
    assert event.status is CommunityEventStatus.REJECTED
    assert event.review_note == 'Pick another room'
    sent = await dms()
    assert [d['to'] for d in sent] == [600]
    assert 'Pick another room' in sent[0]['text']


async def test_only_staff_review(member, other_member):
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    with pytest.raises(PermissionError):
        await service.approve(other_member, event.id)
    with pytest.raises(PermissionError):
        await service.reject(member, event.id, 'no')
    with pytest.raises(PermissionError):
        await service.list_for_review(member)


async def test_taking_a_listed_event_down_tells_its_subscribers(member, other_member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(staff, _draft())
    await service.subscribe(other_member, event.id)

    await service.reject(staff, event.id, 'Venue said no')

    recipients = sorted(d['to'] for d in await dms())
    assert recipients == [601, 602]  # the host (staff here) and the subscriber


# --- edits and cancellation ---------------------------------------------------


async def test_moving_a_listed_event_tells_subscribers_and_rearms_reminders(member, other_member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    await service.approve(staff, event.id)
    await service.subscribe(other_member, event.id)
    await CommunityEventSubscription.filter(event_id=event.id).update(reminder_sent_at=datetime.now(timezone.utc))
    await dms()

    await service.update(member, event.id, _draft(starts_at=_soon(8)))

    await event.refresh_from_db()
    assert event.status is CommunityEventStatus.APPROVED
    assert (await CommunityEventSubscription.get(event_id=event.id)).reminder_sent_at is None
    sent = await dms()
    assert [d['to'] for d in sent] == [602]
    assert 'moved' in sent[0]['text']


async def test_editing_text_alone_does_not_ping_subscribers(member, other_member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(staff, _draft())
    await service.subscribe(other_member, event.id)
    await dms()

    await service.update(staff, event.id, _draft(starts_at=event.starts_at, description='New words'))

    assert await dms() == []


async def test_editing_a_rejected_event_resubmits_it(member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    await service.reject(staff, event.id, 'Too vague')
    await dms()

    await service.update(member, event.id, _draft(description='Now with details'))

    await event.refresh_from_db()
    assert event.status is CommunityEventStatus.PENDING
    assert [d['to'] for d in await dms()] == [601]


async def test_only_the_host_or_staff_can_edit_or_cancel(member, other_member, staff):
    service = CommunityEventService()
    event = await service.submit(staff, _draft())
    with pytest.raises(PermissionError):
        await service.update(other_member, event.id, _draft())
    with pytest.raises(PermissionError):
        await service.cancel(other_member, event.id)


async def test_cancelling_a_listed_event_tells_subscribers_and_freezes_it(member, other_member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    await service.approve(staff, event.id)
    await service.subscribe(other_member, event.id)
    await dms()

    await service.cancel(member, event.id)

    await event.refresh_from_db()
    assert event.status is CommunityEventStatus.CANCELLED
    assert [d['to'] for d in await dms()] == [602]
    assert AuditActions.COMMUNITY_EVENT_CANCELLED in await _actions()
    with pytest.raises(ValueError):
        await service.update(member, event.id, _draft())
    with pytest.raises(ValueError):
        await service.approve(staff, event.id)


# --- subscriptions ------------------------------------------------------------


async def test_subscribing_is_idempotent_and_counted(member, other_member, staff):
    service = CommunityEventService()
    event = await service.submit(staff, _draft())

    await service.subscribe(other_member, event.id)
    await service.subscribe(other_member, event.id)
    await service.subscribe(member, event.id)

    mine, counts = await service.subscription_state(other_member, [event.id])
    assert mine == {event.id}
    assert counts == {event.id: 2}

    await service.unsubscribe(other_member, event.id)
    await service.unsubscribe(other_member, event.id)
    mine, counts = await service.subscription_state(other_member, [event.id])
    assert mine == set()
    assert counts == {event.id: 1}


async def test_you_cannot_subscribe_to_an_unlisted_event(member, other_member):
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    with pytest.raises(NotFoundError):
        await service.subscribe(other_member, event.id)
    with pytest.raises(ValueError):
        await service.subscribe(member, event.id)


async def test_the_flag_being_off_hides_everything(member, staff):
    await TenantFeatureFlag.filter(
        tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.COMMUNITY_EVENTS.value,
    ).update(enabled=False)
    service = CommunityEventService()
    with pytest.raises(FeatureDisabledError):
        await service.list_upcoming(member)
    with pytest.raises(FeatureDisabledError):
        await service.submit(member, _draft())
    with pytest.raises(FeatureDisabledError):
        await service.list_for_review(staff)


async def test_events_are_published_for_webhooks(member, staff, monkeypatch):
    from application.events import event_bus

    published: list = []
    monkeypatch.setattr(event_bus, 'publish', published.append)
    service = CommunityEventService()
    event = await service.submit(member, _draft())
    await service.approve(staff, event.id)

    assert [e.event_type for e in published] == [
        EventType.COMMUNITY_EVENT_SUBMITTED, EventType.COMMUNITY_EVENT_APPROVED,
    ]
    assert published[0].payload['host_id'] == member.id


# --- RSVPs --------------------------------------------------------------------


async def test_rsvps_respect_the_cap_and_count_as_a_reminder(member, other_member, staff):
    service = CommunityEventService()
    event = await service.submit(staff, _draft(rsvp_enabled=True, rsvp_capacity=1))

    await service.rsvp(other_member, event.id)
    await service.rsvp(other_member, event.id)
    with pytest.raises(ValueError, match='full'):
        await service.rsvp(member, event.id)

    going, counts = await service.rsvp_state(member, [event.id])
    assert going == set() and counts == {event.id: 1}
    subscribed, _ = await service.subscription_state(other_member, [event.id])
    assert subscribed == {event.id}
    assert [u.id for u in await service.list_attendees(staff, event.id)] == [other_member.id]

    await service.cancel_rsvp(other_member, event.id)
    await service.rsvp(member, event.id)
    assert (await service.rsvp_state(member, [event.id]))[0] == {event.id}


async def test_a_reminder_subscriber_can_rsvp_without_a_second_row(other_member, staff):
    service = CommunityEventService()
    event = await service.submit(staff, _draft(rsvp_enabled=True))
    await service.subscribe(other_member, event.id)

    await service.rsvp(other_member, event.id)

    rows = await CommunityEventSubscription.filter(event_id=event.id)
    assert [(r.user_id, r.going) for r in rows] == [(other_member.id, True)]


async def test_rsvps_need_the_host_to_open_them(other_member, staff):
    service = CommunityEventService()
    event = await service.submit(staff, _draft())
    with pytest.raises(ValueError, match='RSVP'):
        await service.rsvp(other_member, event.id)


async def test_only_the_host_or_staff_see_who_is_going(member, other_member, staff):
    service = CommunityEventService()
    event = await service.submit(member, _draft(rsvp_enabled=True))
    await service.approve(staff, event.id)
    await service.rsvp(other_member, event.id)

    assert [u.id for u in await service.list_attendees(member, event.id)] == [other_member.id]
    with pytest.raises(PermissionError):
        await service.list_attendees(other_member, event.id)


@pytest.mark.parametrize('cap', [0, 1001, 'many'])
async def test_a_bad_rsvp_cap_is_refused(member, cap):
    with pytest.raises(ValueError, match='cap'):
        await CommunityEventService().submit(member, _draft(rsvp_enabled=True, rsvp_capacity=cap))


async def test_a_cap_without_rsvps_is_dropped(member):
    event = await CommunityEventService().submit(member, _draft(rsvp_capacity=5))
    assert event.rsvp_enabled is False and event.rsvp_capacity is None


# --- taking a listed event down, RSVP caps and the approval DM -------------


async def test_a_taken_down_event_drops_its_subscribers_so_re_approval_does_not_remind_them(
    member, other_member, staff, dms,
):
    from application.repositories.community_event_repository import CommunityEventSubscriptionRepository

    service = CommunityEventService()
    event = await service.submit(member, _draft(starts_at=_soon(0.25)))
    await service.approve(staff, event.id)
    await service.subscribe(other_member, event.id)
    await dms()

    await service.reject(staff, event.id, 'Venue said no')
    assert 602 in [d['to'] for d in await dms()]
    assert await CommunityEventSubscription.filter(event_id=event.id).count() == 0

    await service.approve(staff, event.id)
    now = datetime.now(timezone.utc)
    assert await CommunityEventSubscriptionRepository.due_for_reminder(now, now + timedelta(hours=1)) == []


async def test_rejecting_a_pending_event_leaves_nothing_to_drop(member, staff):
    service = CommunityEventService()
    event = await service.submit(member, _draft())

    await service.reject(staff, event.id, 'Pick another room')

    assert await CommunityEventSubscription.filter(event_id=event.id).count() == 0


async def test_the_rsvp_cap_cannot_drop_below_who_is_already_going(member, other_member, staff):
    service = CommunityEventService()
    event = await service.submit(member, _draft(rsvp_enabled=True, rsvp_capacity=5))
    await service.approve(staff, event.id)
    await service.rsvp(other_member, event.id)
    await service.rsvp(staff, event.id)

    with pytest.raises(ValueError, match="2 people have already RSVP'd"):
        await service.update(member, event.id, _draft(starts_at=event.starts_at, rsvp_enabled=True, rsvp_capacity=1))
    await event.refresh_from_db()
    assert event.rsvp_capacity == 5

    await service.update(member, event.id, _draft(starts_at=event.starts_at, rsvp_enabled=True, rsvp_capacity=2))
    await event.refresh_from_db()
    assert event.rsvp_capacity == 2



async def test_turning_rsvps_off_ignores_the_cap_check(member, other_member, staff):
    service = CommunityEventService()
    event = await service.submit(staff, _draft(rsvp_enabled=True, rsvp_capacity=5))
    await service.rsvp(other_member, event.id)
    await service.rsvp(member, event.id)

    await service.update(staff, event.id, _draft(starts_at=event.starts_at))

    await event.refresh_from_db()
    assert event.rsvp_enabled is False and event.rsvp_capacity is None


@pytest.mark.parametrize('rsvp_enabled, says', [
    (False, 'subscribe to it for a reminder'),
    (True, 'RSVP to it, which also gets them a reminder'),
])
async def test_the_approval_dm_matches_how_members_sign_up(member, staff, dms, rsvp_enabled, says):
    service = CommunityEventService()
    event = await service.submit(member, _draft(rsvp_enabled=rsvp_enabled))
    await dms()

    await service.approve(staff, event.id)

    [sent] = await dms()
    assert says in sent['text']


# --- what's new since the last visit --------------------------------------------


async def test_everything_listed_is_new_until_the_listing_is_opened(member, other_member, staff, dms):
    service = CommunityEventService()
    theirs = await service.submit(staff, _draft(title='Panel'))
    await service.submit(other_member, _draft(title='Waiting'))

    assert await service.unseen_ids(member) == {theirs.id}

    await service.mark_seen(member)

    assert await service.unseen_ids(member) == set()


async def test_an_event_listed_after_the_last_visit_is_new_and_your_own_never_is(member, staff, dms):
    service = CommunityEventService()
    await service.mark_seen(member)
    mine = await service.submit(member, _draft(title='Mine'))
    await service.approve(staff, mine.id)
    later = await service.submit(staff, _draft(title='Later'))

    assert await service.unseen_ids(member) == {later.id}


async def test_an_event_put_back_after_a_takedown_is_new_again(member, staff, dms):
    service = CommunityEventService()
    event = await service.submit(staff, _draft())
    await service.mark_seen(member)
    await service.reject(staff, event.id, 'Wrong room')
    await service.mark_seen(member)

    await service.approve(staff, event.id)

    assert await service.unseen_ids(member) == {event.id}


async def test_whats_new_is_for_members_only(staff, dms):
    outsider = await make_user(discord_id=603, username='outsider')
    service = CommunityEventService()
    await service.submit(staff, _draft())

    for call in (service.unseen_ids(outsider), service.mark_seen(outsider)):
        with pytest.raises(PermissionError):
            await call


async def test_list_attending_says_how_each_event_is_attended(member, other_member, staff):
    service = CommunityEventService()
    start, end = _soon(0), _soon(48)
    await service.submit(staff, _draft(title='Hosted'))
    going = await service.submit(member, _draft(title='Going', rsvp_enabled=True, starts_at=_soon(6)))
    reminded = await service.submit(member, _draft(title='Reminded', starts_at=_soon(7)))
    later = await service.submit(member, _draft(title='Too late', starts_at=_soon(72)))
    await service.approve(staff, going.id)
    await service.approve(staff, reminded.id)
    await service.approve(staff, later.id)
    pending = await service.submit(other_member, _draft(title='Pending'))
    await service.rsvp(staff, going.id)
    for event in (reminded, later, pending):
        await CommunityEventSubscription.create(
            event_id=event.id, user=staff, tenant_id=DEFAULT_TEST_TENANT_ID,
        )

    attending = await service.list_attending(staff, start, end)

    assert [(e.title, how) for e, how in attending] == [
        ('Hosted', 'hosting'), ('Going', 'going'), ('Reminded', 'reminder'),
    ]
    assert (await service.list_attending(other_member, start, end)) == []


async def test_an_rsvp_counts_as_a_reminder_once_rsvps_close(member, staff):
    service = CommunityEventService()
    event = await service.submit(member, _draft(rsvp_enabled=True))
    await service.approve(staff, event.id)
    await service.rsvp(staff, event.id)
    await CommunityEvent.filter(id=event.id).update(rsvp_enabled=False)

    attending = await service.list_attending(staff, _soon(0), _soon(24))

    assert [how for _, how in attending] == ['reminder']


async def test_list_attending_keeps_an_event_still_running(member, staff):
    service = CommunityEventService()
    event = await service.submit(staff, _draft(duration_minutes=180))
    await CommunityEvent.filter(id=event.id).update(starts_at=_soon(-1))

    assert [e.id for e, _ in await service.list_attending(staff, _soon(0), _soon(24))] == [event.id]
    assert await service.list_attending(staff, _soon(3), _soon(24)) == []
