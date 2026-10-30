"""start.gg GraphQL transport client (read only) and OAuth identity client.

Two clients live here:

* :class:`StartggClient` reads an event (phases, pools, entrants, standings and
  sets) and reports a set's winner, with either a community's start.gg sign-in
  or the platform's personal access token (``STARTGG_API_TOKEN``).
* :class:`StartggOAuthClient` runs both OAuth flows: a player linking their
  start.gg account (scope ``user.identity``, token used once and discarded)
  and staff signing the community in (``tournament.reporter`` too, token kept
  and refreshed).

Both return **normalized** dicts so the service layer never sees the wire shape:

    event   -> {'event_id', 'name', 'slug', 'state', 'phases': [phase]}
    phase   -> {'phase_id', 'name', 'phase_order', 'bracket_type', 'state', 'pools': [pool]}
    pool    -> {'phase_group_id', 'identifier', 'bracket_type', 'complete'}
    entrant -> {'entrant_id', 'name', 'gamer_tag', 'startgg_user_id', 'discord_id'}
    detail  -> {'standings': [{'entrant_id', 'placement'}], 'sets': [set]}
    set     -> {'set_id', 'round', 'round_text', 'identifier', 'complete', 'winner_id',
                'completed_at', 'slots': [{'entrant_id', 'score', 'prereq_type',
                'prereq_id', 'prereq_placement'}]}

start.gg caps a request at 1,000 objects and 80 requests a minute, so the event
is read in pieces: one request for its shape, a page of entrants at a time, and
a page of sets per pool.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import aiohttp

from application.utils.clients.oauth_identity_client import (
    OAuthIdentityClient,
    ProviderConfig,
    opt_str,
)
from application.utils.clients.oauth_identity_client import (
    build_authorize_url as _build_authorize_url,
)

API_URL = 'https://api.start.gg/gql/alpha'
AUTHORIZE_URL = 'https://start.gg/oauth/authorize'
TOKEN_URL = 'https://api.start.gg/oauth/access_token'
REFRESH_URL = 'https://api.start.gg/oauth/refresh'

_ENTRANT_PAGE = 50
_SET_PAGE = 40
_STANDINGS_PAGE = 64
_TIMEOUT = aiohttp.ClientTimeout(total=20)
# start.gg's ActivityState: 3 is COMPLETED. Everything else is still to play.
_COMPLETED = 3

_SLUG_RE = re.compile(r'^tournament/[A-Za-z0-9_-]+/event/[A-Za-z0-9_-]+$')


class StartggAPIError(Exception):
    """Raised when start.gg answers with an error or an unexpected payload."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def parse_event_slug(id_or_url: str) -> str:
    """Turn a start.gg event link into the ``tournament/<t>/event/<e>`` slug.

    Accepts the full URL with or without a trailing tab (``/brackets``,
    ``/overview``, ...) or the bare slug. A tournament link without an event
    is refused, since one tournament can hold several events.
    """
    raw = (id_or_url or '').strip()
    if not raw:
        raise ValueError('Enter a start.gg event link.')
    path = urlparse(raw).path if '://' in raw else raw
    if not path.startswith('tournament/') and '/tournament/' in f'/{path}':
        path = path[path.index('tournament/'):]
    parts = [p for p in path.strip('/').split('/') if p]
    if len(parts) >= 4 and parts[0] == 'tournament' and parts[2] == 'event':
        slug = '/'.join(parts[:4])
        if _SLUG_RE.match(slug):
            return slug
    if len(parts) >= 2 and parts[0] == 'tournament':
        raise ValueError(
            'That links the whole tournament. Open the event on start.gg and paste '
            'its link, which has /event/ in it.'
        )
    raise ValueError("That doesn't look like a start.gg event link.")


def event_url(slug: str) -> str:
    return f'https://www.start.gg/{slug}'


def _ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _discord_id(user: Optional[Dict[str, Any]]) -> Optional[str]:
    for auth in (user or {}).get('authorizations') or []:
        if (auth or {}).get('externalId'):
            return str(auth['externalId'])
    return None


