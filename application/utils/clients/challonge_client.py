"""Challonge API v2.1 transport client.

A thin async ``aiohttp`` wrapper over the Challonge v2.1 (JSON:API) endpoints
we need, plus the OAuth token exchange/refresh. There is no mature Python
library for v2.1, so this lives here and is deliberately small.

The client returns **normalized** Python dicts so the service layer never has
to know the JSON:API wire shape:

    me               -> {'user_id', 'username'}
    tournament       -> {'id', 'name', 'url', 'state', 'group_stage', 'tournament_type'}
    participant      -> {'participant_id', 'name', 'challonge_user_id', 'username', 'misc'}
    match            -> {'match_id', 'state', 'round',
                         'player1_participant_id', 'player2_participant_id',
                         'winner_participant_id'}

Authenticated service calls obtain a bearer token via a ``token_provider``
callback (``async (force_refresh: bool = False) -> str``) so the client can
transparently refresh-on-401 and retry once. OAuth helpers
(:meth:`exchange_code`, :meth:`refresh`, :meth:`get_me`) take explicit tokens
because they run outside an established connection (e.g. the player-link
callback, which never stores a token).
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional
from urllib.parse import quote

import aiohttp

AUTHORIZE_URL = 'https://api.challonge.com/oauth/authorize'
TOKEN_URL = 'https://api.challonge.com/oauth/token'
BASE_URL = 'https://api.challonge.com/v2.1'
_PAGE_SIZE = 100
# aiohttp's default is five minutes, long enough to freeze a staff click or the
# DM queue a result push rides on while Challonge is down.
_TIMEOUT = aiohttp.ClientTimeout(total=20)

TokenProvider = Callable[..., Awaitable[str]]
RequestHook = Callable[[], Awaitable[None]]


class ChallongeAPIError(Exception):
    """Raised when the Challonge API returns an error or unexpected payload."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


@asynccontextmanager
async def _session() -> AsyncIterator[aiohttp.ClientSession]:
    # Callers only catch ChallongeAPIError, so a hung or unreachable Challonge
    # must surface as one rather than escape as a raw aiohttp/timeout error.
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            yield session
    except asyncio.TimeoutError as e:
        raise ChallongeAPIError('Challonge did not respond in time') from e
    except aiohttp.ClientError as e:
        raise ChallongeAPIError(f'Could not reach Challonge: {e}') from e


def build_authorize_url(
    client_id: str, redirect_uri: str, scope: str, state: str,
    community_id: Optional[str] = None,
) -> str:
    """Return the Challonge OAuth authorize URL to redirect the browser to.

    ``community_id`` (a subdomain or permalink) asks Challonge to grant access
    to that community; ``communities:manage`` alone does not prompt for one.
    """
    url = (
        f"{AUTHORIZE_URL}"
        f"?client_id={quote(client_id, safe='')}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&scope={quote(scope, safe='')}"
        f"&state={quote(state, safe='')}"
    )
    if community_id:
        url += f"&community_id={quote(community_id, safe='')}"
    return url


