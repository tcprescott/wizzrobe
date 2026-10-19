"""sglive.speedgaming.org prize-pool transport client.

SpeedGaming Live's own site sorts Matcherino contributions into per-game prize
pools. ``GET /data/`` serves them as JSON::

    {"tournaments": [{"slug": "wwr", "short_name": "Wind Waker Randomizer",
                      "long_name": "", "donations_total": "$1,701.00",
                      "donations": [{"name": "...", "total": "$500.00"}], ...}]}

This module returns raw entries only; matching them to tournaments is
:class:`~application.services.PayoutService`'s job. The game's slug is the
stable key: names on sglive differ from the names communities give their
tournaments. A payload with no games raises :class:`SgliveAPIError` rather than
returning an empty list, because an empty list would read as "every pool is
gone" and the caller couldn't tell an outage from a real result.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, List

import aiohttp

SGLIVE_URL = 'https://sglive.speedgaming.org/'
DATA_URL = f'{SGLIVE_URL}data/'

REQUEST_TIMEOUT_SECONDS = 20


class SgliveAPIError(Exception):
    """sglive didn't answer, or served a payload this module doesn't recognise."""


@dataclass(frozen=True)
class SgliveGame:
    """One game on sglive, with its donations total."""

    slug: str
    name: str
    total: Decimal


def parse_money(text: Any) -> Decimal:
    """``'$1,701.00'`` -> ``Decimal('1701.00')``."""
    cleaned = str(text).strip().lstrip('$').replace(',', '').strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as e:
        raise SgliveAPIError(f'sglive sent {text!r} where a dollar amount should be') from e
    if value < 0:
        raise SgliveAPIError(f'sglive sent a negative pool ({text!r})')
    return value.quantize(Decimal('0.01'))


def parse_games(payload: Any) -> List[SgliveGame]:
    """Every game in a ``/data/`` payload, in the order sglive lists them."""
    rows = payload.get('tournaments') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise SgliveAPIError('sglive sent no tournaments; the data feed may have changed')
    games: dict[str, SgliveGame] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get('slug') or 'donations_total' not in row:
            raise SgliveAPIError('sglive sent a tournament without a slug or total')
        slug = str(row['slug']).strip().lower()
        name = str(row.get('short_name') or row.get('long_name') or slug)
        games.setdefault(slug, SgliveGame(slug=slug, name=name, total=parse_money(row['donations_total'])))
    return list(games.values())


async def fetch_games(url: str = DATA_URL) -> List[SgliveGame]:
    """Read and parse sglive's data feed, or raise :class:`SgliveAPIError`."""
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={'User-Agent': 'Wizzrobe prize pool sync'}) as resp:
                if resp.status != 200:
                    raise SgliveAPIError(f'sglive answered HTTP {resp.status}')
                body = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise SgliveAPIError(f"sglive didn't answer ({type(e).__name__})") from e
    try:
        payload = json.loads(body)
    except ValueError as e:
        raise SgliveAPIError('sglive sent something other than JSON') from e
    return parse_games(payload)