def _normalize_entrant(node: Dict[str, Any]) -> Dict[str, Any]:
    participants = node.get('participants') or []
    first = participants[0] if participants else {}
    user = first.get('user') or None
    return {
        'entrant_id': str(node['id']),
        'name': node.get('name') or first.get('gamerTag') or 'Unknown',
        'gamer_tag': first.get('gamerTag'),
        'startgg_user_id': opt_str((user or {}).get('id')),
        'discord_id': _discord_id(user),
    }


def _score(standing: Optional[Dict[str, Any]]) -> Optional[int]:
    value = (((standing or {}).get('stats') or {}).get('score') or {}).get('value')
    return None if value is None else int(value)


def _normalize_set(node: Dict[str, Any]) -> Dict[str, Any]:
    slots = []
    for slot in node.get('slots') or []:
        entrant = slot.get('entrant') or {}
        slots.append({
            'entrant_id': opt_str(entrant.get('id')),
            'score': _score(slot.get('standing')),
            'prereq_type': slot.get('prereqType'),
            'prereq_id': opt_str(slot.get('prereqId')),
            'prereq_placement': slot.get('prereqPlacement'),
        })
    return {
        'set_id': str(node['id']),
        'round': int(node.get('round') or 0),
        'round_text': node.get('fullRoundText'),
        'identifier': node.get('identifier'),
        'complete': node.get('state') == _COMPLETED or node.get('winnerId') is not None,
        'winner_id': opt_str(node.get('winnerId')),
        'completed_at': _ts(node.get('completedAt')),
        'slots': slots,
    }


_EVENT_QUERY = """
query Event($slug: String) {
  event(slug: $slug) {
    id name slug state
    phases {
      id name state bracketType phaseOrder
      phaseGroups(query: {page: 1, perPage: 64}) {
        nodes { id displayIdentifier state bracketType }
      }
    }
  }
}
"""

_ENTRANTS_QUERY = """
query Entrants($id: ID!, $page: Int!, $perPage: Int!) {
  event(id: $id) {
    entrants(query: {page: $page, perPage: $perPage}) {
      pageInfo { totalPages }
      nodes {
        id name
        participants {
          gamerTag
          user { id authorizations(types: [DISCORD]) { externalId } }
        }
      }
    }
  }
}
"""

_POOL_QUERY = """
query Pool($id: ID!, $page: Int!, $perPage: Int!, $standings: Int!) {
  phaseGroup(id: $id) {
    standings(query: {page: 1, perPage: $standings}) {
      nodes { placement entrant { id } }
    }
    sets(page: $page, perPage: $perPage, sortType: STANDARD) {
      pageInfo { totalPages }
      nodes {
        id round fullRoundText identifier state winnerId completedAt
        slots {
          prereqType prereqId prereqPlacement
          entrant { id }
          standing { stats { score { value } } }
        }
      }
    }
  }
}
"""

_SET_QUERY = """
query Set($id: ID!) {
  set(id: $id) { id state winnerId slots { entrant { id } } }
}
"""

_REPORT_MUTATION = """
mutation Report($setId: ID!, $winnerId: ID!) {
  reportBracketSet(setId: $setId, winnerId: $winnerId) { id state }
}
"""

_ME_QUERY = 'query { currentUser { id slug player { gamerTag } } }'

_ADMIN_TOURNAMENTS_QUERY = """
query AdminTournaments($page: Int!, $perPage: Int!) {
  currentUser {
    id
    tournaments(query: {page: $page, perPage: $perPage, filter: {tournamentView: "admin"}}) {
      pageInfo { totalPages }
      nodes { slug }
    }
  }
}
"""

_TOURNAMENT_ADMINS_QUERY = """
query TournamentAdmins($slug: String) {
  tournament(slug: $slug) { id owner { id } admins { id } }
}
"""

_ADMIN_PAGE = 50
_ADMIN_PAGES = 4


def tournament_slug(event_slug: str) -> str:
    """``tournament/x/event/y`` -> ``tournament/x``, the slug admin roles hang off."""
    return event_slug.split('/event/', 1)[0]