def _attr(resource: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Pull the first present key from a JSON:API resource's ``attributes``.

    v2.1 attribute names are not always documented, so we try a few synonyms
    and fall back to None rather than KeyError.
    """
    attrs = resource.get('attributes') or {}
    for key in keys:
        if key in attrs and attrs[key] is not None:
            return attrs[key]
    return None


def _rel_id(resource: Dict[str, Any], rel: str) -> Optional[str]:
    """Pull a to-one relationship's id from a JSON:API resource."""
    rels = resource.get('relationships') or {}
    data = (rels.get(rel) or {}).get('data')
    if isinstance(data, dict) and data.get('id') is not None:
        return str(data['id'])
    return None


def _normalize_tournament(resource: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'id': str(resource.get('id')),
        'name': _attr(resource, 'name'),
        'url': _attr(resource, 'full_challonge_url', 'url'),
        'state': _attr(resource, 'state'),
        'group_stage': bool(_attr(resource, 'group_stage_enabled')),
        'tournament_type': _attr(resource, 'tournament_type'),
    }


def _normalize_participant(resource: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'participant_id': str(resource.get('id')),
        'name': _attr(resource, 'name', 'display_name'),
        'challonge_user_id': _opt_str(
            _attr(resource, 'challonge_user_id', 'user_id') or _rel_id(resource, 'user')
        ),
        'username': _attr(resource, 'challonge_username', 'username'),
        'misc': _opt_str(_attr(resource, 'misc')),
        # Challonge keeps an entrant dropped after the start as inactive.
        'active': _attr(resource, 'active') is not False,
    }


def _normalize_match(resource: Dict[str, Any]) -> Dict[str, Any]:
    # Final-stage matches of a two-stage tournament carry no player1/player2
    # relationships; their players appear only in ``points_by_participant``
    # (player 1 first). Group-stage and single-stage matches carry both.
    points = _attr(resource, 'points_by_participant') or []
    by_points = [_opt_str(p.get('participant_id')) for p in points if isinstance(p, dict)]
    by_points += [None, None]
    return {
        'match_id': str(resource.get('id')),
        'state': _attr(resource, 'state'),
        'round': _attr(resource, 'round'),
        'player1_participant_id': _opt_str(
            _attr(resource, 'player1_id') or _rel_id(resource, 'player1')
        ) or by_points[0],
        'player2_participant_id': _opt_str(
            _attr(resource, 'player2_id') or _rel_id(resource, 'player2')
        ) or by_points[1],
        'winner_participant_id': _opt_str(
            _attr(resource, 'winner_id') or _rel_id(resource, 'winner')
        ),
    }


def _opt_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _tournament_path(tournament_id: str, community: Optional[str] = None) -> str:
    # A tournament hosted on an organization subdomain (``org.challonge.com/x``)
    # only resolves under its community scope; v2.1 rejects the v1-style
    # ``org-x`` identifier.
    path = f'/tournaments/{quote(str(tournament_id), safe="")}'
    if community:
        path = f'/communities/{quote(community, safe="")}{path}'
    return path


class ChallongeClient:
    """Async Challonge v2.1 client."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_provider: Optional[TokenProvider] = None,
        on_request: Optional[RequestHook] = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token_provider = token_provider
        self._on_request = on_request

    async def _count_request(self) -> None:
        """Notify the configured hook of one real outbound HTTP request."""
        if self._on_request is not None:
            await self._on_request()

    # ------------------------------------------------------------------
    # OAuth (explicit-token / no-connection context)
    # ------------------------------------------------------------------
    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """Exchange an authorization code for a token payload."""
        return await self._token_request({
            'grant_type': 'authorization_code',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': redirect_uri,
            'code': code,
        })

    async def refresh(self, refresh_token: str) -> Dict[str, Any]:
        """Exchange a refresh token for a new token payload."""
        return await self._token_request({
            'grant_type': 'refresh_token',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': refresh_token,
        })

    async def _token_request(self, data: Dict[str, str]) -> Dict[str, Any]:
        await self._count_request()
        async with _session() as session:
            async with session.post(TOKEN_URL, data=data) as resp:
                payload = await resp.json()
                if resp.status >= 400:
                    raise ChallongeAPIError(
                        f"Challonge token request failed ({resp.status}): {payload}"
                    )
                return payload

    async def get_me(self, access_token: str) -> Dict[str, Any]:
        """Fetch the authenticated Challonge account identity for a raw token."""
        data = await self._raw_get('/me.json', access_token)
        resource = data.get('data') or {}
        return {
            'user_id': _opt_str(resource.get('id') or _attr(resource, 'id')),
            'username': _attr(resource, 'username', 'name'),
        }

    # ------------------------------------------------------------------
    # Authenticated service calls (token via token_provider)
    # ------------------------------------------------------------------
    async def get_tournament(self, tournament_id: str, community: Optional[str] = None) -> Dict[str, Any]:
        data = await self._authed_request('GET', f'{_tournament_path(tournament_id, community)}.json')
        resource = data.get('data') or {}
        return _normalize_tournament(resource)

    async def list_participants(
        self, tournament_id: str, community: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = await self._authed_get_all(
            f'{_tournament_path(tournament_id, community)}/participants.json',
        )
        return [_normalize_participant(r) for r in rows]

    async def list_matches(
        self, tournament_id: str, community: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = await self._authed_get_all(
            f'{_tournament_path(tournament_id, community)}/matches.json',
        )
        return [_normalize_match(r) for r in rows]

    async def get_match(
        self, tournament_id: str, match_id: str, community: Optional[str] = None,
    ) -> Dict[str, Any]:
        data = await self._authed_request(
            'GET', f'{_tournament_path(tournament_id, community)}/matches/{match_id}.json',
        )
        return _normalize_match(data.get('data') or {})

    async def get_tournament_full(
        self, tournament_id: str, community: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch a tournament with its participants and matches, usually in one request.

        Challonge embeds the associated participant/match records via the
        ``include_participants`` / ``include_matches`` flags, collapsing what
        would otherwise be three calls into one. Embedded resources arrive in
        the JSON:API ``included`` array, partitioned by ``type``.
        """
        data = await self._authed_request(
            'GET',
            f'{_tournament_path(tournament_id, community)}.json'
            '?include_participants=1&include_matches=1',
        )
        resource = data.get('data') or {}
        if not resource.get('id'):
            # A sync prunes whatever this answer leaves out, so an empty or
            # truncated 200 must never read as "the bracket is now empty".
            raise ChallongeAPIError('Challonge returned no tournament in its answer')
        tournament = _normalize_tournament(resource)
        participants: List[Dict[str, Any]] = []
        matches: List[Dict[str, Any]] = []
        for included in (data.get('included') or []):
            kind = (included.get('type') or '').lower()
            if kind in ('participant', 'participants'):
                participants.append(_normalize_participant(included))
            elif kind in ('match', 'matches'):
                matches.append(_normalize_match(included))
        # The embedded participants carry an empty ``user`` relationship; only the
        # participants endpoint fills it in. An entrant with a Challonge account
        # (it has a username) needs that id to map to its linked Wizzrobe user.
        if any(p['username'] and not p['challonge_user_id'] for p in participants):
            user_ids = {
                p['participant_id']: p['challonge_user_id']
                for p in await self.list_participants(tournament_id, community=community)
            }
            for p in participants:
                p['challonge_user_id'] = p['challonge_user_id'] or user_ids.get(p['participant_id'])
        return {'tournament': tournament, 'participants': participants, 'matches': matches}

    async def change_state(
        self, tournament_id: str, state: str, community: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply a ``change_state`` action (``start``, ``finalize_group_stage``, ...).

        Returns the normalized tournament as Challonge reports it afterwards.
        """
        body = {'data': {'type': 'TournamentState', 'attributes': {'state': state}}}
        data = await self._authed_request(
            'PUT', f'{_tournament_path(tournament_id, community)}/change_state.json', json=body,
        )
        return _normalize_tournament(data.get('data') or {})

    async def update_match(
        self,
        tournament_id: str,
        match_id: str,
        winner_participant_id: str,
        loser_participant_id: str,
        winner_score: str = '1',
        loser_score: str = '0',
        community: Optional[str] = None,
    ) -> None:
        """Report a match result. The winner is flagged ``advancing: true``."""
        body = {
            'data': {
                'type': 'Match',
                'attributes': {
                    'match': [
                        {
                            'participant_id': str(winner_participant_id),
                            'score_set': str(winner_score),
                            'advancing': True,
                        },
                        {
                            'participant_id': str(loser_participant_id),
                            'score_set': str(loser_score),
                            'advancing': False,
                        },
                    ]
                },
            }
        }
        await self._authed_request(
            'PUT', f'{_tournament_path(tournament_id, community)}/matches/{match_id}.json',
            json=body,
        )

    async def create_participant(
        self,
        tournament_id: str,
        *,
        name: str,
        username: str,
        misc: Optional[str] = None,
        community: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add an entrant by Challonge username; Challonge invites that account."""
        attributes: Dict[str, Any] = {'name': name, 'username': username}
        if misc:
            attributes['misc'] = misc
        body = {'data': {'type': 'Participant', 'attributes': attributes}}
        data = await self._authed_request(
            'POST', f'{_tournament_path(tournament_id, community)}/participants.json', json=body,
        )
        return _normalize_participant(data.get('data') or {})

    async def delete_participant(
        self, tournament_id: str, participant_id: str, community: Optional[str] = None,
    ) -> None:
        await self._authed_request(
            'DELETE',
            f'{_tournament_path(tournament_id, community)}/participants/'
            f'{quote(str(participant_id), safe="")}.json',
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _headers(self, token: str) -> Dict[str, str]:
        return {
            'Content-Type': 'application/vnd.api+json',
            'Accept': 'application/json',
            'Authorization-Type': 'v2',
            'Authorization': f'Bearer {token}',
        }

    async def _raw_get(self, path: str, token: str) -> Dict[str, Any]:
        await self._count_request()
        async with _session() as session:
            async with session.get(BASE_URL + path, headers=self._headers(token)) as resp:
                return await self._parse(resp)

    async def _authed_get_all(self, path: str) -> List[Dict[str, Any]]:
        """GET every page of a list endpoint (25 rows per page by default).

        Asks for 100 per page so a typical bracket is one request, and follows
        ``links.next`` for anything larger.
        """
        rows: List[Dict[str, Any]] = []
        next_path: Optional[str] = f'{path}?per_page={_PAGE_SIZE}'
        while next_path:
            data = await self._authed_request('GET', next_path)
            rows.extend(data.get('data') or [])
            next_url = (data.get('links') or {}).get('next')
            next_path = next_url[len(BASE_URL):] if next_url and next_url.startswith(BASE_URL) else None
        return rows

    async def _authed_request(
        self, method: str, path: str, *, json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if self._token_provider is None:
            raise ChallongeAPIError('No token provider configured for authenticated calls')
        try:
            return await self._send(self._token_provider, method, path, json)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ChallongeAPIError(f"Challonge didn't answer ({type(e).__name__})") from e

    async def _send(
        self, token_provider: TokenProvider, method: str, path: str,
        json: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        token = await token_provider()
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            await self._count_request()
            async with session.request(
                method, BASE_URL + path, headers=self._headers(token), json=json,
            ) as resp:
                if resp.status == 401:
                    # Token may have expired between refresh checks — force one refresh + retry.
                    token = await token_provider(force_refresh=True)
                    await self._count_request()
                    async with session.request(
                        method, BASE_URL + path, headers=self._headers(token), json=json,
                    ) as retry:
                        return await self._parse(retry)
                return await self._parse(resp)

    @staticmethod
    async def _parse(resp: aiohttp.ClientResponse) -> Dict[str, Any]:
        text = await resp.text()
        if resp.status >= 400:
            raise ChallongeAPIError(f"Challonge API error ({resp.status}): {text}", status=resp.status)
        if not text:
            return {}
        import json as _json
        try:
            return _json.loads(text)
        except ValueError as e:
            raise ChallongeAPIError(f"Challonge API returned non-JSON: {text[:200]}") from e


# ----------------------------------------------------------------------
# Mock client (MOCK_CHALLONGE) — a tiny canned 4-player single-elim bracket
# so local dev can click through connect/link/sync/schedule without a real app.
# ----------------------------------------------------------------------
_MOCK_PARTICIPANTS = [
    {'participant_id': '9001', 'name': 'MockPlayerOne', 'challonge_user_id': '1001', 'username': 'mockone'},
    {'participant_id': '9002', 'name': 'MockPlayerTwo', 'challonge_user_id': '1002', 'username': 'mocktwo'},
    {'participant_id': '9003', 'name': 'MockPlayerThree', 'challonge_user_id': '1003', 'username': 'mockthree'},
    {'participant_id': '9004', 'name': 'MockPlayerFour', 'challonge_user_id': '1004', 'username': 'mockfour'},
]
_MOCK_MATCHES = [
    {'match_id': '8001', 'state': 'open', 'round': 1, 'player1_participant_id': '9001',
     'player2_participant_id': '9002', 'winner_participant_id': None},
    {'match_id': '8002', 'state': 'open', 'round': 1, 'player1_participant_id': '9003',
     'player2_participant_id': '9004', 'winner_participant_id': None},
    {'match_id': '8003', 'state': 'pending', 'round': 2, 'player1_participant_id': None,
     'player2_participant_id': None, 'winner_participant_id': None},
]


class MockChallongeClient(ChallongeClient):
    """Canned client used when MOCK_CHALLONGE is enabled."""

    def __init__(self, *args, **kwargs):
        super().__init__('mock', 'mock', kwargs.get('token_provider'))

    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        return {'access_token': 'mock-access', 'refresh_token': 'mock-refresh', 'expires_in': 604800,
                'scope': 'me tournaments:read tournaments:write matches:read matches:write '
                         'participants:read participants:write communities:manage'}

    async def refresh(self, refresh_token: str) -> Dict[str, Any]:
        return await self.exchange_code('mock', 'mock')

    async def get_me(self, access_token: str) -> Dict[str, Any]:
        # Deterministic per-call identity so each linking dev user maps to a
        # distinct mock participant by their numeric position is not possible;
        # use an env hint to choose which mock identity to bind.
        idx = int(os.environ.get('MOCK_CHALLONGE_IDENTITY', '1'))
        p = _MOCK_PARTICIPANTS[(idx - 1) % len(_MOCK_PARTICIPANTS)]
        return {'user_id': p['challonge_user_id'], 'username': p['username']}

    async def get_tournament(self, tournament_id: str, community: Optional[str] = None) -> Dict[str, Any]:
        return {'id': str(tournament_id), 'name': 'Mock Challonge Tournament',
                'url': f'https://challonge.com/{tournament_id}', 'state': 'underway',
                'group_stage': False, 'tournament_type': 'single elimination'}

    async def list_participants(
        self, tournament_id: str, community: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return [dict(p) for p in _MOCK_PARTICIPANTS]

    async def list_matches(
        self, tournament_id: str, community: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return [dict(m) for m in _MOCK_MATCHES]

    async def get_match(
        self, tournament_id: str, match_id: str, community: Optional[str] = None,
    ) -> Dict[str, Any]:
        return next((dict(m) for m in _MOCK_MATCHES if m['match_id'] == str(match_id)), dict(_MOCK_MATCHES[0]))

    async def get_tournament_full(
        self, tournament_id: str, community: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            'tournament': await self.get_tournament(tournament_id),
            'participants': [dict(p) for p in _MOCK_PARTICIPANTS],
            'matches': [dict(m) for m in _MOCK_MATCHES],
        }

    async def update_match(self, tournament_id, match_id, winner_participant_id,
                           loser_participant_id, winner_score='1', loser_score='0',
                           community=None) -> None:
        print(f"[MOCK Challonge] update_match t={tournament_id} m={match_id} "
              f"winner={winner_participant_id} {winner_score}-{loser_score}")

    async def change_state(self, tournament_id, state, community=None) -> Dict[str, Any]:
        print(f"[MOCK Challonge] change_state t={tournament_id} state={state}")
        return await self.get_tournament(tournament_id, community=community)

    async def create_participant(
        self, tournament_id, *, name, username, misc=None, community=None,
    ) -> Dict[str, Any]:
        print(f"[MOCK Challonge] create_participant t={tournament_id} username={username}")
        return {'participant_id': f'mock-{username}', 'name': name, 'challonge_user_id': None,
                'username': username, 'misc': misc}

    async def delete_participant(self, tournament_id, participant_id, community=None) -> None:
        print(f"[MOCK Challonge] delete_participant t={tournament_id} p={participant_id}")
