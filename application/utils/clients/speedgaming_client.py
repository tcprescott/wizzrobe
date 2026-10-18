"""SpeedGaming schedule API transport client (PR 7).

A thin async wrapper over the one SG endpoint the ETL needs: the public schedule
feed at ``https://speedgaming.org/api/schedule``. It returns the **raw** episode
dicts SG serves; normalization into Wizzrobe's shape happens in the ETL service, so
this layer stays a pure transport.

``MOCK_SPEEDGAMING`` swaps in :class:`MockSpeedGamingClient`, a deterministic
scripted fake that returns canned episodes so local dev and the browser
validation loop can exercise the full ETL without hitting speedgaming.org. Like
the other mock flags it refuses to run under ``ENVIRONMENT=production``.

SG publishes no schema, so the wire shape encoded here was captured from the live
feed (``GET /api/schedule/`` with no filter returns every active event's
episodes) and cross-checked against SahasrahBot
(github.com/tcprescott/sahasrahbot), which has consumed it in production for
years. The key sets are pinned in the ``SG_*_KEYS`` manifests below; the mock
fixtures and their tests are validated against them so the fake cannot drift
into a shape the real API never serves.

Four behaviours of the live API that the naive reading gets wrong, and that this
client therefore handles explicitly:

* **JSON is served as ``Content-Type: text/html``** — never call ``resp.json()``
  without an override; we parse ``resp.text()`` instead.
* **Errors come back 200 with a JSON object** (``{"error": "Failed to find
  event."}``), not a list and not a 4xx. An unknown event slug looks like a
  success to the transport unless the body is inspected.
* **Absent values are ``''``, not ``null``.** Every scalar on a player, crew
  member, match side, and event is a string that is empty when unset — a player
  with no linked Discord account carries ``discordId: ''``. ``match2`` is the
  one field that is genuinely ``null``. Consumers must test truthiness, not
  ``is None``.
* **Times are always UTC-offset ISO strings** and ``whenCountdown`` matches
  ``when``. The episode's own ``timezone`` field is empty and the ``timezone``
  query parameter is ignored, so there is no server-side localization to
  respect — the ETL's job is purely to attach ``timezone.utc``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from application.utils.environment import env_flag, is_production

SPEEDGAMING_BASE = 'https://speedgaming.org'
SCHEDULE_URL = f'{SPEEDGAMING_BASE}/api/schedule'

# A courteous cap so a misconfigured window never pulls an unbounded page.
REQUEST_TIMEOUT_SECONDS = 20

# ----------------------------------------------------------------------
# Wire-shape manifest — the exact key set the live feed serves per object.
#
# Captured from an unfiltered ``GET /api/schedule/`` (25 episodes across 9
# events). SG serves every key on every object, so these are exact sets rather
# than minimums: a key missing from a payload means the shape changed upstream,
# and a key we invent is one no consumer may rely on.
# ----------------------------------------------------------------------
SG_EPISODE_KEYS = frozenset({
    'id', 'title', 'when', 'whenCountdown', 'length', 'timezone', 'approved',
    'externalRestream', 'event', 'match1', 'match2', 'channels',
    'commentators', 'trackers', 'broadcasters', 'helpers',
})
SG_EVENT_KEYS = frozenset({
    'id', 'slug', 'name', 'shortName', 'game', 'active', 'srtv', 'srl', 'botChannel',
})
SG_MATCH_KEYS = frozenset({'id', 'title', 'note', 'players'})
SG_PLAYER_KEYS = frozenset({
    'id', 'displayName', 'discordId', 'discordTag', 'publicStream', 'streamingFrom',
})
# Crew carry two fields players do not: ``approved`` (staff accepted the signup)
# and ``ready`` (the volunteer said they are set). Both flip between polls.
SG_CREW_KEYS = frozenset({
    'id', 'displayName', 'discordId', 'discordTag', 'publicStream',
    'approved', 'ready', 'partner', 'language',
})
SG_CHANNEL_KEYS = frozenset({'id', 'name', 'slug', 'initials', 'language'})

# The four parallel crew lists on an episode. Wizzrobe does not import crew from
# SG yet — the raw lists are retained in ``SpeedGamingEpisode.payload``.
SG_CREW_ROLES = ('commentators', 'trackers', 'broadcasters', 'helpers')


class SpeedGamingAPIError(Exception):
    """Raised when the SG API errors or returns an unexpected payload."""


def is_mock_speedgaming() -> bool:
    """Return True when MOCK_SPEEDGAMING is enabled (and not in production)."""
    enabled = env_flag('MOCK_SPEEDGAMING')
    if enabled and is_production():
        raise RuntimeError(
            'MOCK_SPEEDGAMING must not be enabled in production: it fakes the '
            'SpeedGaming schedule feed. Unset MOCK_SPEEDGAMING or change ENVIRONMENT.'
        )
    return enabled


def _validate_schedule_payload(payload: Any, event_slug: str) -> List[Dict[str, Any]]:
    """Return the episode list, or raise with SG's own error text.

    A bad event slug is answered with ``200 {"error": "..."}``, so the status
    code alone cannot distinguish "no episodes" from "no such event".
    """
    if isinstance(payload, dict) and payload.get('error'):
        raise SpeedGamingAPIError(
            f"SpeedGaming rejected the schedule request for '{event_slug}': "
            f"{payload['error']}"
        )
    if not isinstance(payload, list):
        raise SpeedGamingAPIError(
            f"SpeedGaming schedule payload was not a list: {type(payload).__name__}"
        )
    return payload


class SpeedGamingClient:
    """Async SpeedGaming schedule client."""

    async def fetch_schedule(
        self,
        event_slug: str,
        start: datetime,
        end: datetime,
        content_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch raw episodes for an event over ``[start, end]`` (UTC datetimes).

        Returns the SG API's list of episode dicts verbatim. Raises
        :class:`SpeedGamingAPIError` on a non-2xx response or non-list payload.
        """
        params = {
            'event': event_slug,
            'from': start.isoformat(),
            'to': end.isoformat(),
        }
        if content_type:
            params['type'] = content_type
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(SCHEDULE_URL, params=params) as resp:
                    text = await resp.text()
                    status = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise SpeedGamingAPIError(f"SpeedGaming didn't answer ({type(e).__name__})") from e
        if status >= 400:
            raise SpeedGamingAPIError(
                f"SpeedGaming schedule request failed ({status}): {text[:200]}"
            )
        import json as _json
        try:
            payload = _json.loads(text)
        except ValueError as e:
            raise SpeedGamingAPIError(
                f"SpeedGaming API returned non-JSON: {text[:200]}"
            ) from e
        return _validate_schedule_payload(payload, event_slug)