def _internal_error(payload: Any) -> bool:
    errors = payload.get('errors') if isinstance(payload, dict) else None
    return isinstance(errors, list) and bool(errors) and all(
        ((e or {}).get('extensions') or {}).get('category') == 'internal' for e in errors
    )


class StartggClient:
    """Read-only start.gg API client using a personal access token."""

    def __init__(self, token: Optional[str] = None) -> None:
        self.token = token if token is not None else os.getenv('STARTGG_API_TOKEN', '')

    async def _query(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        if not self.token:
            raise StartggAPIError(
                "start.gg isn't connected: sign in under Settings → Integrations, "
                'or set STARTGG_API_TOKEN.'
            )
        headers = {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}
        # One retry on a rate-limit answer: the limit is a rolling minute, so a
        # short wait usually clears a burst from a manual sync on top of the worker.
        for attempt in range(2):
            # Sentry SGLMAN-15: a raw timeout skipped the per-event handler, so one
            # slow answer aborted the community's whole sync and reported as an error.
            try:
                async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                    async with session.post(
                        API_URL, json={'query': query, 'variables': variables}, headers=headers,
                    ) as resp:
                        status = resp.status
                        try:
                            payload = await resp.json(content_type=None)
                        except ValueError as e:
                            raise StartggAPIError(f'start.gg returned non-JSON ({status})', status) from e
            except asyncio.TimeoutError as e:
                raise StartggAPIError("start.gg didn't answer in time; try again shortly.") from e
            except aiohttp.ClientError as e:
                raise StartggAPIError(f"Couldn't reach start.gg ({type(e).__name__}).") from e
            if status == 429 and attempt == 0:
                await asyncio.sleep(5)
                continue
            # Sentry SGLMAN-2H: start.gg answers a 200 with an "internal" error now
            # and then (its own fault, gone on the next request), so retry it once.
            if attempt == 0 and _internal_error(payload):
                await asyncio.sleep(1)
                continue
            break
        if status in (401, 403):
            raise StartggAPIError(
                'start.gg refused the sign-in. Reconnect start.gg under Settings → '
                'Integrations (or replace an expired STARTGG_API_TOKEN).', status,
            )
        if status >= 400:
            raise StartggAPIError(f'start.gg request failed ({status})', status)
        errors = (payload or {}).get('errors')
        if errors:
            message = '; '.join(str(e.get('message', e)) for e in errors)[:300]
            raise StartggAPIError(f'start.gg error: {message}', status)
        return (payload or {}).get('data') or {}

    async def get_event(self, slug: str) -> Dict[str, Any]:
        data = await self._query(_EVENT_QUERY, {'slug': slug})
        event = data.get('event')
        if not event:
            raise StartggAPIError(f'No start.gg event at {slug}.', 404)
        phases = []
        for phase in event.get('phases') or []:
            pools = [{
                'phase_group_id': str(g['id']),
                'identifier': str(g.get('displayIdentifier') or ''),
                'bracket_type': g.get('bracketType'),
                'complete': g.get('state') == _COMPLETED,
            } for g in ((phase.get('phaseGroups') or {}).get('nodes') or [])]
            phases.append({
                'phase_id': str(phase['id']),
                'name': phase.get('name') or 'Bracket',
                'phase_order': int(phase.get('phaseOrder') or 0),
                'bracket_type': phase.get('bracketType'),
                'state': phase.get('state'),
                'pools': pools,
            })
        return {
            'event_id': str(event['id']),
            'name': event.get('name') or slug,
            'slug': event.get('slug') or slug,
            'state': event.get('state'),
            'phases': phases,
        }

    async def get_entrants(self, event_id: str) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        page = 1
        while True:
            data = await self._query(
                _ENTRANTS_QUERY, {'id': event_id, 'page': page, 'perPage': _ENTRANT_PAGE},
            )
            if data.get('event') is None:
                # A null event would mirror as "no entrants" and prune the roster.
                raise StartggAPIError(f'start.gg returned no event {event_id}.')
            block = ((data.get('event') or {}).get('entrants')) or {}
            rows.extend(_normalize_entrant(n) for n in block.get('nodes') or [])
            if page >= int((block.get('pageInfo') or {}).get('totalPages') or 1):
                return rows
            page += 1

    async def get_pool(self, phase_group_id: str) -> Dict[str, Any]:
        standings: List[Dict[str, Any]] = []
        sets: List[Dict[str, Any]] = []
        page = 1
        while True:
            data = await self._query(_POOL_QUERY, {
                'id': phase_group_id, 'page': page, 'perPage': _SET_PAGE,
                'standings': _STANDINGS_PAGE if page == 1 else 1,
            })
            group = data.get('phaseGroup')
            if group is None:
                raise StartggAPIError(f'start.gg returned no pool {phase_group_id}.')
            if page == 1:
                standings = [
                    {'entrant_id': str(n['entrant']['id']), 'placement': n.get('placement')}
                    for n in ((group.get('standings') or {}).get('nodes') or [])
                    if n.get('entrant')
                ]
            block = group.get('sets') or {}
            sets.extend(_normalize_set(n) for n in block.get('nodes') or [])
            if page >= int((block.get('pageInfo') or {}).get('totalPages') or 1):
                return {'standings': standings, 'sets': sets}
            page += 1

    async def get_set_state(self, set_id: str) -> Dict[str, Any]:
        """Re-read one set right before writing, so a result already on start.gg wins."""
        data = await self._query(_SET_QUERY, {'id': set_id})
        node = data.get('set')
        if not node:
            raise StartggAPIError(f'No start.gg set {set_id}.', 404)
        return {
            'complete': node.get('state') == _COMPLETED or node.get('winnerId') is not None,
            'entrant_ids': [str((s.get('entrant') or {}).get('id')) for s in node.get('slots') or []],
        }

    async def report_winner(self, set_id: str, winner_entrant_id: str) -> None:
        await self._query(_REPORT_MUTATION, {'setId': set_id, 'winnerId': winner_entrant_id})

    async def get_admin_tournament_slugs(self) -> Tuple[Optional[str], Set[str]]:
        """The signed-in account's id and the tournaments it holds an admin role on.

        Capped at a few pages: a staff account admins a handful of tournaments,
        and this runs when the Integrations card opens.
        """
        slugs: Set[str] = set()
        user_id: Optional[str] = None
        for page in range(1, _ADMIN_PAGES + 1):
            data = await self._query(_ADMIN_TOURNAMENTS_QUERY, {'page': page, 'perPage': _ADMIN_PAGE})
            me = data.get('currentUser') or {}
            user_id = opt_str(me.get('id'))
            block = me.get('tournaments') or {}
            slugs.update(n['slug'] for n in block.get('nodes') or [] if n and n.get('slug'))
            if page >= int((block.get('pageInfo') or {}).get('totalPages') or 1):
                break
        return user_id, slugs

    async def get_tournament_admin_ids(self, slug: str) -> Set[str]:
        """Owner plus admins of a tournament, as far as this account may see them."""
        data = await self._query(_TOURNAMENT_ADMINS_QUERY, {'slug': slug})
        node = data.get('tournament') or {}
        ids = [(node.get('owner') or {}).get('id')] + [a.get('id') for a in node.get('admins') or [] if a]
        return {str(i) for i in ids if i is not None}

    async def get_event_full(self, slug: str) -> Dict[str, Any]:
        """The event's shape, every entrant, and every pool's standings and sets."""
        event = await self.get_event(slug)
        entrants = await self.get_entrants(event['event_id'])
        pools: Dict[str, Dict[str, Any]] = {}
        for phase in event['phases']:
            for pool in phase['pools']:
                pools[pool['phase_group_id']] = await self.get_pool(pool['phase_group_id'])
        return {'event': event, 'entrants': entrants, 'pools': pools}


_PLAYER_SCOPE = 'user.identity'
CONNECT_SCOPE = 'user.identity tournament.reporter'


def build_authorize_url(client_id: str, redirect_uri: str, scope: str, state: str) -> str:
    return _build_authorize_url(AUTHORIZE_URL, client_id, redirect_uri, scope, state)


def _extract_identity(payload: Any) -> Dict[str, Any]:
    if (payload or {}).get('errors'):
        raise StartggAPIError(f"start.gg currentUser failed: {payload['errors']}")
    me = ((payload or {}).get('data') or {}).get('currentUser') or {}
    if not me.get('id'):
        raise StartggAPIError('start.gg returned no signed-in user.')
    gamer_tag = (me.get('player') or {}).get('gamerTag')
    return {
        'user_id': opt_str(me.get('id')),
        'username': gamer_tag or me.get('slug'),
        'display_name': gamer_tag,
    }


class StartggOAuthClient(OAuthIdentityClient):
    """start.gg's OAuth differs from the shared shape in two places: the token
    exchange takes a JSON body, and identity is a GraphQL query."""

    config = ProviderConfig(
        label='start.gg',
        token_url=TOKEN_URL,
        userinfo_url=API_URL,
        error_class=StartggAPIError,
        extract_identity=_extract_identity,
    )

    async def exchange_code(
        self, code: str, redirect_uri: str, scope: str = _PLAYER_SCOPE,
    ) -> Dict[str, Any]:
        return await self._token_request(TOKEN_URL, {
            'grant_type': 'authorization_code',
            'code': code,
            'scope': scope,
            'redirect_uri': redirect_uri,
        })

    async def refresh(self, refresh_token: str, redirect_uri: str, scope: str) -> Dict[str, Any]:
        # start.gg wants the original scope and redirect URI repeated on a refresh.
        return await self._token_request(REFRESH_URL, {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'scope': scope,
            'redirect_uri': redirect_uri,
        })

    async def _token_request(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        body = {**body, 'client_id': self.client_id, 'client_secret': self.client_secret}
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.post(url, json=body) as resp:
                    payload = await resp.json(content_type=None)
                    if resp.status >= 400:
                        raise StartggAPIError(f'start.gg token request failed ({resp.status})', resp.status)
                    return payload
        except asyncio.TimeoutError as e:
            raise StartggAPIError("start.gg didn't answer in time; try again shortly.") from e
        except aiohttp.ClientError as e:
            raise StartggAPIError(f"Couldn't reach start.gg ({type(e).__name__}).") from e

    async def get_me(self, access_token: str) -> Dict[str, Any]:
        headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(API_URL, json={'query': _ME_QUERY}, headers=headers) as resp:
                if resp.status >= 400:
                    raise StartggAPIError(f'start.gg currentUser failed ({resp.status})', resp.status)
                payload = await resp.json(content_type=None)
        return _extract_identity(payload)


class MockStartggOAuthClient(StartggOAuthClient):
    """Canned identity for MOCK_STARTGG, so dev can click through link/unlink."""

    _IDENTITIES: Tuple[Dict[str, str], ...] = (
        {'user_id': '30001', 'username': 'MockGGOne', 'display_name': 'MockGGOne'},
        {'user_id': '30002', 'username': 'MockGGTwo', 'display_name': 'MockGGTwo'},
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__('mock', 'mock')

    async def exchange_code(
        self, code: str, redirect_uri: str, scope: str = _PLAYER_SCOPE,
    ) -> Dict[str, Any]:
        return {
            'access_token': 'mock-access', 'refresh_token': 'mock-refresh',
            'token_type': 'Bearer', 'expires_in': 604800,
        }

    async def refresh(self, refresh_token: str, redirect_uri: str, scope: str) -> Dict[str, Any]:
        return await self.exchange_code('mock', redirect_uri, scope)

    async def get_me(self, access_token: str) -> Dict[str, Any]:
        idx = int(os.environ.get('MOCK_STARTGG_IDENTITY', '1'))
        return dict(self._IDENTITIES[(idx - 1) % len(self._IDENTITIES)])
