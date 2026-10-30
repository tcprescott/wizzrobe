"""Membership as a managed thing rather than a write-only table."""

import pytest

from application.repositories.user_role_repository import UserRoleRepository
from application.services.tenant_membership_service import TenantMembershipService
from application.tenant_context import tenant_scope
from models import Role, Tenant, TenantMembership, User


@pytest.fixture
async def staff(db):
    boss = await User.create(discord_id=4000, username='boss')
    await UserRoleRepository.add(boss, Role.STAFF)
    await TenantMembership.create(user=boss, tenant_id=1)
    return boss


async def test_add_member_requires_role_granting_authority(db):
    nobody = await User.create(discord_id=4001, username='nobody')
    target = await User.create(discord_id=4002, username='target')
    with pytest.raises(PermissionError):
        await TenantMembershipService().add_member(nobody, target)


async def test_add_member_is_idempotent(staff, db):
    target = await User.create(discord_id=4003, username='target')
    service = TenantMembershipService()
    await service.add_member(staff, target)
    await service.add_member(staff, target)
    assert await TenantMembership.filter(user=target, tenant_id=1).count() == 1


async def test_remove_member_refuses_while_roles_are_held(staff, db):
    with pytest.raises(ValueError) as exc:
        await TenantMembershipService().remove_member(staff, staff)
    assert 'revoke' in str(exc.value).lower()
    assert await TenantMembershipService().is_member(staff) is True


async def test_remove_member_succeeds_once_roles_are_revoked(staff, db):
    target = await User.create(discord_id=4004, username='target')
    service = TenantMembershipService()
    await UserRoleRepository.add(target, Role.PROCTOR)
    await service.add_member(staff, target)

    with pytest.raises(ValueError):
        await service.remove_member(staff, target)

    await UserRoleRepository.remove(target, Role.PROCTOR)
    await service.remove_member(staff, target)
    assert await service.is_member(target) is False


async def test_remove_member_reports_a_non_member(staff, db):
    stranger = await User.create(discord_id=4005, username='stranger')
    with pytest.raises(ValueError) as exc:
        await TenantMembershipService().remove_member(staff, stranger)
    assert 'not a member' in str(exc.value).lower()


async def test_list_members_returns_only_this_tenants_members(staff, db):
    other = await Tenant.create(name='Other', slug='other')
    here = await User.create(discord_id=4006, username='here')
    elsewhere = await User.create(discord_id=4007, username='elsewhere')
    await TenantMembershipService().add_member(staff, here)
    await TenantMembership.create(user=elsewhere, tenant=other)

    names = {u.username for u in await TenantMembershipService().list_members()}
    assert 'here' in names
    assert 'elsewhere' not in names

    with tenant_scope(other.id):
        names_there = {u.username for u in await TenantMembershipService().list_members()}
    assert names_there == {'elsewhere'}


async def test_membership_changes_are_audited_and_published(staff, db, monkeypatch):
    from application.events import EventType, event_bus

    published = []
    monkeypatch.setattr(event_bus, 'publish', lambda e: published.append(e))

    target = await User.create(discord_id=4008, username='target')
    service = TenantMembershipService()
    await service.add_member(staff, target)
    await service.remove_member(staff, target)

    from models import AuditLog
    actions = [row.action for row in await AuditLog.all()]
    assert 'tenant.member_added' in actions
    assert 'tenant.member_removed' in actions
    assert [e.event_type for e in published] == [
        EventType.TENANT_MEMBER_ADDED, EventType.TENANT_MEMBER_REMOVED,
    ]


async def test_ensure_member_is_idempotent_and_writes_no_audit(db):
    from models import AuditLog

    target = await User.create(discord_id=4009, username='target')
    await TenantMembershipService.ensure_member(target)
    await TenantMembershipService.ensure_member(target)
    assert await TenantMembership.filter(user=target, tenant_id=1).count() == 1
    assert await AuditLog.filter(action='tenant.member_added').count() == 0


async def test_addable_users_offers_non_members_only(staff, db):
    already = await User.create(discord_id=4010, username='already')
    await TenantMembershipService().add_member(staff, already)
    await User.create(discord_id=4011, username='outsider')

    names = {u.username for u in await TenantMembershipService().addable_users()}
    assert 'outsider' in names
    assert 'already' not in names
    assert 'boss' not in names