class MockSpeedGamingClient(SpeedGamingClient):
    """Scripted client used when MOCK_SPEEDGAMING is enabled.

    Returns deterministic canned episodes so the ETL, read-only guard, and admin
    observability can all be exercised end-to-end without the live SG API. The
    episodes are shaped like the real feed (``id``, ``when``, ``match1.players``
    with ``discordId``/``discordTag``, channel + crew metadata).
    """

    def __init__(self, episodes: Optional[List[Dict[str, Any]]] = None) -> None:
        self._episodes = episodes if episodes is not None else _MOCK_EPISODES

    async def fetch_schedule(
        self,
        event_slug: str,
        start: datetime,
        end: datetime,
        content_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # Return a deep-ish copy so a caller mutating the transform input can't
        # corrupt the canned fixtures across polls.
        import copy
        return [copy.deepcopy(ep) for ep in self._episodes]


def get_speedgaming_client() -> SpeedGamingClient:
    """Return the live or mock SG client per ``MOCK_SPEEDGAMING``."""
    if is_mock_speedgaming():
        return MockSpeedGamingClient()
    return SpeedGamingClient()


# ----------------------------------------------------------------------
# Canned fixtures for MOCK_SPEEDGAMING.
#
# Every episode carries the full ``SG_EPISODE_KEYS`` set with live-accurate
# value conventions (``''`` for unset, ``null`` only for ``match2``, UTC offsets
# on both instants), so the mock exercises the same parsing the real API
# demands. ``test_speedgaming_client.py`` validates them against the manifests.
#
# Coverage, chosen so each fixture reaches a branch the others do not:
#
# 1. Two players — one resolvable by discord id, one only by tag — with crew in
#    all four roles at mixed ``approved``/``ready``, on a restream channel.
# 2. A repeat of player 1 alongside a new opponent; no crew at all.
# 3. Unapproved episode, three players, one with no Discord identity whatsoever
#    (``''``/``''`` → placeholder), matchup still TBD so the only human label is
#    the side's ``note``.
# 4. A doubleheader: ``match2`` populated, which the ETL unions into one Match.
# 5. A slot with no players yet — SG publishes the airtime before the matchup.
# ----------------------------------------------------------------------
_MOCK_EVENT: Dict[str, Any] = {
    'id': 9100,
    'slug': 'mockevent',
    'name': 'Mock Randomizer Tournament',
    'shortName': 'Mock Tournament',
    'game': 'The Legend of Zelda: A Link to the Past',
    'active': True,
    'srtv': False,
    'srl': '',
    'botChannel': '',
}

_MOCK_CHANNELS: List[Dict[str, Any]] = [
    {'id': 7001, 'name': 'SpeedGaming', 'slug': 'speedgaming',
     'initials': 'SG', 'language': 'en'},
]


def _mock_episode(**overrides: Any) -> Dict[str, Any]:
    """Build a fixture with every live key present, then apply ``overrides``.

    Spelling the defaults once is what keeps the fixtures exhaustive: a new key
    on the real feed is added here and every episode gains it.
    """
    episode: Dict[str, Any] = {
        'id': 0,
        'title': '',
        'when': '',
        'whenCountdown': '',
        'length': 90,
        'timezone': '',
        'approved': True,
        'externalRestream': False,
        'event': _MOCK_EVENT,
        'match1': {'id': 0, 'title': '', 'note': '', 'players': []},
        'match2': None,
        'channels': _MOCK_CHANNELS,
        'commentators': [],
        'trackers': [],
        'broadcasters': [],
        'helpers': [],
    }
    episode.update(overrides)
    # SG serves whenCountdown equal to when; no fixture needs to restate it.
    episode['whenCountdown'] = overrides.get('whenCountdown', episode['when'])
    return episode


def _mock_crew(sg_id: int, name: str, **overrides: Any) -> Dict[str, Any]:
    member: Dict[str, Any] = {
        'id': sg_id,
        'displayName': name,
        'discordId': '',
        'discordTag': name.lower(),
        'publicStream': name.lower(),
        'approved': True,
        'ready': False,
        'partner': '',
        'language': 'en',
    }
    member.update(overrides)
    return member


_MOCK_EPISODES: List[Dict[str, Any]] = [
    _mock_episode(
        id=900001,
        when='2026-07-20T18:00:00+00:00',
        match1={
            'id': 910001,
            'title': 'Round 1',
            'note': 'Game 1',
            'players': [
                {'id': 5001, 'displayName': 'PlayerOne', 'discordId': '111111111111111111',
                 'discordTag': 'playerone',
                 'publicStream': 'playerone_tv', 'streamingFrom': 'playerone_tv'},
                {'id': 5002, 'displayName': 'sg_only_user', 'discordId': '',
                 'discordTag': 'sgonlyuser',
                 'publicStream': 'sgonlyuser', 'streamingFrom': 'sgonlyuser'},
            ],
        },
        commentators=[
            _mock_crew(6001, 'CasterOne', ready=True),
            _mock_crew(6002, 'CasterTwo', approved=False),
        ],
        trackers=[_mock_crew(6003, 'TrackerOne', discordId='333333333333333333')],
        broadcasters=[_mock_crew(6004, 'RestreamerOne')],
        helpers=[_mock_crew(6005, 'HelperOne', approved=False, publicStream='')],
    ),
    _mock_episode(
        id=900002,
        when='2026-07-21T20:30:00+00:00',
        match1={
            'id': 910002,
            'title': 'Round 2',
            'note': '',
            'players': [
                {'id': 5003, 'displayName': 'PlayerTwo', 'discordId': '222222222222222222',
                 'discordTag': 'playertwo',
                 'publicStream': 'playertwo', 'streamingFrom': 'playertwo'},
                {'id': 5001, 'displayName': 'PlayerOne', 'discordId': '111111111111111111',
                 'discordTag': 'playerone',
                 'publicStream': 'playerone_tv', 'streamingFrom': 'playerone_tv'},
            ],
        },
    ),
    _mock_episode(
        # Three-player match, still unapproved, crew signup pending. The matchup
        # is TBD — SG serves '' — so ``note`` is the only label the ETL can use.
        id=900003,
        when='2026-07-22T19:00:00+00:00',
        length=120,
        approved=False,
        match1={
            'id': 910003,
            'title': '',
            'note': 'Swiss Round 5',
            'players': [
                {'id': 5004, 'displayName': 'PlayerThree', 'discordId': '',
                 'discordTag': '', 'publicStream': '', 'streamingFrom': ''},
                {'id': 5005, 'displayName': 'PlayerFour', 'discordId': '',
                 'discordTag': 'playerfour',
                 'publicStream': 'playerfour', 'streamingFrom': 'playerfour'},
                {'id': 5006, 'displayName': 'PlayerFive', 'discordId': '',
                 'discordTag': 'playerfive',
                 'publicStream': 'playerfive', 'streamingFrom': 'playerfive'},
            ],
        },
        channels=[],
        commentators=[_mock_crew(6006, 'CasterThree', approved=False)],
    ),
    _mock_episode(
        # Doubleheader: two SG matches share one airtime slot. The ETL unions
        # both sides' players into a single Wizzrobe Match.
        id=900004,
        when='2026-07-23T23:00:00+00:00',
        length=130,
        externalRestream=True,
        match1={
            'id': 910004,
            'title': '',
            'note': 'Showcase Race 5 & 6',
            'players': [
                {'id': 5007, 'displayName': 'PlayerSix', 'discordId': '',
                 'discordTag': 'playersix',
                 'publicStream': '', 'streamingFrom': 'PlayerSix'},
                {'id': 5008, 'displayName': 'PlayerSeven', 'discordId': '',
                 'discordTag': 'playerseven',
                 'publicStream': '', 'streamingFrom': 'PlayerSeven'},
            ],
        },
        match2={
            'id': 910005,
            'title': '',
            'note': 'Showcase Race 5 & 6',
            'players': [
                {'id': 5009, 'displayName': 'PlayerEight', 'discordId': '',
                 'discordTag': 'playereight',
                 'publicStream': '', 'streamingFrom': 'PlayerEight'},
            ],
        },
    ),
    _mock_episode(
        # Airtime published before the matchup is known: a real, approved
        # episode whose only content is a title.
        id=900005,
        when='2026-07-24T01:00:00+00:00',
        length=240,
        match1={'id': 910006, 'title': 'Blitz', 'note': '', 'players': []},
    ),
]
