"""
Ticket Store Service - Business Logic Layer

The store accounts a community sells badges through. Staff paste their own
Stripe restricted key (or, later, PayPal credentials); Wizzrobe registers its
webhook on that account and seals both secrets per community. Nothing here ever
hands a secret back out: pages and the API see the provider, the account name,
test mode and who connected it.

Gated by :attr:`~models.FeatureFlag.TICKET_SALES`.
"""

import secrets
from dataclasses import dataclass
from typing import List, Optional

from application.feature_flags import requires_feature
from application.repositories import TicketStoreRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.ticket_gateways import (
    GatewayError,
    TicketGateway,
    gateway_for,
    validate_secret,
)
from application.tenant_context import require_tenant_id
from application.utils.environment import get_base_url
from application.utils.secret_box import open_, seal
from models import FeatureFlag, TicketProvider, TicketStore, User

STORE_SEAL_PURPOSE = 'ticket_store'
HOOK_SEAL_PURPOSE = 'ticket_store_webhook'


@dataclass(frozen=True)
class StoreView:
    """A connected store as staff see it: never the secret."""

    provider: TicketProvider
    account_label: Optional[str]
    test_mode: bool
    connected_by: Optional[str]


def webhook_url(store: TicketStore) -> str:
    return f'{get_base_url()}/api/ticket-webhooks/{store.provider.value}/{store.webhook_token}'


def _context(store: TicketStore) -> str:
    return str(store.tenant_id)


def store_gateway(store: TicketStore) -> TicketGateway:
    """A gateway on ``store``'s account, or a ``ValueError`` when its key no longer opens."""
    secret = open_(store.secret, purpose=STORE_SEAL_PURPOSE, context=_context(store))
    if secret is None:
        raise ValueError(f'The {store.provider.label} key needs pasting again on Admin → Tickets.')
    return gateway_for(store.provider, secret)


def store_webhook_secret(store: TicketStore) -> Optional[str]:
    if not store.webhook_secret:
        return None
    return open_(store.webhook_secret, purpose=HOOK_SEAL_PURPOSE, context=_context(store))


class TicketStoreService:
    """Connect, list and disconnect a community's store accounts."""

    def __init__(self) -> None:
        self.repository = TicketStoreRepository()
        self.audit_service = AuditService()

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def list_stores(self, actor: User) -> List[StoreView]:
        await AuthService.ensure(await AuthService.can_manage_tickets(actor), 'Only staff can manage ticket stores.')
        rows = await self.repository.list_all()
        out = []
        for row in rows:
            await row.fetch_related('connected_by')
            out.append(StoreView(
                row.provider, row.account_label, row.test_mode,
                row.connected_by.preferred_name if row.connected_by else None,
            ))
        return out

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def connected_providers(self) -> List[TicketProvider]:
        """The stores a buyer can pay with, in a fixed order (no secrets, no names)."""
        return [row.provider for row in await self.repository.list_all()]

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def connect(self, actor: User, provider: TicketProvider, secret: str) -> StoreView:
        """Connect ``provider`` with a pasted key, replacing any earlier connection to it."""
        await AuthService.ensure(await AuthService.can_manage_tickets(actor), 'Only staff can manage ticket stores.')
        secret = validate_secret(provider, secret)
        existing = await self.repository.get_by_provider(provider)
        token = existing.webhook_token if existing else secrets.token_urlsafe(24)
        probe = TicketStore(tenant_id=require_tenant_id(), provider=provider, webhook_token=token)
        gateway = gateway_for(provider, secret)
        connected = await gateway.connect(webhook_url(probe))
        if existing is not None:
            await self._drop_webhook(existing)
        context = str(require_tenant_id())
        fields = dict(
            account_label=(connected.account_label or '')[:255] or None,
            secret=seal(secret, purpose=STORE_SEAL_PURPOSE, context=context),
            webhook_secret=seal(connected.webhook_secret, purpose=HOOK_SEAL_PURPOSE, context=context)
            if connected.webhook_secret else None,
            webhook_id=connected.webhook_id,
            test_mode=connected.test_mode,
            connected_by=actor,
        )
        if existing is None:
            store = await self.repository.create(provider=provider, webhook_token=token, **fields)
        else:
            store = await self.repository.update(existing, **fields)
        await self.audit_service.write_log(actor, AuditActions.TICKET_STORE_CONNECTED, {
            'provider': provider.value, 'test_mode': connected.test_mode, 'replaced': existing is not None,
        })
        return StoreView(provider, store.account_label, store.test_mode, actor.preferred_name)

    @requires_feature(FeatureFlag.TICKET_SALES)
    async def disconnect(self, actor: User, provider: TicketProvider) -> None:
        """Forget ``provider``'s key and remove Wizzrobe's webhook from that account.

        Orders already paid stay as they are; refunds for them then have to be
        made in the store itself.
        """
        await AuthService.ensure(await AuthService.can_manage_tickets(actor), 'Only staff can manage ticket stores.')
        store = await self.repository.get_by_provider(provider)
        if store is None:
            raise ValueError(f'{provider.label} isn\'t connected.')
        await self._drop_webhook(store)
        await self.repository.delete(store)
        await self.audit_service.write_log(actor, AuditActions.TICKET_STORE_DISCONNECTED, {'provider': provider.value})

    async def _drop_webhook(self, store: TicketStore) -> None:
        """Best effort: a revoked key can't remove its webhook, and that mustn't block staff."""
        try:
            await store_gateway(store).disconnect(store.webhook_id)
        except (GatewayError, ValueError):
            pass