async def test_addable_users_never_offers_the_system_actor(staff, db):
    from application.repositories.user_repository import UserRepository

    await UserRepository.get_or_create_system_user()
    names = {u.username for u in await TenantMembershipService().addable_users()}
    assert 'System' not in names


async def _age_decision(request_id: int, *, days: int) -> None:
    """Move a decision ``days`` into the past, past (or inside) the cooldown."""
    from datetime import datetime, timedelta, timezone

    from models import TenantJoinRequest

    await TenantJoinRequest.filter(id=request_id).update(
        decided_at=datetime.now(timezone.utc) - timedelta(days=days),
    )


@pytest.fixture
def captured_dms(monkeypatch):
    """Collect what would have been DM'd, without a Discord connection."""
    sent = []
    from application.services import discord as discord_pkg

    def _enqueue(coro):
        coro.close()
        if coro.__name__ != '_edit_all':
            sent.append(True)

    monkeypatch.setattr(discord_pkg.discord_queue, 'enqueue', _enqueue)
    return sent


async def test_request_to_join_is_refused_for_an_existing_member(staff, db):
    outsider = await User.create(discord_id=4100, username='outsider')
    await TenantMembershipService().add_member(staff, outsider)
    with pytest.raises(ValueError) as exc:
        await TenantMembershipService().request_to_join(outsider, 1)
    assert 'already a member' in str(exc.value)


async def test_a_denied_request_can_be_reopened_not_duplicated(staff, db):
    from models import JoinRequestStatus, TenantJoinRequest

    outsider = await User.create(discord_id=4101, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1, 'let me in')
    await service.deny_request(staff, request.id)
    await _age_decision(request.id, days=8)

    reopened = await service.request_to_join(outsider, 1, 'asking again')
    assert reopened.id == request.id
    assert reopened.status is JoinRequestStatus.PENDING
    assert reopened.message == 'asking again'
    assert reopened.decided_by_id is None and reopened.decided_at is None
    assert await TenantJoinRequest.filter(user=outsider, tenant_id=1).count() == 1


async def test_asking_again_while_pending_does_not_re_notify_staff(staff, db, monkeypatch):
    """Every click DM'd every staff member, and a client can keep clicking."""
    from unittest.mock import AsyncMock

    notify = AsyncMock()
    monkeypatch.setattr(TenantMembershipService, '_notify_staff_of_request', notify)
    outsider = await User.create(discord_id=4110, username='outsider')
    service = TenantMembershipService()

    await service.request_to_join(outsider, 1, 'let me in')
    again = await service.request_to_join(outsider, 1, 'please')

    assert notify.await_count == 1
    assert again.message == 'please'


async def test_remove_member_refuses_a_tournament_admin(staff, db):
    """A per-tournament grant left behind comes back with any re-admission."""
    from models import Tournament

    target = await User.create(discord_id=4111, username='ta')
    service = TenantMembershipService()
    await service.add_member(staff, target)
    tournament = await Tournament.create(name='Cup')
    await tournament.admins.add(target)

    with pytest.raises(ValueError, match='tournament admin'):
        await service.remove_member(staff, target)
    assert await service.is_member(target) is True

    await tournament.admins.remove(target)
    await service.remove_member(staff, target)
    assert await service.is_member(target) is False


