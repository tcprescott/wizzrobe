"""Matcherino transport client for event check-in.

Matcherino publishes no API. The endpoints here are the ones its own web app
calls, used with Matcherino's go-ahead but **without** any support promise from
them — they may change or disappear without notice. Everything in this module is
written for that: each parse is strict about the fields check-in cannot work
without and raises :class:`MatcherinoAPIError` rather than guessing, and the sync
that consumes it treats any failure as "keep the roster we have" (see
``CheckInService``).

Check-in reads a ticketed **venue** (Matcherino's word for an event that sells
badges), not a bounty. Wire shape, captured from the live API:

* ``POST /__api/auth/token`` with ``{"appName": "WEB", "refreshToken"}``
  answers ``{"body": {"accessToken", "refreshToken", "expiresIn"}}``. The access
  token is a JWT sent as ``x-mno-auth: Bearer <token>``; it lasts a day. The
  refresh token does not rotate, so one stored value keeps working.
* ``GET /__api/venues/admin/purchaseData?venueId=`` (authenticated, venue
  admins only) answers every badge purchase in one list, no paging. Each carries
  the purchase ``id``, ``passId`` plus the embedded ``pass``, the buyer's
  ``userId`` and ``user`` (with ``authProvider``/``authId``), the door ``code``,
  ``purchasedAt`` and ``refundedAt``. It also carries the buyer's contact and
  address fields and a payment ledger, which are dropped at parse time.
* ``POST /__api/venues/pass/listPrivate`` with ``{"venueId"}`` (authenticated)
  answers the venue's badge types, with ``qtySold``.
* ``GET /__api/bounties/findById?id=`` answers a venue too (``kind: "venue"``);
  it is how staff confirm a venue id before saving.
* Failures come back as ``{"status": 4xx/5xx, "error": {"message": ...}}``.

Each buyer carries the account they signed in to Matcherino with
(``authProvider`` + ``authId``). For ``discord`` the id is the Discord snowflake
and for ``twitch`` it is the Twitch user id, which is what makes most matches
exact.

The login is per community: a refresh token for a Matcherino account that is
an admin of the venues being synced, which staff paste on Admin → Check-in
(``MatcherinoLogin``) and the check-in service passes in when it builds a
client. ``MOCK_MATCHERINO`` swaps in :class:`MockMatcherinoClient`, which serves canned
sales so local dev and the browser loop never call Matcherino. Like the other
mock flags it refuses to run under ``ENVIRONMENT=production``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote

import httpx

from application.utils.environment import env_flag, is_production

MATCHERINO_API = 'https://api.matcherino.com/__api'
TOKEN_URL = f'{MATCHERINO_API}/auth/token'
PURCHASES_URL = f'{MATCHERINO_API}/venues/admin/purchaseData'
TIERS_URL = f'{MATCHERINO_API}/venues/pass/listPrivate'
VENUE_URL = f'{MATCHERINO_API}/bounties/findById'

LOGIN_SETTING = 'the saved Matcherino login (Admin → Check-in)'
MAX_REFRESH_TOKEN_LENGTH = 512
REQUEST_TIMEOUT_SECONDS = 20
TOKEN_EXPIRY_MARGIN_SECONDS = 300
USER_AGENT = 'Wizzrobe event check-in'

# An allowlist: buyer name, email, phone and address must never reach our database.
_PURCHASE_KEEP = (
    'id', 'passId', 'userId', 'venueId', 'code', 'purchasedAt', 'refundedAt',
    'completed', 'sourceType', 'entries', 'bountyAttachments',
)
_ACCOUNT_KEEP = ('id', 'displayName', 'userName', 'authProvider', 'authId', 'avatar', 'createdAt')


class MatcherinoAPIError(Exception):
    """Matcherino errored, or served a payload this module does not recognise."""


class MatcherinoAuthError(MatcherinoAPIError):
    """The stored Matcherino login is missing, or Matcherino refused it."""


def is_mock_matcherino() -> bool:
    """Return True when MOCK_MATCHERINO is enabled (and not in production)."""
    enabled = env_flag('MOCK_MATCHERINO')
    if enabled and is_production():
        raise RuntimeError(
            'MOCK_MATCHERINO must not be enabled in production: it fakes the '
            'Matcherino ticket sales. Unset MOCK_MATCHERINO or change ENVIRONMENT.'
        )
    return enabled


@dataclass(frozen=True)
class MatcherinoAccount:
    """A buyer's Matcherino account, with the trimmed raw object kept beside it."""

    user_id: str
    display_name: str
    auth_provider: Optional[str] = None
    auth_id: Optional[str] = None
    avatar_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def handle(self) -> str:
        """The ``name#id`` form ``User.matcherino_username`` stores."""
        return f'{self.display_name}#{self.user_id}'


