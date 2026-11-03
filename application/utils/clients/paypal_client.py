"""PayPal transport for event ticket sales, on a community's own business account.

Talks to the PayPal REST API with ``httpx`` rather than the SDK. The community
creates a REST app in the PayPal developer dashboard and pastes its client id
and secret on Admin → Tickets; every call gets a short-lived OAuth token from
them, so every call acts on their account.

Unlike Stripe, an approved PayPal order holds no money until it is
**captured**: Wizzrobe captures when the ``CHECKOUT.ORDER.APPROVED`` webhook
arrives, and only then is the order paid. A buyer who approves after their hold
lapsed is never captured, so they're never charged.

Calls used:

* ``POST /v1/oauth2/token`` — client-credentials token for the other calls.
* ``POST /v1/notifications/webhooks`` / ``DELETE …/{id}`` — the endpoint PayPal
  posts ``CHECKOUT.ORDER.APPROVED``, ``PAYMENT.CAPTURE.COMPLETED`` and
  ``PAYMENT.CAPTURE.REFUNDED`` to.
* ``POST /v1/notifications/verify-webhook-signature`` — PayPal checks its own
  signature; webhooks carry no shared secret.
* ``POST /v2/checkout/orders`` and ``…/{id}/capture`` — the hosted approval
  page, then taking the money.
* ``POST /v2/payments/captures/{id}/refund`` — a full or partial refund.

``MOCK_PAYPAL`` swaps in :class:`MockPayPalClient`; like the other mock flags
it refuses to run in production.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional, Sequence

import httpx

from application.utils.clients.stripe_client import CheckoutLine
from application.utils.environment import env_flag, is_production

LIVE_BASE = 'https://api-m.paypal.com'
SANDBOX_BASE = 'https://api-m.sandbox.paypal.com'
TIMEOUT = httpx.Timeout(20.0)
WEBHOOK_EVENTS = ('CHECKOUT.ORDER.APPROVED', 'PAYMENT.CAPTURE.COMPLETED', 'PAYMENT.CAPTURE.REFUNDED')
ZERO_DECIMAL = frozenset({'HUF', 'JPY', 'TWD'})
SIGNATURE_HEADERS = {
    'auth_algo': 'paypal-auth-algo',
    'cert_url': 'paypal-cert-url',
    'transmission_id': 'paypal-transmission-id',
    'transmission_sig': 'paypal-transmission-sig',
    'transmission_time': 'paypal-transmission-time',
}


class PayPalError(Exception):
    """PayPal refused a call or could not be reached; the message is safe to show staff."""


class PayPalSignatureError(PayPalError):
    """A webhook PayPal wouldn't vouch for."""


@dataclass(frozen=True)
class PayPalOrder:
    id: str
    url: str


@dataclass(frozen=True)
class PayPalEvent:
    """The part of a PayPal webhook ticket sales acts on.

    ``kind`` is ``approved`` (capture it now), ``paid`` or ``refunded``;
    ``order_ref`` is the PayPal order id and ``payment_ref`` the capture id.
    ``amount_refunded`` is the capture's running refund total, in cents.
    """

    id: str
    kind: str
    order_ref: Optional[str]
    payment_ref: Optional[str]
    amount_refunded: int = 0


def to_value(cents: int, currency: str) -> str:
    """Cents → PayPal's decimal string (``6000`` → ``"60.00"``; no decimals for JPY)."""
    if currency.upper() in ZERO_DECIMAL:
        return str(cents)
    return f'{Decimal(cents) / 100:.2f}'


def to_cents(value: Optional[str], currency: str) -> int:
    if not value:
        return 0
    amount = Decimal(value)
    return int(amount if currency.upper() in ZERO_DECIMAL else amount * 100)


def _capture_id_from_links(resource: Mapping[str, Any]) -> Optional[str]:
    for link in resource.get('links') or []:
        if link.get('rel') == 'up' and '/captures/' in (link.get('href') or ''):
            return link['href'].rstrip('/').rsplit('/', 1)[-1]
    return None


