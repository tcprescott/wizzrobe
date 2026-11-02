"""Stripe transport for event ticket sales, on a community's own account.

Talks to the Stripe REST API with ``httpx`` (form-encoded requests, JSON
answers) rather than the SDK: ticket sales uses five calls and a webhook
signature check. The key is the community's **restricted key**, pasted on
Admin → Tickets, so every call acts on their account; Wizzrobe holds no Stripe
account of its own.

The restricted key needs write access to Checkout Sessions, Refunds and Webhook
Endpoints. Reading the account name is optional: without it the store shows as
just "Stripe".

Calls used:

* ``GET /v1/account`` — the business name shown on Admin → Tickets (optional).
* ``POST /v1/webhook_endpoints`` / ``DELETE /v1/webhook_endpoints/{id}`` — the
  endpoint Stripe posts ``checkout.session.completed``,
  ``checkout.session.expired`` and ``charge.refunded`` to. Creating it returns
  the signing secret, once.
* ``POST /v1/checkout/sessions`` — the hosted checkout a buyer is sent to.
* ``POST /v1/refunds`` — a full or partial refund against the payment intent.

``MOCK_STRIPE`` swaps in :class:`MockStripeClient` so local dev and tests never
call Stripe; like the other mock flags it refuses to run in production.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode

import httpx

from application.utils.environment import env_flag, is_production

API_BASE = 'https://api.stripe.com'
TIMEOUT = httpx.Timeout(20.0)
SIGNATURE_TOLERANCE_SECONDS = 300
WEBHOOK_EVENTS = ('checkout.session.completed', 'checkout.session.expired', 'charge.refunded')


class StripeError(Exception):
    """Stripe refused a call or could not be reached; the message is safe to show staff."""


class StripeSignatureError(StripeError):
    """A webhook whose signature doesn't check out: not from Stripe, or replayed."""


@dataclass(frozen=True)
class CheckoutLine:
    name: str
    unit_amount: int
    quantity: int


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    url: str


@dataclass(frozen=True)
class WebhookEndpoint:
    id: str
    secret: str


@dataclass(frozen=True)
class StripeEvent:
    """The part of a Stripe event ticket sales acts on.

    ``kind`` is ``paid``, ``expired`` or ``refunded``; ``order_ref`` is the
    checkout session id (paid/expired) and ``payment_ref`` the payment intent.
    ``amount_refunded`` is the charge's running refund total.
    """

    id: str
    kind: str
    order_ref: Optional[str]
    payment_ref: Optional[str]
    amount_refunded: int = 0


def _flatten(data: Mapping[str, Any], prefix: str = '') -> List[Tuple[str, str]]:
    """Stripe's bracketed form encoding: ``{'a': {'b': 1}}`` → ``a[b]=1``."""
    out: List[Tuple[str, str]] = []
    for key, value in data.items():
        name = f'{prefix}[{key}]' if prefix else str(key)
        if isinstance(value, Mapping):
            out += _flatten(value, name)
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                if isinstance(item, Mapping):
                    out += _flatten(item, f'{name}[{i}]')
                else:
                    out.append((f'{name}[{i}]', str(item)))
        elif isinstance(value, bool):
            out.append((name, 'true' if value else 'false'))
        elif value is not None:
            out.append((name, str(value)))
    return out


def verify_signature(payload: bytes, header: Optional[str], secret: str, *, now: Optional[float] = None) -> None:
    """Raise :class:`StripeSignatureError` unless ``header`` signs ``payload`` recently.

    Stripe's scheme: ``Stripe-Signature: t=<unix>,v1=<hex>``, where the hex is
    HMAC-SHA256 of ``"<t>.<payload>"`` under the endpoint secret. A timestamp
    more than five minutes off is refused, so a captured request can't be
    replayed later.
    """
    if not header:
        raise StripeSignatureError('Missing Stripe-Signature header.')
    parts: Dict[str, List[str]] = {}
    for item in header.split(','):
        key, _, value = item.strip().partition('=')
        parts.setdefault(key, []).append(value)
    try:
        stamp = int(parts.get('t', [''])[0])
    except ValueError as exc:
        raise StripeSignatureError('Bad Stripe-Signature timestamp.') from exc
    if abs((now if now is not None else time.time()) - stamp) > SIGNATURE_TOLERANCE_SECONDS:
        raise StripeSignatureError('Stripe-Signature timestamp is too old.')
    expected = hmac.new(secret.encode(), f'{stamp}.'.encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in parts.get('v1', [])):
        raise StripeSignatureError('Stripe-Signature does not match.')


def sign(payload: bytes, secret: str, stamp: Optional[int] = None) -> str:
    """A ``Stripe-Signature`` header for ``payload``: for tests and the mock store."""
    stamp = int(time.time()) if stamp is None else stamp
    digest = hmac.new(secret.encode(), f'{stamp}.'.encode() + payload, hashlib.sha256).hexdigest()
    return f't={stamp},v1={digest}'