@dataclass(frozen=True)
class MatcherinoTier:
    """A badge type the venue sells (a Matcherino *pass*)."""

    id: int
    title: str
    amount_cents: int = 0
    role: Optional[str] = None
    qty_sold: int = 0


@dataclass(frozen=True)
class MatcherinoPurchase:
    id: int
    tier: MatcherinoTier
    code: str
    buyer: MatcherinoAccount
    purchased_at: Optional[datetime] = None
    refunded_at: Optional[datetime] = None
    raw: Dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def active(self) -> bool:
        return self.refunded_at is None


@dataclass(frozen=True)
class VenueSales:
    tiers: List[MatcherinoTier]
    purchases: List[MatcherinoPurchase]


@dataclass(frozen=True)
class MatcherinoVenue:
    id: int
    title: str


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_time(value: Any) -> Optional[datetime]:
    text = _opt_str(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _require_int(raw: Dict[str, Any], key: str, what: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().isdigit():
        raise MatcherinoAPIError(
            f'Matcherino {what} is missing {key}; the API shape may have changed.'
        )
    return int(value)


def parse_tier(raw: Any) -> MatcherinoTier:
    if not isinstance(raw, dict):
        raise MatcherinoAPIError(f'Matcherino badge type was not an object: {type(raw).__name__}')
    title = _opt_str(raw.get('title'))
    if title is None:
        raise MatcherinoAPIError('Matcherino badge type has no title; the API shape may have changed.')
    amount = raw.get('amount')
    sold = raw.get('qtySold')
    return MatcherinoTier(
        id=_require_int(raw, 'id', 'badge type'),
        title=title,
        amount_cents=amount if isinstance(amount, int) and not isinstance(amount, bool) else 0,
        role=_opt_str(raw.get('role')),
        qty_sold=sold if isinstance(sold, int) and not isinstance(sold, bool) else 0,
    )


def parse_account(raw: Any, user_id: Optional[int] = None) -> MatcherinoAccount:
    if not isinstance(raw, dict):
        raise MatcherinoAPIError('Matcherino purchase has no buyer; the API shape may have changed.')
    account_id = _opt_str(raw.get('id')) or (str(user_id) if user_id is not None else None)
    display_name = _opt_str(raw.get('displayName'))
    if account_id is None or display_name is None:
        raise MatcherinoAPIError(
            'Matcherino buyer is missing id or displayName; the API shape may have changed.'
        )
    return MatcherinoAccount(
        user_id=account_id,
        display_name=display_name,
        auth_provider=(_opt_str(raw.get('authProvider')) or '').lower() or None,
        auth_id=_opt_str(raw.get('authId')),
        avatar_url=_opt_str(raw.get('avatar')),
        raw={key: raw.get(key) for key in _ACCOUNT_KEEP if key in raw},
    )


def parse_purchase(raw: Any, tiers: Dict[int, MatcherinoTier]) -> MatcherinoPurchase:
    """Parse one purchase, or raise if the shape has changed.

    The badge type comes from ``tiers`` (the venue's list) when it is there and
    from the embedded ``pass`` otherwise, so a badge type the venue has since
    deleted still resolves.
    """
    if not isinstance(raw, dict):
        raise MatcherinoAPIError(f'Matcherino purchase was not an object: {type(raw).__name__}')
    purchase_id = _require_int(raw, 'id', 'purchase')
    pass_id = _require_int(raw, 'passId', 'purchase')
    user_id = _require_int(raw, 'userId', 'purchase')
    code = _opt_str(raw.get('code'))
    if code is None:
        raise MatcherinoAPIError('Matcherino purchase has no code; the API shape may have changed.')
    tier = tiers.get(pass_id)
    if tier is None:
        tier = parse_tier(raw.get('pass'))
        if tier.id != pass_id:
            raise MatcherinoAPIError('Matcherino purchase names a different badge type than it embeds.')
    buyer = parse_account(raw.get('user'), user_id)
    if buyer.user_id != str(user_id):
        raise MatcherinoAPIError('Matcherino purchase names a different buyer than it embeds.')
    return MatcherinoPurchase(
        id=purchase_id,
        tier=tier,
        code=code,
        buyer=buyer,
        purchased_at=_parse_time(raw.get('purchasedAt')),
        refunded_at=_parse_time(raw.get('refundedAt')),
        raw={key: raw.get(key) for key in _PURCHASE_KEEP if key in raw},
    )


def distinct_purchases(purchases: Iterable[MatcherinoPurchase]) -> List[MatcherinoPurchase]:
    """One entry per purchase id, the last listed winning.

    A purchase listed twice would otherwise make the sync insert its badge twice
    and fail on the unique purchase id every tick until Matcherino stopped.
    """
    return list({purchase.id: purchase for purchase in purchases}.values())


def _unwrap(payload: Any, what: str) -> Any:
    """Return the envelope's ``body``, or raise with Matcherino's own message."""
    if not isinstance(payload, dict):
        raise MatcherinoAPIError(f'Matcherino {what} response was not an object.')
    error = payload.get('error')
    if error:
        message = error.get('message') if isinstance(error, dict) else str(error)
        raise MatcherinoAPIError(f'Matcherino rejected the {what} request: {message or "unknown error"}')
    if 'body' not in payload or payload['body'] is None:
        raise MatcherinoAPIError(f'Matcherino {what} response had no body.')
    return payload['body']


# Keyed by a hash of the refresh token, so communities never share a token and a replaced login misses.
_tokens: Dict[str, Tuple[str, float]] = {}
_token_locks: Dict[str, asyncio.Lock] = {}


def reset_token_cache() -> None:
    _tokens.clear()


def _cache_key(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode()).hexdigest()


def normalize_refresh_token(text: Optional[str]) -> str:
    """The refresh token from what staff pasted, or ``''`` when there is none.

    Takes the bare token, or any JSON object carrying ``refreshToken``: the
    ``/auth/token`` request payload, or the value of the ``credentials`` cookie
    the web app keeps it in (which may arrive URL-encoded).
    """
    value = (text or '').strip()
    if value.lower().startswith('%7b'):
        value = unquote(value)
    if value.startswith('{'):
        try:
            payload = json.loads(value)
        except ValueError:
            return value
        if isinstance(payload, dict):
            return _opt_str(payload.get('refreshToken')) or ''
    return value.strip('"\' ')


def account_id_from_access_token(token: str) -> Optional[str]:
    """The Matcherino user id in an access token's ``sub``, unverified.

    Only for showing staff whose login is saved; nothing is authorized on it.
    """
    parts = token.split('.')
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
    except (ValueError, binascii.Error):
        return None
    sub = payload.get('sub') if isinstance(payload, dict) else None
    return _opt_str(sub)


def _is_auth_failure(resp: httpx.Response, payload: Any) -> bool:
    status = payload.get('status') if isinstance(payload, dict) else None
    return resp.status_code in (401, 403) or status in (401, 403)


class MatcherinoClient:
    """Async client for the Matcherino endpoints check-in uses.

    ``refresh_token`` is the community's saved login. Without one, the public
    venue lookup still works and anything that needs a venue admin raises
    :class:`MatcherinoAuthError` before calling Matcherino.
    """

    def __init__(self, refresh_token: Optional[str] = None) -> None:
        self._refresh = (refresh_token or '').strip() or None

    def _refresh_token(self) -> str:
        if self._refresh is None:
            raise MatcherinoAuthError(
                "Matcherino ticket sync isn't set up: no Matcherino login is saved. "
                'Add one on Admin → Check-in.'
            )
        return self._refresh

    def _session(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'},
        )

    async def _send(
        self, client: httpx.AsyncClient, method: str, url: str, *,
        params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> tuple[httpx.Response, Any]:
        headers: Dict[str, str] = {}
        if token is not None:
            headers['x-mno-auth'] = f'Bearer {token}'
        content = None
        if body is not None:
            # Keep text/plain: it's what Matcherino's own web app sends.
            headers['Content-Type'] = 'text/plain;charset=UTF-8'
            content = json.dumps(body)
        try:
            resp = await client.request(method, url, params=params, content=content, headers=headers)
        except httpx.HTTPError as e:
            raise MatcherinoAPIError(f"Couldn't reach Matcherino: {e.__class__.__name__}") from e
        try:
            payload = resp.json()
        except ValueError as e:
            raise MatcherinoAPIError(
                f'Matcherino returned non-JSON ({resp.status_code}): {resp.text[:200]}'
            ) from e
        return resp, payload

    async def _access_token(self, client: httpx.AsyncClient, *, force: bool = False) -> str:
        refresh = self._refresh_token()
        key = _cache_key(refresh)
        async with _token_locks.setdefault(key, asyncio.Lock()):
            cached = _tokens.get(key)
            if not force and cached is not None and time.time() < cached[1]:
                return cached[0]
            resp, payload = await self._send(
                client, 'POST', TOKEN_URL, body={'appName': 'WEB', 'refreshToken': refresh},
            )
            if _is_auth_failure(resp, payload) or resp.status_code >= 400:
                _tokens.pop(key, None)
                raise MatcherinoAuthError(
                    f'Matcherino refused {LOGIN_SETTING}. Sign in to Matcherino as a '
                    'venue admin and paste a fresh refresh token there.'
                )
            body = _unwrap(payload, 'sign-in')
            token = _opt_str(body.get('accessToken')) if isinstance(body, dict) else None
            if token is None:
                raise MatcherinoAPIError('Matcherino sign-in returned no access token.')
            expires_in = body.get('expiresIn')
            lifetime = expires_in if isinstance(expires_in, int) and expires_in > 0 else 3600
            _tokens[key] = (token, time.time() + max(lifetime - TOKEN_EXPIRY_MARGIN_SECONDS, 60))
            return token

    async def verify_login(self) -> Optional[str]:
        """Sign in with the refresh token now; return the account id it belongs to.

        Always mints rather than trusting the cache, so a token is checked
        against Matcherino at the moment staff save it.
        """
        async with self._session() as client:
            token = await self._access_token(client, force=True)
        return account_id_from_access_token(token)

    async def _call(
        self, client: httpx.AsyncClient, method: str, url: str, what: str, *,
        params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None,
        auth: bool = False,
    ) -> Any:
        token = await self._access_token(client) if auth else None
        resp, payload = await self._send(client, method, url, params=params, body=body, token=token)
        if auth and _is_auth_failure(resp, payload):
            token = await self._access_token(client, force=True)
            resp, payload = await self._send(client, method, url, params=params, body=body, token=token)
            if _is_auth_failure(resp, payload):
                raise MatcherinoAuthError(
                    f'Matcherino refused the {what} request. The account behind '
                    f'{LOGIN_SETTING} must be an admin of this venue.'
                )
        result = _unwrap(payload, what)
        if resp.status_code >= 400:
            raise MatcherinoAPIError(f'Matcherino {what} request failed ({resp.status_code}).')
        return result

    async def fetch_venue(self, venue_id: int) -> MatcherinoVenue:
        """The venue's title, refusing a plain bounty (the usual mix-up)."""
        async with self._session() as client:
            body = await self._call(client, 'GET', VENUE_URL, 'venue', params={'id': venue_id})
        if not isinstance(body, dict):
            raise MatcherinoAPIError('Matcherino venue response was not an object.')
        title = _opt_str(body.get('title'))
        if body.get('id') is None or title is None:
            raise MatcherinoAPIError(f'Matcherino has no venue {venue_id}.')
        if body.get('kind') != 'venue':
            raise MatcherinoAPIError(
                f'{venue_id} is a Matcherino bounty ("{title}"), not a ticketed venue. Use the '
                "number from the event's badge page instead."
            )
        return MatcherinoVenue(id=int(body['id']), title=title)

    async def fetch_tiers(self, venue_id: int) -> List[MatcherinoTier]:
        async with self._session() as client:
            return await self._fetch_tiers(client, venue_id)

    async def _fetch_tiers(self, client: httpx.AsyncClient, venue_id: int) -> List[MatcherinoTier]:
        body = await self._call(
            client, 'POST', TIERS_URL, 'badge types', body={'venueId': venue_id}, auth=True,
        )
        if not isinstance(body, list):
            raise MatcherinoAPIError('Matcherino badge types were not a list.')
        return [parse_tier(raw) for raw in body]

    async def fetch_sales(self, venue_id: int) -> VenueSales:
        """Every badge type and every purchase on the venue.

        A short read is an error, not fewer buyers: the sync withdraws anyone
        missing from this list. Matcherino counts each badge type's sales, so a
        purchase list shorter than those counts add up to is treated as cut off.
        """
        async with self._session() as client:
            tiers = await self._fetch_tiers(client, venue_id)
            body = await self._call(
                client, 'GET', PURCHASES_URL, 'ticket sales', params={'venueId': venue_id}, auth=True,
            )
        if not isinstance(body, list):
            raise MatcherinoAPIError('Matcherino ticket sales were not a list.')
        by_id = {tier.id: tier for tier in tiers}
        purchases = distinct_purchases(parse_purchase(raw, by_id) for raw in body)
        expected = sum(tier.qty_sold for tier in tiers)
        if len(purchases) < expected:
            raise MatcherinoAPIError(
                f'Matcherino counts {expected} badges sold but listed {len(purchases)}; '
                'treating the read as incomplete.'
            )
        return VenueSales(tiers=tiers, purchases=purchases)


class MockMatcherinoClient(MatcherinoClient):
    """Canned client for ``MOCK_MATCHERINO`` and tests.

    Serves the same badge types and purchases for any venue id, shaped like the
    live feed, so the full sync and matching path runs offline. Pass ``tiers``
    and ``purchases`` to script different sales (tests do).
    """

    def __init__(
        self,
        purchases: Optional[List[Dict[str, Any]]] = None,
        tiers: Optional[List[Dict[str, Any]]] = None,
        title: str = 'Mock Matcherino Venue',
    ) -> None:
        super().__init__()
        self._purchases = purchases if purchases is not None else MOCK_PURCHASES
        self._tiers = tiers if tiers is not None else MOCK_TIERS
        self._title = title

    async def fetch_venue(self, venue_id: int) -> MatcherinoVenue:
        return MatcherinoVenue(id=venue_id, title=self._title)

    async def verify_login(self) -> Optional[str]:
        return MOCK_LOGIN_ACCOUNT_ID

    async def fetch_tiers(self, venue_id: int) -> List[MatcherinoTier]:
        return [parse_tier(copy.deepcopy(raw)) for raw in self._tiers]

    async def fetch_sales(self, venue_id: int) -> VenueSales:
        tiers = await self.fetch_tiers(venue_id)
        by_id = {tier.id: tier for tier in tiers}
        return VenueSales(
            tiers=tiers,
            purchases=distinct_purchases(parse_purchase(copy.deepcopy(raw), by_id) for raw in self._purchases),
        )


def get_matcherino_client(refresh_token: Optional[str] = None) -> MatcherinoClient:
    """Return the live client signed in with ``refresh_token``, or the mock per ``MOCK_MATCHERINO``."""
    if is_mock_matcherino():
        return MockMatcherinoClient()
    return MatcherinoClient(refresh_token)


def mock_tier(pass_id: int, title: str, amount_cents: int, *, role: str = 'player',
              qty_sold: int = 0) -> Dict[str, Any]:
    """A badge type in the live feed's exact key set."""
    return {
        'id': pass_id, 'amount': amount_cents, 'description': '', 'meta': {}, 'role': role,
        'thumbnailImg': '', 'title': title, 'venueId': 0, 'discountsAvailable': 0,
        'applicableTaxes': None, 'precedence': 'primary', 'availableStart': '2026-01-01T00:00:00Z',
        'availableEnd': None, 'validStart': '2026-01-01T00:00:00Z', 'validEnd': None,
        'qtySold': qty_sold, 'createdAt': '2026-01-01T00:00:00Z', 'isDeleted': False,
        'availableChildren': None, 'availableParents': None,
    }


def mock_purchase(
    user_id: int,
    display_name: str,
    *,
    tier: Optional[Dict[str, Any]] = None,
    purchase_id: Optional[int] = None,
    code: Optional[int] = None,
    auth_provider: str = 'gplus',
    auth_id: str = '',
    purchased_at: str = '2026-09-01T12:00:00Z',
    refunded_at: Optional[str] = None,
) -> Dict[str, Any]:
    """A purchase in the live feed's exact key set, contact fields empty as served."""
    tier = tier if tier is not None else MOCK_TIERS[0]
    purchase_id = purchase_id if purchase_id is not None else 50_000 + user_id % 10_000
    return {
        'id': purchase_id, 'passId': tier['id'], 'userId': user_id, 'venueId': 0,
        'code': code if code is not None else 10_000_000 + purchase_id, 'meta': {},
        'firstName': '', 'lastName': '', 'phone': '', 'email': '', 'address1': '', 'address2': '',
        'city': '', 'state': '', 'zip': '', 'country': '',
        'purchasedAt': purchased_at, 'refundedAt': refunded_at, 'completed': False,
        'sourceId': 0, 'sourceType': '', 'revshareTotal': 0, 'pass': copy.deepcopy(tier),
        'entries': None, 'transactions': [], 'upsells': None, 'bountyAttachments': [],
        'venue': None,
        'user': {
            'id': user_id, 'displayName': display_name, 'userName': display_name.lower().replace(' ', ''),
            'authProvider': auth_provider, 'authId': auth_id or str(10_000_000 + user_id),
            'avatar': '', 'createdAt': '2025-01-01T00:00:00Z', 'email': None, 'status': 'active',
        },
    }


# Each buyer lines up with a seeded fixture; tests/test_seed_coverage.py checks every link method appears.
MOCK_DISCORD_RACER_ID = '100000000073811948'
MOCK_TWITCH_RACER_ID = '555000222'
MOCK_REMEMBERED_ID = 900005
MOCK_LOGIN_ACCOUNT_ID = '900000'

MOCK_TIERS: List[Dict[str, Any]] = [
    mock_tier(1988, 'Base Tier Badge', 8000, qty_sold=6),
    mock_tier(1989, 'VIP Tier Badge', 11000, qty_sold=3),
    mock_tier(1990, 'Super VIP Tier Badge', 16000, qty_sold=1),
    mock_tier(1991, 'Day Pass', 4000, role='spectator', qty_sold=2),
]
_BASE, _VIP, _SUPER, _DAY = MOCK_TIERS

MOCK_PURCHASES: List[Dict[str, Any]] = [
    mock_purchase(100234, 'jemgold', tier=_BASE, auth_provider='discord',
                  auth_id='100000000000000001', purchased_at='2026-08-02T18:00:00Z'),
    mock_purchase(204871, 'blueshell', tier=_VIP, auth_provider='twitch', auth_id='555000111',
                  purchased_at='2026-08-14T09:30:00Z'),
    mock_purchase(309552, 'ridgeline', tier=_BASE),
    mock_purchase(900001, 'DiscordRacer', tier=_SUPER, auth_provider='discord',
                  auth_id=MOCK_DISCORD_RACER_ID),
    mock_purchase(900002, 'MockTwitch', tier=_BASE, auth_provider='twitch',
                  auth_id=MOCK_TWITCH_RACER_ID),
    mock_purchase(900002, 'MockTwitch', tier=_DAY, purchase_id=59_102, auth_provider='twitch',
                  auth_id=MOCK_TWITCH_RACER_ID),
    mock_purchase(900003, 'Player Three', tier=_BASE),
    mock_purchase(900004, 'Mock_Facebook', tier=_BASE, auth_provider='facebook'),
    mock_purchase(MOCK_REMEMBERED_ID, 'Racer Five', tier=_VIP),
    mock_purchase(900006, 'Refund Requester', tier=_VIP, auth_provider='discord',
                  auth_id='100000000000000006', refunded_at='2026-09-20T10:00:00Z'),
    mock_purchase(900007, 'Mock Google', tier=_DAY),
    mock_purchase(900008, 'Base Buyer', tier=_BASE),
]
