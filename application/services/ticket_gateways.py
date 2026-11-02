"""The store plug-in shape behind ticket sales.

Each provider a community can connect (Stripe now, PayPal next) is a
:class:`TicketGateway`: connect an account, open a hosted checkout, refund,
and read a signed webhook into a :class:`GatewayEvent`. The ticket services
only ever talk to this shape, so adding a store is one new class here plus an
entry in :func:`gateway_for`.

Gateways hold a decrypted secret for the length of one call and never log it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Protocol, Sequence

from application.utils.clients.stripe_client import (
    CheckoutLine,
    StripeError,
    parse_event,
    stripe_client,
    verify_signature,
)
from models import TicketProvider


class GatewayError(ValueError):
    """The store refused or couldn't be reached. A ``ValueError``, so pages show it."""


class GatewaySignatureError(Exception):
    """A webhook the store didn't sign. Answered 400 and otherwise ignored."""


@dataclass(frozen=True)
class Connected:
    account_label: Optional[str]
    webhook_id: Optional[str]
    webhook_secret: Optional[str]
    test_mode: bool


@dataclass(frozen=True)
class Checkout:
    provider_ref: str
    url: str


@dataclass(frozen=True)
class GatewayEvent:
    """A store webhook, normalised: ``paid``, ``expired`` or ``refunded``."""

    id: str
    kind: str
    order_ref: Optional[str]
    payment_ref: Optional[str]
    amount_refunded: int = 0


class TicketGateway(Protocol):
    provider: TicketProvider

    async def connect(self, webhook_url: str) -> Connected: ...

    async def disconnect(self, webhook_id: Optional[str]) -> None: ...

    async def create_checkout(
        self, *, order_id: int, lines: Sequence[CheckoutLine], currency: str,
        success_url: str, cancel_url: str, expires_at: int, email: Optional[str],
    ) -> Checkout: ...

    async def refund(self, payment_ref: str, amount: int, *, idempotency_key: str) -> None: ...

    def read_webhook(
        self, body: bytes, headers: Mapping[str, str], webhook_secret: str,
    ) -> Optional[GatewayEvent]: ...


class StripeGateway:
    provider = TicketProvider.STRIPE

    def __init__(self, secret: str) -> None:
        self._client = stripe_client(secret)

    async def connect(self, webhook_url: str) -> Connected:
        try:
            label = await self._client.account_label()
            endpoint = await self._client.create_webhook(webhook_url)
        except StripeError as exc:
            raise GatewayError(str(exc)) from exc
        return Connected(label, endpoint.id, endpoint.secret, self._client.test_mode)

    async def disconnect(self, webhook_id: Optional[str]) -> None:
        if not webhook_id:
            return
        try:
            await self._client.delete_webhook(webhook_id)
        except StripeError as exc:
            raise GatewayError(str(exc)) from exc

    async def create_checkout(
        self, *, order_id: int, lines: Sequence[CheckoutLine], currency: str,
        success_url: str, cancel_url: str, expires_at: int, email: Optional[str],
    ) -> Checkout:
        try:
            session = await self._client.create_checkout(
                order_id=order_id, lines=lines, currency=currency, success_url=success_url,
                cancel_url=cancel_url, expires_at=expires_at, email=email,
            )
        except StripeError as exc:
            raise GatewayError(str(exc)) from exc
        return Checkout(session.id, session.url)

    async def refund(self, payment_ref: str, amount: int, *, idempotency_key: str) -> None:
        try:
            await self._client.refund(payment_ref, amount, idempotency_key=idempotency_key)
        except StripeError as exc:
            raise GatewayError(str(exc)) from exc

    def read_webhook(
        self, body: bytes, headers: Mapping[str, str], webhook_secret: str,
    ) -> Optional[GatewayEvent]:
        try:
            verify_signature(body, headers.get('stripe-signature'), webhook_secret)
            event = parse_event(body)
        except StripeError as exc:
            raise GatewaySignatureError(str(exc)) from exc
        if event is None:
            return None
        return GatewayEvent(event.id, event.kind, event.order_ref, event.payment_ref, event.amount_refunded)


def validate_secret(provider: TicketProvider, secret: str) -> str:
    """The pasted secret, trimmed, or a ``ValueError`` saying what's wrong with it."""
    secret = (secret or '').strip()
    if provider is TicketProvider.STRIPE:
        if secret.startswith(('sk_live_', 'sk_test_')):
            raise ValueError(
                "That's a secret key. Create a restricted key in Stripe (Developers → API keys) "
                'with write access to Checkout Sessions, Refunds and Webhook Endpoints, and paste that.',
            )
        if not secret.startswith(('rk_live_', 'rk_test_')):
            raise ValueError('Paste a Stripe restricted key. It starts with rk_live_ or rk_test_.')
        return secret
    raise ValueError(f'{provider.label} isn\'t available yet.')


def gateway_for(provider: TicketProvider, secret: str) -> TicketGateway:
    if provider is TicketProvider.STRIPE:
        return StripeGateway(secret)
    raise GatewayError(f'{provider.label} isn\'t available yet.')
