"""Patreon patron badges: both sign-ins, token refresh, the webhook, status changes, sync, cleanup, walls."""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from application.errors import NotFoundError
from application.services import _patreon_oauth as oauth
from application.services.audit_service import AuditActions
from application.services.feature_flag_service import reset_flag_cache
from application.services.member_details_service import MemberDetailsService
from application.services.members_admin_service import MembersAdminService
from application.services.patreon_campaign_service import PatreonCampaignService, PatreonSignatureError
from application.services.patreon_link_service import PatreonLinkService, PatreonOAuthService
from application.services.patron_service import PatronService
from application.tenant_context import tenant_scope
from application.utils.clients.patreon_client import ACTIVE_PATRON, MemberStatus, signature_ok
from application.utils.mocks import mock_patreon
from application.utils.mocks.mock_patreon import MOCK_CAMPAIGN_ID, MOCK_WEBHOOK_SECRET
from application.utils.secret_box import open_
from models import (
    AuditLog,
    FeatureFlag,
    PatreonCampaign,
    PatreonMembership,
    PatreonOAuthState,
    PatreonTierBadge,
    Role,
    TenantFeatureFlag,
    TenantMembership,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_member, make_user

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def mock_patreon_on(monkeypatch):
    monkeypatch.setenv('MOCK_PATREON', '1')


@pytest.fixture
async def staff():
    user = await make_member(800, 'staffer')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
async def player():
    return await make_member(801, 'player')


def _state(url: str) -> str:
    return parse_qs(urlparse(url).query)['state'][0]


async def _connect(staff) -> PatreonCampaign:
    url = await PatreonCampaignService().start_connect(staff)
    back = await PatreonOAuthService().finish(_state(url), 'mock', None)
    assert 'patreon=connected' in back, back
    return await PatreonCampaign.get(tenant_id=DEFAULT_TEST_TENANT_ID)


async def _link(user) -> str:
    url = await PatreonLinkService().start_link(user)
    back = await PatreonOAuthService().finish(_state(url), 'mock', None)
    await user.refresh_from_db()
    return back


def _signed(body: bytes, secret: str = MOCK_WEBHOOK_SECRET) -> dict:
    return {'X-Patreon-Signature': hmac.new(secret.encode(), body, hashlib.md5).hexdigest(),
            'X-Patreon-Event': 'members:update'}


def _webhook(status, patreon_user_id='p-1001', tiers=('t-hero',), campaign=MOCK_CAMPAIGN_ID) -> bytes:
    return json.dumps({'data': {
        'type': 'member', 'id': 'm-1',
        'attributes': {'patron_status': status, 'currently_entitled_amount_cents': 500},
        'relationships': {
            'user': {'data': {'type': 'user', 'id': patreon_user_id}},
            'campaign': {'data': {'type': 'campaign', 'id': campaign}},
            'currently_entitled_tiers': {'data': [{'type': 'tier', 'id': t} for t in tiers]},
        },
    }}).encode()


async def _flag_off() -> None:
    await TenantFeatureFlag.filter(flag=FeatureFlag.PATREON.value).update(enabled=False)
    reset_flag_cache()


async def _actions(action: str) -> int:
    return await AuditLog.filter(action=action).count()


# ── creator sign-in ───────────────────────────────────────────────────


async def test_staff_connect_seals_tokens_registers_the_webhook_and_queues_a_sync(staff):
    campaign = await _connect(staff)
    assert campaign.campaign_id == MOCK_CAMPAIGN_ID and campaign.campaign_name == 'Mock Campaign'
    assert 'mock-access' not in campaign.access_token and 'mock-refresh' not in (campaign.refresh_token or '')
    assert oauth.open_for(campaign, campaign.access_token) == 'mock-access-mock'
    assert oauth.open_for(campaign, campaign.webhook_secret, oauth.WEBHOOK_PURPOSE) == MOCK_WEBHOOK_SECRET
    assert open_(campaign.access_token, purpose=oauth.TOKEN_PURPOSE, context='999') is None
    assert campaign.webhook_id == 'mock-webhook' and campaign.sync_requested_at is not None
    assert [t.tier_title for t in await PatreonTierBadge.all().order_by('sort')] == ['Hero', 'Supporter']
    assert await _actions(AuditActions.PATREON_CONNECTED) == 1
    assert await PatreonOAuthState.all().count() == 0


async def test_a_sign_in_state_works_once(staff):
    url = await PatreonCampaignService().start_connect(staff)
    state = _state(url)
    assert 'patreon=connected' in await PatreonOAuthService().finish(state, 'mock', None)
    assert 'patreon=expired' in await PatreonOAuthService().finish(state, 'mock', None)
    assert 'patreon=expired' in await PatreonOAuthService().finish('made-up', 'mock', None)


async def test_cancelled_and_lapsed_sign_ins_change_nothing(staff):
    url = await PatreonCampaignService().start_connect(staff)
    assert 'patreon=cancelled' in await PatreonOAuthService().finish(_state(url), None, 'access_denied')
    url = await PatreonCampaignService().start_connect(staff)
    await PatreonOAuthState.all().update(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert 'patreon=expired' in await PatreonOAuthService().finish(_state(url), 'mock', None)
    assert await PatreonCampaign.all().count() == 0


async def test_non_staff_cannot_connect_or_see_the_campaign(player, staff):
    with pytest.raises(PermissionError):
        await PatreonCampaignService().start_connect(player)
    with pytest.raises(PermissionError):
        await PatreonCampaignService().overview(player)
    with pytest.raises(PermissionError):
        await PatreonCampaignService().finish_connect(player, 'mock')
    await _connect(staff)
    with pytest.raises(PermissionError):
        await PatreonCampaignService().disconnect(player)


async def test_another_communitys_staff_see_only_their_own(two_tenants, staff):
    _, tenant_b = two_tenants
    await _connect(staff)
    other = await make_user(850, 'other-staff')
    await TenantMembership.create(user=other, tenant_id=tenant_b.id)
    await UserRole.create(user=other, role=Role.STAFF, tenant_id=tenant_b.id)
    with tenant_scope(tenant_b.id):
        view = await PatreonCampaignService().overview(other)
        assert view.connected is False
        with pytest.raises(ValueError):
            await PatreonCampaignService().disconnect(other)
    assert await PatreonCampaign.all().count() == 1


# ── member link and the entitlement ───────────────────────────────────


async def test_link_keeps_only_the_patreon_id_and_grants_the_badge(staff, player):
    await _connect(staff)
    back = await _link(player)
    assert 'patreon=linked' in back
    await player.refresh_from_db()
    assert player.patreon_user_id == 'p-1001' and player.patreon_username == 'Mock Patron'
    status = await PatronService().get_patron_status(player)
    assert status.is_patron and status.badge_label == 'Patron' and status.tier_ids == ('t-hero',)
    assert await _actions(AuditActions.PATREON_LINKED) == 1
    assert await _actions(AuditActions.PATREON_BADGE_GRANTED) == 1
    membership = await PatreonMembership.get(user_id=player.id)
    assert 'mock-access' not in json.dumps(membership.tier_ids)

    await PatreonCampaignService().set_tier_labels(staff, [('t-hero', 'Hero patron'), ('t-supporter', '')])
    assert (await PatronService().get_patron_status(player)).badge_label == 'Hero patron'


async def test_one_patreon_account_links_to_one_wizzrobe_account(staff, player):
    await _connect(staff)
    await _link(player)
    second = await make_member(802, 'second')
    assert 'patreon=taken' in await _link(second)
    await second.refresh_from_db()
    assert second.patreon_user_id is None


async def test_link_before_the_membership_shows_queues_a_sync(staff, player, monkeypatch):
    async def no_memberships(self, *, with_memberships=False):
        return mock_patreon.Identity('p-1001', 'Mock Patron', ())

    monkeypatch.setattr(mock_patreon.MockPatreonClient, 'identity', no_memberships)
    campaign = await _connect(staff)
    await PatreonCampaignService().sync_if_due(staff, force=True)
    campaign = await PatreonCampaign.get(id=campaign.id)
    assert campaign.sync_requested_at is None
    await _link(player)
    assert (await PatreonCampaign.get(id=campaign.id)).sync_requested_at is not None
    assert not (await PatronService().get_patron_status(player)).is_patron
    await PatreonCampaignService().sync_if_due(staff)
    assert (await PatronService().get_patron_status(player)).is_patron


async def test_hidden_badges_drop_out_of_the_member_list_but_still_count(staff, player):
    await _connect(staff)
    await _link(player)
    assert (await PatronService().visible_badges()) == {player.id: 'Patron'}
    rows = {r.id: r for r in (await MembersAdminService().board(staff)).members}
    assert rows[player.id].patron_badge == 'Patron'
    await PatreonLinkService().set_badge_hidden(player, True)
    assert await PatronService().visible_badges() == {}
    status = await PatronService().get_patron_status(player)
    assert status.is_patron and status.badge_hidden


async def test_member_details_carry_patron_status(staff, player):
    await _connect(staff)
    await _link(player)
    details = await MemberDetailsService().get(player.id, actor=staff)
    assert details.patron is not None and details.patron.is_patron


async def test_flag_off_reads_as_not_a_patron(staff, player):
    await _connect(staff)
    await _link(player)
    await _flag_off()
    assert not (await PatronService().get_patron_status(player)).is_patron
    assert await PatronService().visible_badges() == {}
    with pytest.raises(NotFoundError):
        await PatreonCampaignService().overview(staff)


# ── webhook ───────────────────────────────────────────────────────────


async def test_webhook_signature_is_checked_before_anything_changes(staff, player):
    campaign = await _connect(staff)
    await _link(player)
    body = _webhook('declined_patron')
    service = PatreonCampaignService()
    with pytest.raises(PatreonSignatureError):
        await service.handle_webhook(campaign.webhook_token, body, _signed(body, 'wrong-secret'))
    with pytest.raises(PatreonSignatureError):
        await service.handle_webhook(campaign.webhook_token, body, {'X-Patreon-Event': 'members:update'})
    with pytest.raises(NotFoundError):
        await service.handle_webhook('not-a-token', body, _signed(body))
    assert (await PatronService().get_patron_status(player)).is_patron


async def test_status_moves_active_declined_former_with_one_revoke(staff, player):
    campaign = await _connect(staff)
    await _link(player)
    service = PatreonCampaignService()
    for status in ('declined_patron', 'former_patron'):
        body = _webhook(status)
        assert await service.handle_webhook(campaign.webhook_token, body, _signed(body)) in ('badge changed', 'recorded')
        assert not (await PatronService().get_patron_status(player)).is_patron
        assert (await PatreonMembership.get(user_id=player.id)).patron_status == status
    assert await _actions(AuditActions.PATREON_BADGE_REVOKED) == 1
    body = _webhook(ACTIVE_PATRON)
    assert await service.handle_webhook(campaign.webhook_token, body, _signed(body)) == 'badge changed'
    assert await _actions(AuditActions.PATREON_BADGE_GRANTED) == 2


async def test_member_delete_and_strangers(staff, player):
    campaign = await _connect(staff)
    await _link(player)
    service = PatreonCampaignService()
    stranger = _webhook(ACTIVE_PATRON, patreon_user_id='p-9999')
    assert await service.handle_webhook(campaign.webhook_token, stranger, _signed(stranger)) == 'not linked'
    other = _webhook(ACTIVE_PATRON, campaign='someone-else')
    assert await service.handle_webhook(campaign.webhook_token, other, _signed(other)) == 'other campaign'
    gone = _webhook(ACTIVE_PATRON)
    headers = {**_signed(gone), 'X-Patreon-Event': 'members:delete'}
    assert await service.handle_webhook(campaign.webhook_token, gone, headers) == 'badge changed'
    assert await PatreonMembership.filter(user_id=player.id).count() == 0


async def test_webhook_with_the_flag_off_is_acknowledged_and_ignored(staff, player):
    campaign = await _connect(staff)
    await _link(player)
    await _flag_off()
    body = _webhook('former_patron')
    assert await PatreonCampaignService().handle_webhook(campaign.webhook_token, body, _signed(body)) == 'disabled'
    assert (await PatreonMembership.get(user_id=player.id)).patron_status == ACTIVE_PATRON


def test_signature_check():
    body = b'{"data": {}}'
    good = hmac.new(b'secret', body, hashlib.md5).hexdigest()
    assert signature_ok('secret', body, good)
    assert signature_ok('secret', body, good.upper())
    assert not signature_ok('secret', body + b' ', good)
    assert not signature_ok('other', body, good)
    assert not signature_ok(None, body, good)
    assert not signature_ok('secret', body, None)


# ── token refresh and sync ────────────────────────────────────────────


async def test_an_expiring_token_is_refreshed_once_under_concurrency(staff, monkeypatch):
    campaign = await _connect(staff)
    await PatreonCampaign.filter(id=campaign.id).update(token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    calls = []
    original = mock_patreon.MockPatreonOAuthClient.refresh

    async def counting(self, refresh_token):
        calls.append(refresh_token)
        await asyncio.sleep(0)
        return await original(self, refresh_token)

    monkeypatch.setattr(mock_patreon.MockPatreonOAuthClient, 'refresh', counting)
    service = PatreonCampaignService()
    first, second = await asyncio.gather(
        service._access_token(await PatreonCampaign.get(id=campaign.id)),
        service._access_token(await PatreonCampaign.get(id=campaign.id)),
    )
    assert first == second == 'mock-access-refreshed'
    assert calls == ['mock-refresh']
    refreshed = await PatreonCampaign.get(id=campaign.id)
    assert oauth.open_for(refreshed, refreshed.refresh_token) == 'mock-refresh-2'
    assert refreshed.token_expires_at > datetime.now(timezone.utc) + timedelta(days=20)


async def test_a_refused_refresh_asks_staff_to_reconnect(staff, monkeypatch):
    from application.utils.clients.patreon_client import PatreonAuthError

    campaign = await _connect(staff)
    await PatreonCampaign.filter(id=campaign.id).update(token_expires_at=datetime.now(timezone.utc))

    async def refused(self, refresh_token):
        raise PatreonAuthError('nope', 401)

    monkeypatch.setattr(mock_patreon.MockPatreonOAuthClient, 'refresh', refused)
    assert await PatreonCampaignService().sync_if_due(staff, force=True) is None
    assert 'Reconnect' in (await PatreonCampaign.get(id=campaign.id)).last_sync_error


async def test_sync_pages_through_members_and_fixes_drift(staff, player, monkeypatch):
    campaign = await _connect(staff)
    await _link(player)
    second = await make_member(803, 'second', patreon_user_id='p-1002')
    await PatreonMembership.create(
        tenant_id=DEFAULT_TEST_TENANT_ID, user=second, patron_status=ACTIVE_PATRON, tier_ids=[],
        synced_at=datetime.now(timezone.utc),
    )
    leaver = await make_user(804, 'leaver', patreon_user_id='p-1003')
    await PatreonMembership.create(
        tenant_id=DEFAULT_TEST_TENANT_ID, user=leaver, patron_status=ACTIVE_PATRON, tier_ids=[],
        synced_at=datetime.now(timezone.utc),
    )
    pages = {
        None: ([MemberStatus('m-1', 'p-1001', MOCK_CAMPAIGN_ID, ACTIVE_PATRON, ('t-hero',))], 'page-2'),
        'page-2': ([MemberStatus('m-2', 'p-1002', MOCK_CAMPAIGN_ID, 'declined_patron', ())], None),
    }
    seen = []

    async def paged(self, campaign_id, cursor=None):
        seen.append(cursor)
        return pages[cursor]

    monkeypatch.setattr(mock_patreon.MockPatreonClient, 'members_page', paged)
    monkeypatch.setattr('application.services.patreon_campaign_service.PAGE_PAUSE_SECONDS', 0)
    changed = await PatreonCampaignService().sync_if_due(staff, force=True)
    assert seen == [None, 'page-2']
    assert changed == 2
    assert (await PatronService().get_patron_status(player)).is_patron
    assert not (await PatronService().get_patron_status(second)).is_patron
    assert await PatreonMembership.filter(user_id=leaver.id).count() == 0
    synced = await PatreonCampaign.get(id=campaign.id)
    assert synced.last_synced_at is not None and synced.sync_requested_at is None and synced.last_sync_error is None


async def test_sync_waits_until_due(staff):
    await _connect(staff)
    assert await PatreonCampaignService().sync_if_due(staff) is not None
    assert await PatreonCampaignService().sync_if_due(staff) is None


# ── cleanup ───────────────────────────────────────────────────────────


async def test_disconnect_removes_webhook_tokens_labels_and_badges(staff, player, monkeypatch):
    deleted = []

    async def delete(self, webhook_id):
        deleted.append(webhook_id)

    monkeypatch.setattr(mock_patreon.MockPatreonClient, 'delete_webhook', delete)
    await _connect(staff)
    await _link(player)
    assert await PatreonCampaignService().disconnect(staff) == 1
    assert deleted == ['mock-webhook']
    assert await PatreonCampaign.all().count() == 0
    assert await PatreonTierBadge.all().count() == 0
    assert await PatreonMembership.all().count() == 0
    assert not (await PatronService().get_patron_status(player)).is_patron
    log = await AuditLog.get(action=AuditActions.PATREON_DISCONNECTED)
    assert 'mock-access' not in json.dumps(log.details) and 'secret' not in json.dumps(log.details)


async def test_unlink_forgets_the_account_and_badges_in_every_community(two_tenants, staff, player):
    _, tenant_b = two_tenants
    await _connect(staff)
    await _link(player)
    await PatreonMembership.create(
        tenant_id=tenant_b.id, user=player, patron_status=ACTIVE_PATRON, tier_ids=[],
        synced_at=datetime.now(timezone.utc),
    )
    assert await PatreonLinkService().unlink(player) == 2
    await player.refresh_from_db()
    assert player.patreon_user_id is None and player.patreon_username is None
    assert await PatreonMembership.filter(user_id=player.id).count() == 0
    assert await _actions(AuditActions.PATREON_UNLINKED) == 1
    assert await AuditLog.filter(action=AuditActions.PATREON_BADGE_REVOKED, tenant_id=tenant_b.id).count() == 1
