"""racetime.gg race-room creation: the bot's ``startrace`` call.

A room only exists on racetime.gg once a category bot asks for it. The bot
authenticates with its own client credentials (``client_credentials`` grant),
then POSTs the room settings to ``/o/<category>/startrace``; racetime answers
``201`` with the new room's path in ``Location`` (``/<category>/<slug-words>``),
and that path, not anything we make up, is the room's slug.

The goal is resolved against the category's published goal list: a name racetime
already knows goes as ``goal``, anything else as ``custom_goal`` (which the
category must allow, or racetime refuses and its message is surfaced).

The mock client (``MOCK_RACETIME``) hands back a fresh ``<category>/mock-…``
slug without touching the network, so dev and tests can open rooms freely.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from application.utils.clients.racetime_client import (
    OAUTH_EXCHANGE_URL,
    RACETIME_BASE,
    RacetimeAPIError,
)

_TIMEOUT_SECONDS = 15
# Replace a cached bot token this long before racetime says it expires.
_REFRESH_MARGIN_SECONDS = 600
_DEFAULT_TOKEN_LIFETIME_SECONDS = 36000

# Process-wide bot token cache, keyed by client id. Shared infrastructure like
# the discord queue, not per-user state: every room a category opens rides the
# same bot credential, and a fresh grant per room would only earn rate limits.
_tokens: Dict[str, Tuple[str, float]] = {}


@dataclass(frozen=True)
class RaceRoomSettings:
    """What a room is opened with — the ``RaceRoomProfile`` fields plus a goal."""

    goal: str
    info_user: str = ''
    invitational: bool = False
    unlisted: bool = False
    auto_start: bool = True
    allow_comments: bool = True
    allow_midrace_chat: bool = True
    allow_non_entrant_chat: bool = True
    chat_message_delay: int = 0
    start_delay: int = 15
    time_limit: int = 24
    streaming_required: bool = False

    @classmethod
    def from_profile(cls, profile, *, goal: str, info_user: str = '') -> 'RaceRoomSettings':
        """Settings from a ``RaceRoomProfile`` (or racetime's defaults for ``None``)."""
        if profile is None:
            return cls(goal=goal, info_user=info_user)
        return cls(
            goal=goal,
            info_user=info_user,
            invitational=profile.invitational,
            unlisted=profile.unlisted,
            auto_start=profile.auto_start,
            allow_comments=profile.allow_comments,
            allow_midrace_chat=profile.allow_midrace_chat,
            allow_non_entrant_chat=profile.allow_non_entrant_chat,
            chat_message_delay=profile.chat_message_delay,
            start_delay=profile.start_delay,
            time_limit=profile.time_limit,
            streaming_required=profile.streaming_required,
        )

    def form(self, category_goals: list) -> Dict[str, str]:
        """The ``startrace`` form body. Booleans as the strings racetime parses."""
        def flag(value: bool) -> str:
            return 'true' if value else 'false'

        body = {
            'info_user': self.info_user[:1000],
            'team_race': 'false',
            'invitational': flag(self.invitational),
            'unlisted': flag(self.unlisted),
            'auto_start': flag(self.auto_start),
            'allow_comments': flag(self.allow_comments),
            'hide_comments': 'false',
            'allow_prerace_chat': 'true',
            'allow_midrace_chat': flag(self.allow_midrace_chat),
            'allow_non_entrant_chat': flag(self.allow_non_entrant_chat),
            'chat_message_delay': str(self.chat_message_delay),
            'start_delay': str(self.start_delay),
            'time_limit': str(self.time_limit),
            'streaming_required': flag(self.streaming_required),
        }
        if self.goal in category_goals:
            body['goal'] = self.goal
        else:
            body['custom_goal'] = self.goal
        return body


def _refusal_reason(body: str) -> str:
    """racetime's own words from a refusal (its JSON ``errors``), else ''.

    An error page is HTML, which would land in a staff toast as markup.
    """
    import json

    try:
        payload = json.loads(body)
    except ValueError:
        return ''
    errors = payload.get('errors') if isinstance(payload, dict) else None
    if isinstance(errors, list):
        return '; '.join(str(e) for e in errors)[:300]
    if isinstance(errors, dict):
        return '; '.join(f'{k}: {v}' for k, v in errors.items())[:300]
    return ''


def slug_from_location(location: Optional[str]) -> str:
    """``/alttpr/clever-link-1234`` (or an absolute URL) -> ``alttpr/clever-link-1234``."""
    path = (location or '').strip()
    if path.startswith(RACETIME_BASE):
        path = path[len(RACETIME_BASE):]
    path = path.strip('/')
    if path.count('/') != 1:
        raise RacetimeAPIError(f'racetime returned an unexpected room location: {location!r}')
    return path


class RacetimeRoomsClient:
    """Opens race rooms on racetime.gg as a category bot."""

    async def start_race(
        self, *, category: str, client_id: str, client_secret: str,
        settings: RaceRoomSettings,
    ) -> str:
        """Open a room and return racetime's slug for it (``<category>/<words>``)."""
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                token = await self._token(session, client_id, client_secret)
                goals = await self._category_goals(session, category)
                async with session.post(
                    f'{RACETIME_BASE}/o/{category}/startrace',
                    data=settings.form(goals),
                    headers={'Authorization': f'Bearer {token}'},
                    allow_redirects=False,
                ) as resp:
                    if resp.status == 401:
                        _tokens.pop(client_id, None)
                    if resp.status not in (200, 201):
                        reason = _refusal_reason(await resp.text())
                        raise RacetimeAPIError(
                            f'racetime refused to open a {category} room '
                            f'({resp.status}){": " + reason if reason else ""}'
                        )
                    return slug_from_location(resp.headers.get('Location'))
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise RacetimeAPIError(
                f'could not reach racetime.gg: {exc or type(exc).__name__}'
            ) from exc

    async def _token(self, session, client_id: str, client_secret: str) -> str:
        cached = _tokens.get(client_id)
        if cached is not None and time.monotonic() < cached[1]:
            return cached[0]
        async with session.post(OAUTH_EXCHANGE_URL, data={
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'client_credentials',
        }) as resp:
            if resp.status >= 500:
                raise RacetimeAPIError(f'racetime.gg is having trouble ({resp.status})')
            if resp.status >= 400:
                raise RacetimeAPIError(
                    f'racetime rejected the bot credentials ({resp.status})'
                )
            payload = await resp.json()
        token = payload.get('access_token')
        if not token:
            raise RacetimeAPIError('racetime token response missing access_token')
        lifetime = payload.get('expires_in')
        if not isinstance(lifetime, (int, float)) or lifetime <= 0:
            lifetime = _DEFAULT_TOKEN_LIFETIME_SECONDS
        expires = time.monotonic() + max(60.0, float(lifetime) - _REFRESH_MARGIN_SECONDS)
        _tokens[client_id] = (token, expires)
        return token

    async def _category_goals(self, session, category: str) -> list:
        async with session.get(f'{RACETIME_BASE}/{category}/data') as resp:
            if resp.status >= 400:
                raise RacetimeAPIError(
                    f'racetime has no category {category!r} ({resp.status})'
                )
            payload = await resp.json()
        goals = payload.get('goals') if isinstance(payload, dict) else None
        return list(goals) if isinstance(goals, list) else []


class MockRacetimeRoomsClient(RacetimeRoomsClient):
    """``MOCK_RACETIME``: a fresh fake slug per room, no network."""

    def __init__(self) -> None:
        self.started: list = []

    async def start_race(
        self, *, category: str, client_id: str, client_secret: str,
        settings: RaceRoomSettings,
    ) -> str:
        self.started.append((category, settings))
        return f'{category}/mock-room-{secrets.token_hex(4)}'


def build_rooms_client() -> RacetimeRoomsClient:
    """The mock client under ``MOCK_RACETIME``, else the live one."""
    from application.utils.mocks.mock_racetime import is_mock_racetime

    if is_mock_racetime():
        return MockRacetimeRoomsClient()
    return RacetimeRoomsClient()