async def test_approving_creates_the_membership(staff, db):
    outsider = await User.create(discord_id=4102, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    await service.approve_request(staff, request.id)
    assert await service.is_member(outsider) is True


async def test_denying_creates_no_membership(staff, db):
    outsider = await User.create(discord_id=4103, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    await service.deny_request(staff, request.id)
    assert await service.is_member(outsider) is False


async def test_a_decided_request_cannot_be_decided_twice(staff, db):
    outsider = await User.create(discord_id=4104, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    await service.approve_request(staff, request.id)
    with pytest.raises(ValueError) as exc:
        await service.deny_request(staff, request.id)
    assert 'already been decided' in str(exc.value)


async def test_decisions_require_role_granting_authority(staff, db):
    outsider = await User.create(discord_id=4105, username='outsider')
    nobody = await User.create(discord_id=4106, username='nobody')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    with pytest.raises(PermissionError):
        await service.approve_request(nobody, request.id)
    with pytest.raises(PermissionError):
        await service.deny_request(nobody, request.id)


async def test_another_communitys_request_is_not_found_not_forbidden(staff, db):
    """The message must not confirm that another community's request exists."""
    from application.errors import NotFoundError

    other = await Tenant.create(name='Other', slug='other')
    outsider = await User.create(discord_id=4107, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, other.id)

    with pytest.raises(NotFoundError):
        await service.approve_request(staff, request.id)


async def test_list_pending_is_scoped_and_excludes_decided(staff, db):
    other = await Tenant.create(name='Other', slug='other')
    here = await User.create(discord_id=4108, username='here')
    there = await User.create(discord_id=4109, username='there')
    decided = await User.create(discord_id=4110, username='decided')
    service = TenantMembershipService()

    await service.request_to_join(here, 1)
    await service.request_to_join(there, other.id)
    gone = await service.request_to_join(decided, 1)
    await service.deny_request(staff, gone.id)

    pending = await service.list_pending()
    assert [r.user.username for r in pending] == ['here']


async def test_the_join_audit_row_is_stamped_with_the_target_tenant(db):
    """The requester is not scoped to the tenant they are asking to join.

    Without the explicit scope the row would land in whatever community the
    requester happened to be looking at — or nowhere.
    """
    from models import AuditLog

    other = await Tenant.create(name='Other', slug='other')
    outsider = await User.create(discord_id=4111, username='outsider')
    await TenantMembershipService().request_to_join(outsider, other.id)

    row = await AuditLog.get(action='tenant.join_requested')
    assert row.tenant_id == other.id
    assert row.user_id == outsider.id


async def test_an_over_long_message_is_refused_not_truncated(db):
    outsider = await User.create(discord_id=4112, username='outsider')
    with pytest.raises(ValueError) as exc:
        await TenantMembershipService().request_to_join(outsider, 1, 'x' * 501)
    assert '500' in str(exc.value)


async def test_staff_are_notified_of_a_request(staff, captured_dms, db):
    outsider = await User.create(discord_id=4113, username='outsider')
    await TenantMembershipService().request_to_join(outsider, 1, 'hello')
    assert len(captured_dms) == 1


async def test_the_requester_is_notified_on_approve_and_on_deny(staff, captured_dms, db):
    one = await User.create(discord_id=4114, username='one')
    two = await User.create(discord_id=4115, username='two')
    service = TenantMembershipService()
    r1 = await service.request_to_join(one, 1)
    r2 = await service.request_to_join(two, 1)
    captured_dms.clear()

    await service.approve_request(staff, r1.id)
    assert len(captured_dms) == 1
    await service.deny_request(staff, r2.id)
    assert len(captured_dms) == 2


async def _close_requests() -> None:
    from application.services.system_config_service import KEY_JOIN_REQUESTS
    from models import SystemConfiguration

    await SystemConfiguration.create(name=KEY_JOIN_REQUESTS, value='false', tenant_id=1)


async def test_requests_switched_off_refuse_a_new_request(captured_dms, db):
    from models import TenantJoinRequest

    await _close_requests()
    outsider = await User.create(discord_id=4300, username='outsider')
    with pytest.raises(ValueError) as exc:
        await TenantMembershipService().request_to_join(outsider, 1, 'let me in')
    assert 'join requests' in str(exc.value)
    assert not await TenantJoinRequest.exists(user=outsider, tenant_id=1)
    assert captured_dms == []


async def test_requests_switched_off_leave_the_pending_queue_decidable(staff, captured_dms, db):
    outsider = await User.create(discord_id=4301, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    await _close_requests()

    assert [r.id for r in await service.list_pending()] == [request.id]
    await service.approve_request(staff, request.id)
    assert await service.is_member(outsider) is True


@pytest.fixture
def dm_log(monkeypatch):
    """Each DM's recipient, text and button, without a Discord connection."""
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
        while pending:
            await pending.pop(0)
        return sent

    return flush


async def _pending_request(user_id: int):
    from models import TenantJoinRequest

    return await TenantJoinRequest.get(user_id=user_id, tenant_id=1)


async def test_add_member_closes_a_pending_request_and_tells_them(staff, dm_log, db):
    from models import JoinRequestStatus

    outsider = await User.create(discord_id=4400, username='outsider')
    service = TenantMembershipService()
    await service.request_to_join(outsider, 1)
    (await dm_log()).clear()

    await service.add_member(staff, outsider)

    request = await _pending_request(outsider.id)
    assert request.status is JoinRequestStatus.APPROVED
    assert request.decided_by_id == staff.id
    assert await service.list_pending() == []
    sent = await dm_log()
    assert [d['to'] for d in sent] == [4400]
    assert 'added you' in sent[0]['text']
    assert sent[0]['link'] is not None


async def test_a_role_grant_closes_a_pending_request_and_tells_them(staff, dm_log, db):
    from application.services.user_service import UserService
    from models import JoinRequestStatus

    outsider = await User.create(discord_id=4401, username='outsider')
    await TenantMembershipService().request_to_join(outsider, 1)
    (await dm_log()).clear()

    await UserService().grant_role(outsider, Role.PROCTOR, staff)

    assert (await _pending_request(outsider.id)).status is JoinRequestStatus.APPROVED
    sent = [d for d in await dm_log() if d['to'] == 4401]
    assert any('request to join is closed' in d['text'] for d in sent)


async def test_ensure_member_without_a_request_dms_nobody(dm_log, db):
    outsider = await User.create(discord_id=4402, username='outsider')
    await TenantMembershipService.ensure_member(outsider)
    assert await dm_log() == []


async def test_declining_someone_already_a_member_is_refused(staff, dm_log, db):
    from models import JoinRequestStatus, TenantJoinRequest

    outsider = await User.create(discord_id=4403, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    await TenantMembership.create(user=outsider, tenant_id=1)
    (await dm_log()).clear()

    with pytest.raises(ValueError) as exc:
        await service.deny_request(staff, request.id)

    assert 'already a member' in str(exc.value)
    assert (await TenantJoinRequest.get(id=request.id)).status is JoinRequestStatus.PENDING
    assert await dm_log() == []
    await service.approve_request(staff, request.id)
    assert await service.list_pending() == []


async def test_a_declined_requester_waits_out_the_cooldown(staff, captured_dms, db):
    from application.services.tenant_membership_service import JOIN_REQUEST_COOLDOWN

    outsider = await User.create(discord_id=4404, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    await service.deny_request(staff, request.id)
    captured_dms.clear()

    allowed = await service.next_request_allowed_at(outsider, 1)
    assert allowed is not None
    with pytest.raises(ValueError) as exc:
        await service.request_to_join(outsider, 1, 'again')
    assert 'can’t ask again until' in str(exc.value)
    assert captured_dms == []

    await _age_decision(request.id, days=JOIN_REQUEST_COOLDOWN.days - 1)
    assert await service.next_request_allowed_at(outsider, 1) is not None
    await _age_decision(request.id, days=JOIN_REQUEST_COOLDOWN.days + 1)
    assert await service.next_request_allowed_at(outsider, 1) is None
    await service.request_to_join(outsider, 1, 'again')


async def test_the_cooldown_is_per_community(staff, db):
    other = await Tenant.create(name='Other', slug='other-cooldown')
    outsider = await User.create(discord_id=4405, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    with tenant_scope(1):
        await service.deny_request(staff, request.id)

    assert await service.next_request_allowed_at(outsider, other.id) is None
    await service.request_to_join(outsider, other.id)


async def test_the_decline_dm_says_when_and_links_the_invite(staff, dm_log, db):
    from application.services.system_config_service import KEY_DISCORD_INVITE_URL
    from models import SystemConfiguration

    await SystemConfiguration.create(
        name=KEY_DISCORD_INVITE_URL, value='https://discord.gg/abc123', tenant_id=1,
    )
    outsider = await User.create(discord_id=4406, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    (await dm_log()).clear()

    await service.deny_request(staff, request.id)

    [dm] = await dm_log()
    assert dm['to'] == 4406
    assert 'You can ask again from <t:' in dm['text']
    assert dm['link'].url == 'https://discord.gg/abc123'


async def test_the_decline_dm_has_no_button_without_an_invite(staff, dm_log, db):
    outsider = await User.create(discord_id=4407, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    (await dm_log()).clear()

    await service.deny_request(staff, request.id)

    [dm] = await dm_log()
    assert dm['link'] is None


async def test_removing_a_member_tells_them(staff, dm_log, db):
    outsider = await User.create(discord_id=4408, username='outsider')
    service = TenantMembershipService()
    await service.add_member(staff, outsider)
    (await dm_log()).clear()

    await service.remove_member(staff, outsider)

    [dm] = await dm_log()
    assert dm['to'] == 4408
    assert 'removed you' in dm['text']


async def _auto_join_on() -> None:
    from application.services.system_config_service import KEY_DISCORD_AUTO_JOIN
    from models import SystemConfiguration

    await SystemConfiguration.create(name=KEY_DISCORD_AUTO_JOIN, value='true', tenant_id=1)
    await Tenant.filter(id=1).update(discord_guild_id=555_000_000_000_000_001)


async def test_the_removal_dm_holds_even_with_auto_join(staff, dm_log, db):
    service = TenantMembershipService()
    await _auto_join_on()
    two = await User.create(discord_id=4501, username='two')
    await service.add_member(staff, two)
    (await dm_log()).clear()
    await service.remove_member(staff, two)
    [dm] = await dm_log()
    assert 'no longer open its pages' in dm['text']
    assert 'back in the next time you open it' not in dm['text']


async def test_the_decline_dm_leads_with_the_server_when_auto_join_is_on(staff, dm_log, db):
    await _auto_join_on()
    outsider = await User.create(discord_id=4502, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    (await dm_log()).clear()

    await service.deny_request(staff, request.id)

    [dm] = await dm_log()
    assert 'joining the server gets you in' in dm['text']
    assert 'send a new request from <t:' in dm['text']
    assert ':f>' in dm['text']


async def test_the_cooldown_refusal_says_when(staff, captured_dms, db):
    outsider = await User.create(discord_id=4503, username='outsider')
    service = TenantMembershipService()
    request = await service.request_to_join(outsider, 1)
    await service.deny_request(staff, request.id)
    with pytest.raises(ValueError) as exc:
        await service.request_to_join(outsider, 1)
    assert 'until 20' in str(exc.value)


async def test_a_staff_role_grant_records_the_request_it_closed(staff, dm_log, db, monkeypatch):
    import json

    from application.events import event_bus
    from application.services.user_service import UserService
    from models import AuditLog, JoinRequestStatus, MembershipSource

    published: list = []
    monkeypatch.setattr(event_bus, 'publish', published.append)
    outsider = await User.create(discord_id=4504, username='outsider')
    request = await TenantMembershipService().request_to_join(outsider, 1)
    published.clear()

    await UserService().grant_role(outsider, Role.PROCTOR, staff)

    await request.refresh_from_db()
    assert request.status is JoinRequestStatus.APPROVED
    assert request.decided_by_id == staff.id
    row = await AuditLog.get(action='user.role_granted', user=staff)
    assert json.loads(row.details)['closed_request_id'] == request.id
    approved = [e for e in published if e.event_type == 'tenant.join_approved']
    assert len(approved) == 1 and approved[0].payload['request_id'] == request.id
    membership = await TenantMembership.get(user=outsider, tenant_id=1)
    assert membership.source is MembershipSource.ROLE_GRANT


async def test_ensure_member_returns_nothing_without_a_request(db):
    outsider = await User.create(discord_id=4505, username='outsider')
    assert await TenantMembershipService.ensure_member(outsider) is None


async def test_each_way_in_records_its_source(staff, captured_dms, db):
    from models import MembershipSource

    service = TenantMembershipService()
    added = await User.create(discord_id=4506, username='added')
    await service.add_member(staff, added)
    asked = await User.create(discord_id=4507, username='asked')
    request = await service.request_to_join(asked, 1)
    await service.approve_request(staff, request.id)

    rows = await service.memberships_by_user()
    assert rows[added.id].source is MembershipSource.STAFF
    assert rows[asked.id].source is MembershipSource.JOIN_REQUEST
    assert rows[added.id].created_at is not None
