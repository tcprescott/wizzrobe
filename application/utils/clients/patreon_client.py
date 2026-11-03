"""Patreon API v2: OAuth, the creator's campaign and members, webhooks.

* :class:`PatreonOAuthClient` swaps an authorization code (or a refresh token)
  for tokens, for both sign-ins: the creator connecting a community's campaign
  and a member linking their account.
* :class:`PatreonClient` reads with one access token: the signed-in identity
  and its memberships, the creator's campaign and tiers, a page of campaign
  members, and creates or deletes our webhook.

Every member read asks for ``patron_status`` and the entitled tier ids only
(JSON:API sparse fieldsets), so pledge amounts never reach Wizzrobe.

Patreon answers HTTP 429 when we go too fast; each call waits out
``Retry-After`` (capped) and tries again a few times before giving up.

Webhook deliveries are signed with HMAC-MD5 of the raw body under the
webhook's secret (``X-Patreon-Signature``, hex): :func:`signature_ok`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = 'https://www.patreon.com/oauth2/authorize'
TOKEN_URL = 'https://www.patreon.com/api/oauth2/token'
API_URL = 'https://www.patreon.com/api/oauth2/v2'

CREATOR_SCOPE = 'identity campaigns campaigns.members w:campaigns.webhook'
MEMBER_SCOPE = 'identity identity.memberships'

WEBHOOK_TRIGGERS = ('members:create', 'members:update', 'members:delete')
ACTIVE_PATRON = 'active_patron'

PAGE_SIZE = 1000
_TIMEOUT = httpx.Timeout(20.0)
_MAX_TRIES = 4
_MAX_WAIT_SECONDS = 60.0
_USER_AGENT = 'Wizzrobe Patreon integration'

ClientFactory = Callable[[], httpx.AsyncClient]
Sleeper = Callable[[float], Awaitable[None]]


class PatreonError(Exception):
    """Patreon refused or couldn't be reached. ``status`` is the HTTP status when there was one."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class PatreonAuthError(PatreonError):
    """The token was refused (revoked, expired, or the refresh token already spent)."""


@dataclass(frozen=True)
class Campaign:
    id: str
    name: Optional[str]


@dataclass(frozen=True)
class Tier:
    id: str
    title: str


@dataclass(frozen=True)
class MemberStatus:
    """One campaign membership, reduced to what Wizzrobe keeps."""

    member_id: Optional[str]
    patreon_user_id: Optional[str]
    campaign_id: Optional[str]
    patron_status: Optional[str]
    tier_ids: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_patron(self) -> bool:
        return self.patron_status == ACTIVE_PATRON


@dataclass(frozen=True)
class Identity:
    patreon_user_id: str
    name: Optional[str]
    memberships: Tuple[MemberStatus, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: Optional[str]
    expires_in: Optional[int]


def build_authorize_url(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    return f'{AUTHORIZE_URL}?' + urlencode({
        'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri,
        'scope': scope, 'state': state,
    })


def signature_ok(secret: Optional[str], body: bytes, signature: Optional[str]) -> bool:
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.md5).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def _rel_id(resource: Mapping[str, Any], name: str) -> Optional[str]:
    data = ((resource.get('relationships') or {}).get(name) or {}).get('data')
    if isinstance(data, dict) and data.get('id') is not None:
        return str(data['id'])
    return None


def _rel_ids(resource: Mapping[str, Any], name: str) -> Tuple[str, ...]:
    data = ((resource.get('relationships') or {}).get(name) or {}).get('data')
    if not isinstance(data, list):
        return ()
    return tuple(str(d['id']) for d in data if isinstance(d, dict) and d.get('id') is not None)


def parse_member(resource: Mapping[str, Any]) -> MemberStatus:
    """A JSON:API ``member`` resource (from the members list, identity or a webhook) as a :class:`MemberStatus`."""
    attributes = resource.get('attributes') or {}
    status = attributes.get('patron_status')
    return MemberStatus(
        member_id=str(resource['id']) if resource.get('id') is not None else None,
        patreon_user_id=_rel_id(resource, 'user'),
        campaign_id=_rel_id(resource, 'campaign'),
        patron_status=str(status) if status else None,
        tier_ids=_rel_ids(resource, 'currently_entitled_tiers'),
    )


def parse_webhook(body: bytes) -> MemberStatus:
    try:
        payload = json.loads(body.decode() or '{}')
    except (UnicodeDecodeError, ValueError) as e:
        raise PatreonError('Patreon sent a webhook that is not JSON.') from e
    data = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or data.get('type') != 'member':
        raise PatreonError('Patreon sent a webhook without a member.')
    return parse_member(data)


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT, headers={'User-Agent': _USER_AGENT})


async def _send(
    client_factory: ClientFactory, sleep: Sleeper, method: str, url: str, **kwargs: Any,
) -> httpx.Response:
    """One request, waiting out 429s. Raises :class:`PatreonError` on anything but 2xx."""
    for attempt in range(_MAX_TRIES):
        try:
            async with client_factory() as client:
                resp = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise PatreonError("Patreon didn't answer in time; try again shortly.") from e
        except httpx.HTTPError as e:
            raise PatreonError(f"Couldn't reach Patreon ({type(e).__name__}).") from e
        if resp.status_code == 429 and attempt < _MAX_TRIES - 1:
            await sleep(_retry_after(resp, attempt))
            continue
        if resp.status_code in (401, 403):
            raise PatreonAuthError(f'Patreon refused the sign-in ({resp.status_code}).', resp.status_code)
        if resp.status_code >= 400:
            raise PatreonError(f'Patreon answered {resp.status_code}.', resp.status_code)
        return resp
    raise PatreonError('Patreon is rate limiting us; try again shortly.', 429)


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    raw = resp.headers.get('Retry-After')
    try:
        wait = float(raw) if raw is not None else 2.0 ** attempt
    except ValueError:
        wait = 2.0 ** attempt
    return max(0.0, min(wait, _MAX_WAIT_SECONDS))