def parse_event(payload: bytes) -> Optional[PayPalEvent]:
    """The ticket-relevant part of a verified webhook body, or ``None`` for one we ignore."""
    try:
        body = json.loads(payload)
        kind = body['event_type']
        resource = body['resource']
        event_id = body['id']
    except (ValueError, KeyError, TypeError) as exc:
        raise PayPalError('Unreadable PayPal event.') from exc
    if kind == 'CHECKOUT.ORDER.APPROVED':
        return PayPalEvent(event_id, 'approved', resource.get('id'), None)
    if kind == 'PAYMENT.CAPTURE.COMPLETED':
        order_id = ((resource.get('supplementary_data') or {}).get('related_ids') or {}).get('order_id')
        return PayPalEvent(event_id, 'paid', order_id, resource.get('id'))
    if kind == 'PAYMENT.CAPTURE.REFUNDED':
        breakdown = resource.get('seller_payable_breakdown') or {}
        total = breakdown.get('total_refunded_amount') or resource.get('amount') or {}
        return PayPalEvent(
            event_id, 'refunded', None, _capture_id_from_links(resource),
            to_cents(total.get('value'), total.get('currency_code') or 'USD'),
        )
    return None


class PayPalClient:
    """One community's PayPal REST app, by its client id and secret."""

    def __init__(self, client_id: str, client_secret: str, *, sandbox: bool) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self.sandbox = sandbox
        self._token: Optional[str] = None

    @property
    def _base(self) -> str:
        return SANDBOX_BASE if self.sandbox else LIVE_BASE

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token:
            return self._token
        response = await client.post(
            '/v1/oauth2/token', data={'grant_type': 'client_credentials'},
            auth=(self._client_id, self._client_secret),
        )
        if response.status_code == 401:
            raise PayPalError(
                'PayPal turned down that client id and secret. Check they are from the '
                f'{"sandbox" if self.sandbox else "live"} side of your REST app.',
            )
        if response.status_code >= 400:
            raise PayPalError(f'PayPal answered {response.status_code} when signing in.')
        self._token = str(response.json()['access_token'])
        return self._token

    async def _call(self, method: str, path: str, body: Optional[Mapping[str, Any]] = None,
                    *, request_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(base_url=self._base, timeout=TIMEOUT) as client:
                headers = {'Authorization': f'Bearer {await self._access_token(client)}',
                           'Content-Type': 'application/json', 'Prefer': 'return=representation'}
                if request_id:
                    headers['PayPal-Request-Id'] = request_id
                response = await client.request(method, path, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PayPalError("Couldn't reach PayPal. Try again in a minute.") from exc
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            message = None
            if isinstance(payload, dict):
                details = payload.get('details') or []
                message = (details[0].get('description') if details else None) or payload.get('message')
            raise PayPalError(f'PayPal said: {message}' if message else f'PayPal answered {response.status_code}.')
        return payload if isinstance(payload, dict) else {}

    async def create_webhook(self, url: str) -> str:
        body = await self._call('POST', '/v1/notifications/webhooks', {
            'url': url, 'event_types': [{'name': name} for name in WEBHOOK_EVENTS],
        })
        return str(body['id'])

    async def delete_webhook(self, webhook_id: str) -> None:
        await self._call('DELETE', f'/v1/notifications/webhooks/{webhook_id}')

    async def verify_webhook(self, payload: bytes, headers: Mapping[str, str], webhook_id: str) -> None:
        missing = [name for name in SIGNATURE_HEADERS.values() if not headers.get(name)]
        if missing:
            raise PayPalSignatureError('Missing PayPal signature headers.')
        try:
            event = json.loads(payload)
        except ValueError as exc:
            raise PayPalSignatureError('Unreadable PayPal event.') from exc
        body: Dict[str, Any] = {key: headers[name] for key, name in SIGNATURE_HEADERS.items()}
        body.update(webhook_id=webhook_id, webhook_event=event)
        result = await self._call('POST', '/v1/notifications/verify-webhook-signature', body)
        if result.get('verification_status') != 'SUCCESS':
            raise PayPalSignatureError('PayPal did not vouch for this webhook.')

    async def create_order(
        self, *, order_id: int, lines: Sequence[CheckoutLine], currency: str,
        return_url: str, cancel_url: str,
    ) -> PayPalOrder:
        code = currency.upper()
        total = sum(line.unit_amount * line.quantity for line in lines)
        body = await self._call('POST', '/v2/checkout/orders', {
            'intent': 'CAPTURE',
            'purchase_units': [{
                'reference_id': f'wizzrobe-{order_id}',
                'custom_id': str(order_id),
                'amount': {'currency_code': code, 'value': to_value(total, code),
                           'breakdown': {'item_total': {'currency_code': code, 'value': to_value(total, code)}}},
                'items': [{'name': line.name[:127], 'quantity': str(line.quantity), 'category': 'PHYSICAL_GOODS',
                           'unit_amount': {'currency_code': code, 'value': to_value(line.unit_amount, code)}}
                          for line in lines],
            }],
            'payment_source': {'paypal': {'experience_context': {
                'return_url': return_url, 'cancel_url': cancel_url,
                'user_action': 'PAY_NOW', 'shipping_preference': 'NO_SHIPPING',
            }}},
        }, request_id=f'wizzrobe-order-{order_id}')
        url = next((link['href'] for link in body.get('links') or []
                    if link.get('rel') in ('payer-action', 'approve')), None)
        if not url:
            raise PayPalError("PayPal didn't return an approval link.")
        return PayPalOrder(str(body['id']), url)

    async def capture(self, order_ref: str) -> str:
        """Take the money for an approved order; the capture id refunds go against."""
        body = await self._call('POST', f'/v2/checkout/orders/{order_ref}/capture', {},
                                request_id=f'wizzrobe-capture-{order_ref}')
        for unit in body.get('purchase_units') or []:
            for capture in (unit.get('payments') or {}).get('captures') or []:
                if capture.get('status') in ('COMPLETED', 'PENDING'):
                    return str(capture['id'])
        raise PayPalError("PayPal didn't complete the payment.")

    async def refund(self, capture_id: str, amount: int, currency: str, *, request_id: str) -> str:
        body = await self._call('POST', f'/v2/payments/captures/{capture_id}/refund', {
            'amount': {'currency_code': currency.upper(), 'value': to_value(amount, currency)},
        }, request_id=request_id)
        return str(body.get('id', ''))


class MockPayPalClient(PayPalClient):
    """Local stand-in: approval returns to the success page, capture and refunds just succeed."""

    def __init__(self, client_id: str = 'mock', client_secret: str = 'mock', *, sandbox: bool = True) -> None:
        if is_production():
            raise RuntimeError('MOCK_PAYPAL is not allowed in production.')
        super().__init__(client_id, client_secret, sandbox=sandbox)

    async def create_webhook(self, url: str) -> str:
        return f'WH-MOCK-{secrets.token_hex(4)}'

    async def delete_webhook(self, webhook_id: str) -> None:
        return None

    async def verify_webhook(self, payload: bytes, headers: Mapping[str, str], webhook_id: str) -> None:
        if headers.get('paypal-transmission-sig') != f'mock-{webhook_id}':
            raise PayPalSignatureError('PayPal did not vouch for this webhook.')

    async def create_order(self, *, order_id: int, return_url: str, **_: Any) -> PayPalOrder:
        return PayPalOrder(f'MOCK-ORDER-{order_id}', return_url)

    async def capture(self, order_ref: str) -> str:
        return f'MOCK-CAPTURE-{order_ref}'

    async def refund(self, capture_id: str, amount: int, currency: str, *, request_id: str) -> str:
        return f'MOCK-REFUND-{secrets.token_hex(4)}'


def paypal_client(client_id: str, client_secret: str, *, sandbox: bool) -> PayPalClient:
    if env_flag('MOCK_PAYPAL'):
        return MockPayPalClient(client_id, client_secret, sandbox=sandbox)
    return PayPalClient(client_id, client_secret, sandbox=sandbox)
