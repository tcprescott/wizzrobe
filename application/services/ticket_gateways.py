"""The store plug-in shape behind ticket sales.

Each provider a community can connect (Stripe, PayPal) is a
:class:`TicketGateway`: connect an account, open a hosted checkout, capture,
refund, and read a signed webhook into a :class:`GatewayEvent`. The ticket
services only ever talk to this shape, so adding a store is one new class here
plus an entry in :func:`gateway_for`.

Gateways hold a decrypted secret for the length of one call and never log it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Optional, Protocol, Sequence

from application.utils.clients import paypal_client as paypal
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
    """A store webhook, normalised: ``paid``, ``expired``, ``refunded``, or ``approved``.

    ``approved`` (PayPal) means the buyer agreed to pay but no money has moved
    yet: the order has to be captured before it counts as paid.
    """

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

    async def capture(self, order_ref: str) -> str: ...

    async def refund(self, payment_ref: str, amount: int, *, currency: str, idempotency_key: str) -> None: ...

    async def read_webhook(
        self, body: bytes, headers: Mapping[str, str], *, webhook_secret: Optional[str], webhook_id: Optional[str],
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

    async def capture(self, order_ref: str) -> str:
        raise GatewayError('Stripe takes the payment at checkout; there is nothing to capture.')

    async def refund(self, payment_ref: str, amount: int, *, currency: str, idempotency_key: str) -> None:
        try:
            await self._client.refund(payment_ref, amount, idempotency_key=idempotency_key)
        except StripeError as exc:
            raise GatewayError(str(exc)) from exc

    async def read_webhook(
        self, body: bytes, headers: Mapping[str, str], *, webhook_secret: Optional[str], webhook_id: Optional[str],
    ) -> Optional[GatewayEvent]:
        if not webhook_secret:
            raise GatewaySignatureError('No webhook secret on file for this store.')
        try:
            verify_signature(body, headers.get('stripe-signature'), webhook_secret)
            event = parse_event(body)
        except StripeError as exc:
            raise GatewaySignatureError(str(exc)) from exc
        if event is None:
            return None
        return GatewayEvent(event.id, event.kind, event.order_ref, event.payment_ref, event.amount_refunded)


class PayPalGateway:
    """A PayPal REST app. Its sealed secret is JSON: client id, client secret, sandbox."""

    provider = TicketProvider.PAYPAL

    def __init__(self, secret: str) -> None:
        creds = json.loads(secret)
        self.sandbox = bool(creds.get('sandbox', True))
        self._client = paypal.paypal_client(creds['client_id'], creds['client_secret'], sandbox=self.sandbox)

    async def connect(self, webhook_url: str) -> Connected:
        try:
            webhook_id = await self._client.create_webhook(webhook_url)
        except paypal.PayPalError as exc:
            raise GatewayError(str(exc)) from exc
        return Connected(None, webhook_id, None, self.sandbox)

    async def disconnect(self, webhook_id: Optional[str]) -> None:
        if not webhook_id:
            return
        try:
            await self._client.delete_webhook(webhook_id)
        except paypal.PayPalError as exc:
            raise GatewayError(str(exc)) from exc

    async def create_checkout(
        self, *, order_id: int, lines: Sequence[CheckoutLine], currency: str,
        success_url: str, cancel_url: str, expires_at: int, email: Optional[str],
    ) -> Checkout:
        try:
            order = await self._client.create_order(
                order_id=order_id, lines=lines, currency=currency, return_url=success_url, cancel_url=cancel_url,
            )
        except paypal.PayPalError as exc:
            raise GatewayError(str(exc)) from exc
        return Checkout(order.id, order.url)

    async def capture(self, order_ref: str) -> str:
        try:
            return await self._client.capture(order_ref)
        except paypal.PayPalError as exc:
            raise GatewayError(str(exc)) from exc

    async def refund(self, payment_ref: str, amount: int, *, currency: str, idempotency_key: str) -> None:
        try:
            await self._client.refund(payment_ref, amount, currency, request_id=idempotency_key)
        except paypal.PayPalError as exc:
            raise GatewayError(str(exc)) from exc

    async def read_webhook(
        self, body: bytes, headers: Mapping[str, str], *, webhook_secret: Optional[str], webhook_id: Optional[str],
    ) -> Optional[GatewayEvent]:
        if not webhook_id:
            raise GatewaySignatureError('No webhook on file for this store.')
        try:
            await self._client.verify_webhook(body, headers, webhook_id)
            event = paypal.parse_event(body)
        except paypal.PayPalSignatureError as exc:
            raise GatewaySignatureError(str(exc)) from exc
        except paypal.PayPalError as exc:
            raise GatewayError(str(exc)) from exc
        if event is None:
            return None
        return GatewayEvent(event.id, event.kind, event.order_ref, event.payment_ref, event.amount_refunded)


def validate_secret(
    provider: TicketProvider, secret: str, *, client_id: Optional[str] = None, sandbox: bool = True,
) -> str:
    """What to seal for ``provider``, or a ``ValueError`` saying what's wrong with the paste.

    Stripe: the restricted key, trimmed. PayPal: client id + secret (+ sandbox)
    as one JSON string.
    """
    secret = (secret or '').strip()
    if provider is TicketProvider.PAYPAL:
        client_id = (client_id or '').strip()
        if not client_id:
            raise ValueError("Paste your PayPal REST app's client ID as well as its secret.")
        if not secret:
            raise ValueError("Paste your PayPal REST app's secret.")
        return json.dumps({'client_id': client_id, 'client_secret': secret, 'sandbox': bool(sandbox)})
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
    if provider is TicketProvider.PAYPAL:
        return PayPalGateway(secret)
    raise GatewayError(f'{provider.label} isn\'t available yet.')
