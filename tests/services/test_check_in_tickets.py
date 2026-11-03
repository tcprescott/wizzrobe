"""Badges bought in Wizzrobe on the check-in roster: rows, passes, claims, refunds, comps and the worker."""

# ruff: noqa: F811 — the shared fixtures are imported from check_in_support, and
# pytest injects them as same-named test arguments.

import json
from datetime import datetime, timedelta, timezone

import pytest

from application.repositories import CheckInEventRepository, TicketRepository
from application.services.check_in_rules import active_badges, lanyard_for
from application.services.check_in_sync_worker import _due
from application.services.feature_flag_service import reset_flag_cache
from application.services.ticket_checkout_service import TicketCheckoutService
from application.services.ticket_service import TicketService
from application.services.ticket_store_service import HOOK_SEAL_PURPOSE, TicketStoreService
from application.utils.clients import stripe_client as stripe_module
from application.utils.clients.matcherino_client import mock_purchase, mock_tier
from application.utils.clients.stripe_client import sign
from application.utils.secret_box import open_
from models import (
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEventStatus,
    CheckInLinkMethod,
    CheckInPass,
    FeatureFlag,
    Role,
    TenantFeatureFlag,
    TenantMembership,
    TicketOrder,
    TicketProvider,
    TicketStore,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user
from tests.services.check_in_support import (  # noqa: F401 — fixtures
    VENUE,
    client,
    desk,
    service,
    staff,
)


@pytest.fixture(autouse=True)
def mock_stripe(monkeypatch):
    monkeypatch.setenv('MOCK_STRIPE', '1')

    async def refund(self, payment_ref, amount, *, idempotency_key):
        return 're_test'

    monkeypatch.setattr(stripe_module.MockStripeClient, 'refund', refund)


async def _member(discord_id: int, name: str):
    user = await make_user(discord_id=discord_id, username=name)
    await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
async def store(staff):
    await TenantMembership.get_or_create(user=staff, tenant_id=DEFAULT_TEST_TENANT_ID)
    await TicketStoreService().connect(staff, TicketProvider.STRIPE, 'rk_test_abc123')
    return await TicketStore.get(provider=TicketProvider.STRIPE)


@pytest.fixture
async def weekend(staff, store):
    return await TicketService().create_type(staff, name='Weekend badge', price_cents=6000)


@pytest.fixture
async def vip(staff, store):
    return await TicketService().create_type(staff, name='VIP badge', price_cents=12000, lanyard='vip')


@pytest.fixture
async def roster(service, staff):
    return await service.create_event(staff, 'SGL 2026', status=CheckInEventStatus.OPEN)


async def _post(store: TicketStore, payload: dict):
    body = json.dumps(payload).encode()
    secret = open_(store.webhook_secret, purpose=HOOK_SEAL_PURPOSE, context=str(store.tenant_id))
    headers = {'Stripe-Signature': sign(body, secret)}
    return await TicketCheckoutService().handle_webhook(TicketProvider.STRIPE, store.webhook_token, body, headers)


async def _buy(buyer, quantities, store) -> TicketOrder:
    start = await TicketCheckoutService().start_checkout(buyer, TicketProvider.STRIPE, quantities)
    order = await TicketOrder.get(id=start.order_id)
    await _post(store, {'id': f'evt_{order.id}', 'type': 'checkout.session.completed', 'data': {'object': {
        'id': order.provider_ref, 'payment_status': 'paid', 'payment_intent': f'pi_{order.id}'}}})
    return await TicketOrder.get(id=order.id)


async def _refund_in_dashboard(store: TicketStore, order: TicketOrder, amount: int) -> None:
    await _post(store, {'id': f'evt_r{order.id}', 'type': 'charge.refunded', 'data': {'object': {
        'payment_intent': order.payment_ref, 'amount_refunded': amount}}})


async def _row(roster, user) -> CheckInEntrant:
    return await CheckInEntrant.get(event=roster, user=user).prefetch_related('passes__tier')


class TestRoster:
    async def test_a_buyer_lands_on_the_roster_linked_with_their_badge(self, service, staff, store, weekend, roster):
        buyer = await _member(801, 'buyer')
        await _buy(buyer, {weekend.id: 1}, store)

        result = await service.sync_event(staff, roster.id)

        assert (result.added, result.badges) == (1, 1)
        row = await _row(roster, buyer)
        assert (row.source, row.link_method, row.withdrawn_at) == (
            CheckInEntrantSource.WIZZROBE, CheckInLinkMethod.TICKET, None)
        badge = (await TicketService().my_badges(buyer)).own[0]
        held = active_badges(row.passes)
        assert [(b.code, b.tier.title) for b in held] == [(badge.code, 'Weekend badge')]

    async def test_a_second_sync_changes_nothing(self, service, staff, store, weekend, roster):
        await _buy(await _member(801, 'buyer'), {weekend.id: 1}, store)
        await service.sync_event(staff, roster.id)
        again = await service.sync_event(staff, roster.id)
        assert (again.added, again.withdrawn, again.rejoined) == (0, 0, 0)
        assert await CheckInPass.filter(event=roster).count() == 1

    async def test_the_vip_lanyard_follows_the_badge_type(self, service, staff, store, vip, roster):
        buyer = await _member(801, 'buyer')
        await _buy(buyer, {vip.id: 1}, store)
        await service.sync_event(staff, roster.id)
        row = await _row(roster, buyer)
        assert lanyard_for(row, active_badges(row.passes)) == 'vip'

    async def test_an_unclaimed_extra_has_no_row_until_claimed(self, service, staff, store, weekend, roster):
        buyer = await _member(801, 'buyer')
        await _buy(buyer, {weekend.id: 2}, store)
        await service.sync_event(staff, roster.id)
        assert await CheckInEntrant.filter(event=roster).count() == 1

        token = (await TicketService().my_badges(buyer)).extras[0].claim_path.rsplit('/', 1)[1]
        friend = await _member(802, 'friend')
        await TicketService().claim(friend, token)
        result = await service.sync_event(staff, roster.id)

        assert result.added == 1
        assert len(active_badges((await _row(roster, friend)).passes)) == 1
        assert len(active_badges((await _row(roster, buyer)).passes)) == 1

    async def test_a_refund_withdraws_the_row_and_marks_the_badge(self, service, staff, store, weekend, roster):
        buyer = await _member(801, 'buyer')
        order = await _buy(buyer, {weekend.id: 1}, store)
        await service.sync_event(staff, roster.id)

        await _refund_in_dashboard(store, order, 6000)
        result = await service.sync_event(staff, roster.id)

        row = await _row(roster, buyer)
        assert result.withdrawn == 1 and row.withdrawn_at is not None
        assert active_badges(row.passes) == []
        assert (await CheckInPass.get(event=roster)).refunded_at is not None

    async def test_buying_again_after_a_refund_rejoins(self, service, staff, store, weekend, roster):
        buyer = await _member(801, 'buyer')
        order = await _buy(buyer, {weekend.id: 1}, store)
        await service.sync_event(staff, roster.id)
        await _refund_in_dashboard(store, order, 6000)
        await service.sync_event(staff, roster.id)

        await _buy(buyer, {weekend.id: 1}, store)
        result = await service.sync_event(staff, roster.id)

        assert result.rejoined == 1
        assert (await _row(roster, buyer)).withdrawn_at is None

    async def test_with_ticket_sales_off_the_roster_ignores_badges(self, service, staff, store, weekend, roster):
        await _buy(await _member(801, 'buyer'), {weekend.id: 1}, store)
        await TenantFeatureFlag.filter(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.TICKET_SALES.value,
        ).update(enabled=False)
        reset_flag_cache()
        with pytest.raises(ValueError, match="nothing to sync"):
            await service.sync_event(staff, roster.id)


class TestAlongsideOtherSources:
    async def test_a_comped_buyer_keeps_one_row_and_falls_back_to_the_comp_on_refund(
        self, service, staff, store, weekend,
    ):
        roster = await service.create_event(
            staff, 'Comps', comp_roles=[Role.STAFF], status=CheckInEventStatus.OPEN,
        )
        await service.sync_event(staff, roster.id)
        assert (await _row(roster, staff)).source == CheckInEntrantSource.COMP

        order = await _buy(staff, {weekend.id: 1}, store)
        await service.sync_event(staff, roster.id)
        row = await _row(roster, staff)
        assert (row.source, row.comp_reasons) == (CheckInEntrantSource.WIZZROBE, ['staff'])
        assert await CheckInEntrant.filter(event=roster).count() == 1

        await _refund_in_dashboard(store, order, 6000)
        await service.sync_event(staff, roster.id)
        row = await _row(roster, staff)
        assert (row.source, row.withdrawn_at) == (CheckInEntrantSource.COMP, None)

    async def test_a_matcherino_buyer_who_also_buys_here_keeps_their_row(
        self, service, client, staff, store, weekend,
    ):
        roster = await service.create_event(staff, 'Both', venue_id=VENUE, status=CheckInEventStatus.OPEN)
        buyer = await _member(801, 'buyer')
        tier = mock_tier(1, 'Base', 8000)
        client.set([mock_purchase(100, 'Buyer', tier=tier, purchase_id=1)], tiers=[tier])
        await service.sync_event(staff, roster.id)
        await CheckInEntrant.filter(event=roster, matcherino_user_id='100').update(user=buyer)

        await _buy(buyer, {weekend.id: 1}, store)
        client.set([mock_purchase(100, 'Buyer', tier=tier, purchase_id=1, refunded_at='2026-03-02T00:00:00Z')], tiers=[tier])
        await service.sync_event(staff, roster.id)

        row = await _row(roster, buyer)
        assert (row.source, row.withdrawn_at) == (CheckInEntrantSource.MATCHERINO, None)
        assert [b.tier.title for b in active_badges(row.passes)] == ['Weekend badge']
        assert await CheckInEntrant.filter(event=roster).count() == 1


class TestWorker:
    async def test_an_event_with_badges_is_scanned_and_due_after_a_change(self, service, staff, store, weekend, roster):
        assert roster not in await CheckInEventRepository.list_syncable_all()
        await _buy(await _member(801, 'buyer'), {weekend.id: 1}, store)

        changes = await TicketRepository.badge_changes_by_event()
        assert [e.id for e in await CheckInEventRepository.list_syncable_all(changes.keys())] == [roster.id]

        await service.sync_event(staff, roster.id)
        roster = await CheckInEventRepository.get_by_id(roster.id)
        now = datetime.now(timezone.utc)
        assert not _due(roster, now, changes[roster.event_id])
        assert _due(roster, now, now + timedelta(seconds=1))


class TestDesk:
    async def test_a_badge_row_cannot_be_relinked_unlinked_or_removed(self, service, staff, store, weekend, roster):
        buyer = await _member(801, 'buyer')
        await _buy(buyer, {weekend.id: 1}, store)
        await service.sync_event(staff, roster.id)
        row = await _row(roster, buyer)
        other = await _member(802, 'other')

        for attempt in (
            service.link(staff, row.id, other.id),
            service.unlink(staff, row.id),
            service.remove_entrant(staff, row.id),
        ):
            with pytest.raises(ValueError, match='bought in Wizzrobe'):
                await attempt
        assert (await _row(roster, buyer)).user_id == buyer.id