def parse_event(payload: bytes) -> Optional[StripeEvent]:
    """The ticket-relevant part of a verified event body, or ``None`` for one we ignore."""
    try:
        body = json.loads(payload)
        kind = body['type']
        obj = body['data']['object']
        event_id = body['id']
    except (ValueError, KeyError, TypeError) as exc:
        raise StripeError('Unreadable Stripe event.') from exc
    if kind == 'checkout.session.completed':
        if obj.get('payment_status') != 'paid':
            return None
        return StripeEvent(event_id, 'paid', obj.get('id'), obj.get('payment_intent'))
    if kind == 'checkout.session.expired':
        return StripeEvent(event_id, 'expired', obj.get('id'), None)
    if kind == 'charge.refunded':
        return StripeEvent(
            event_id, 'refunded', None, obj.get('payment_intent'), int(obj.get('amount_refunded') or 0),
        )
    return None


class StripeClient:
    """One community's Stripe account, by its restricted key."""

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    @property
    def test_mode(self) -> bool:
        return '_test_' in self._key

    async def _call(self, method: str, path: str, data: Optional[Mapping[str, Any]] = None,
                    *, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        headers = {'Authorization': f'Bearer {self._key}', 'Stripe-Version': '2024-06-20',
                   'Content-Type': 'application/x-www-form-urlencoded'}
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        try:
            async with httpx.AsyncClient(base_url=API_BASE, timeout=TIMEOUT) as client:
                response = await client.request(
                    method, path, headers=headers, content=urlencode(_flatten(data)) if data else None,
                )
        except httpx.HTTPError as exc:
            raise StripeError("Couldn't reach Stripe. Try again in a minute.") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            message = (body.get('error') or {}).get('message') if isinstance(body, dict) else None
            raise StripeError(f'Stripe said: {message}' if message else f'Stripe answered {response.status_code}.')
        return body

    async def account_label(self) -> Optional[str]:
        """The business name on the account, or ``None`` when the key can't read it."""
        try:
            body = await self._call('GET', '/v1/account')
        except StripeError:
            return None
        profile = body.get('business_profile') or {}
        settings = (body.get('settings') or {}).get('dashboard') or {}
        return profile.get('name') or settings.get('display_name') or None

    async def create_webhook(self, url: str) -> WebhookEndpoint:
        body = await self._call('POST', '/v1/webhook_endpoints', {
            'url': url, 'enabled_events': list(WEBHOOK_EVENTS),
            'description': 'Wizzrobe ticket sales',
        })
        return WebhookEndpoint(body['id'], body['secret'])

    async def delete_webhook(self, endpoint_id: str) -> None:
        await self._call('DELETE', f'/v1/webhook_endpoints/{endpoint_id}')

    async def create_checkout(
        self, *, order_id: int, lines: Sequence[CheckoutLine], currency: str,
        success_url: str, cancel_url: str, expires_at: int, email: Optional[str] = None,
    ) -> CheckoutSession:
        data: Dict[str, Any] = {
            'mode': 'payment',
            'success_url': success_url,
            'cancel_url': cancel_url,
            'client_reference_id': str(order_id),
            'metadata': {'wizzrobe_order': str(order_id)},
            'expires_at': expires_at,
            'line_items': [
                {'quantity': line.quantity, 'price_data': {
                    'currency': currency, 'unit_amount': line.unit_amount,
                    'product_data': {'name': line.name},
                }}
                for line in lines
            ],
        }
        if email:
            data['customer_email'] = email
        body = await self._call(
            'POST', '/v1/checkout/sessions', data, idempotency_key=f'wizzrobe-order-{order_id}',
        )
        return CheckoutSession(body['id'], body['url'])

    async def refund(self, payment_ref: str, amount: int, *, idempotency_key: str) -> str:
        body = await self._call(
            'POST', '/v1/refunds', {'payment_intent': payment_ref, 'amount': amount},
            idempotency_key=idempotency_key,
        )
        return str(body.get('id', ''))


class MockStripeClient(StripeClient):
    """Local stand-in: checkout returns to the success page, refunds just succeed."""

    def __init__(self, api_key: str = 'rk_test_mock') -> None:
        if is_production():
            raise RuntimeError('MOCK_STRIPE is not allowed in production.')
        super().__init__(api_key)

    async def account_label(self) -> Optional[str]:
        return 'Mock Stripe account'

    async def create_webhook(self, url: str) -> WebhookEndpoint:
        return WebhookEndpoint(f'we_mock_{secrets.token_hex(4)}', f'whsec_mock_{secrets.token_hex(8)}')

    async def delete_webhook(self, endpoint_id: str) -> None:
        return None

    async def create_checkout(self, *, order_id: int, success_url: str, **_: Any) -> CheckoutSession:
        return CheckoutSession(f'cs_mock_{order_id}', success_url)

    async def refund(self, payment_ref: str, amount: int, *, idempotency_key: str) -> str:
        return f're_mock_{secrets.token_hex(4)}'


def stripe_client(api_key: str) -> StripeClient:
    return MockStripeClient(api_key) if env_flag('MOCK_STRIPE') else StripeClient(api_key)