class PatreonOAuthClient:
    """Code and refresh-token exchanges against Patreon's token endpoint."""

    def __init__(
        self, client_id: str, client_secret: str,
        *, client_factory: ClientFactory = _default_client, sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._client_factory = client_factory
        self._sleep = sleep

    async def exchange_code(self, code: str, redirect_uri: str) -> Tokens:
        return await self._token({
            'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri,
        })

    async def refresh(self, refresh_token: str) -> Tokens:
        return await self._token({'grant_type': 'refresh_token', 'refresh_token': refresh_token})

    async def _token(self, form: Dict[str, str]) -> Tokens:
        form = {**form, 'client_id': self.client_id, 'client_secret': self.client_secret}
        try:
            resp = await _send(self._client_factory, self._sleep, 'POST', TOKEN_URL, data=form)
        except PatreonError as e:
            if e.status in (400, 401, 403):
                raise PatreonAuthError('Patreon refused the sign-in.', e.status) from e
            raise
        payload = resp.json()
        access = payload.get('access_token')
        if not access:
            raise PatreonAuthError('Patreon sent no access token.')
        expires_in = payload.get('expires_in')
        return Tokens(
            access_token=str(access),
            refresh_token=str(payload['refresh_token']) if payload.get('refresh_token') else None,
            expires_in=int(expires_in) if expires_in else None,
        )


class PatreonClient:
    """Reads and webhook writes with one access token (the creator's, or a member's once)."""

    def __init__(
        self, access_token: str,
        *, client_factory: ClientFactory = _default_client, sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._headers = {'Authorization': f'Bearer {access_token}'}
        self._client_factory = client_factory
        self._sleep = sleep

    async def _get(self, path: str, params: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
        resp = await _send(
            self._client_factory, self._sleep, 'GET', f'{API_URL}{path}', params=list(params), headers=self._headers,
        )
        return resp.json()

    async def identity(self, *, with_memberships: bool = False) -> Identity:
        params = [('fields[user]', 'full_name')]
        if with_memberships:
            params += [
                ('include', 'memberships,memberships.campaign,memberships.currently_entitled_tiers'),
                ('fields[member]', 'patron_status'),
            ]
        payload = await self._get('/identity', params)
        data = payload.get('data') or {}
        if not data.get('id'):
            raise PatreonError('Patreon sent no identity.')
        memberships = tuple(
            parse_member(r) for r in payload.get('included') or [] if r.get('type') == 'member'
        )
        name = (data.get('attributes') or {}).get('full_name')
        return Identity(patreon_user_id=str(data['id']), name=str(name) if name else None, memberships=memberships)

    async def campaigns(self) -> List[Campaign]:
        payload = await self._get('/campaigns', [('fields[campaign]', 'creation_name,vanity')])
        out = []
        for row in payload.get('data') or []:
            attributes = row.get('attributes') or {}
            name = attributes.get('creation_name') or attributes.get('vanity')
            out.append(Campaign(id=str(row['id']), name=str(name) if name else None))
        return out

    async def tiers(self, campaign_id: str) -> List[Tier]:
        payload = await self._get(f'/campaigns/{campaign_id}', [('include', 'tiers'), ('fields[tier]', 'title')])
        return [
            Tier(id=str(r['id']), title=str((r.get('attributes') or {}).get('title') or 'Untitled tier'))
            for r in payload.get('included') or [] if r.get('type') == 'tier'
        ]

    async def members_page(
        self, campaign_id: str, cursor: Optional[str] = None,
    ) -> Tuple[List[MemberStatus], Optional[str]]:
        """One page of the campaign's members and the cursor for the next (``None`` at the end)."""
        params = [
            ('include', 'user,currently_entitled_tiers'),
            ('fields[member]', 'patron_status'),
            ('page[count]', str(PAGE_SIZE)),
        ]
        if cursor:
            params.append(('page[cursor]', cursor))
        payload = await self._get(f'/campaigns/{campaign_id}/members', params)
        members = [parse_member(r) for r in payload.get('data') or []]
        members = [
            m if m.campaign_id else MemberStatus(m.member_id, m.patreon_user_id, campaign_id, m.patron_status, m.tier_ids)
            for m in members
        ]
        cursors = ((payload.get('meta') or {}).get('pagination') or {}).get('cursors') or {}
        nxt = cursors.get('next')
        return members, str(nxt) if nxt else None

    async def create_webhook(self, campaign_id: str, uri: str) -> Tuple[str, str]:
        """Register our webhook on the campaign. Returns ``(webhook_id, secret)``."""
        body = {'data': {
            'type': 'webhook',
            'attributes': {'triggers': list(WEBHOOK_TRIGGERS), 'uri': uri},
            'relationships': {'campaign': {'data': {'type': 'campaign', 'id': campaign_id}}},
        }}
        resp = await _send(
            self._client_factory, self._sleep, 'POST', f'{API_URL}/webhooks', json=body,
            headers={**self._headers, 'Content-Type': 'application/vnd.api+json'},
        )
        data = resp.json().get('data') or {}
        secret = (data.get('attributes') or {}).get('secret')
        if not data.get('id') or not secret:
            raise PatreonError('Patreon made the webhook but sent back no secret.')
        return str(data['id']), str(secret)

    async def delete_webhook(self, webhook_id: str) -> None:
        await _send(
            self._client_factory, self._sleep, 'DELETE', f'{API_URL}/webhooks/{webhook_id}', headers=self._headers,
        )
