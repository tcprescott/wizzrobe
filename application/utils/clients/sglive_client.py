"""sglive.speedgaming.org prize-pool transport client.

SpeedGaming Live's own site sorts Matcherino contributions into per-game prize
pools and publishes them as a leaderboard on its homepage. It has no API or JSON
feed, so this module reads that page's HTML. It returns raw entries only;
matching them to tournaments is :class:`~application.services.PayoutService`'s
job.

The leaderboard is two blocks (online, then on-site), each a set of
``<table class="sub-leaderboard">`` with one row per game::

    <tr><td>#1</td><td><a href="/wwr/" class="leaderboard-link">Wind Waker
    Randomizer</a></td><td>$1,701.00</td></tr>

The game's slug (``wwr``) is the stable key: names on sglive differ from the
names communities give their tournaments, and slugs don't change once a game is
on the board. A page that yields no rows raises :class:`SgliveAPIError` rather
than returning an empty list, because an empty list would read as "every pool
is gone" and the caller would have nothing to tell a layout change apart from
a real result.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import List, Optional

import aiohttp

SGLIVE_URL = 'https://sglive.speedgaming.org/'

REQUEST_TIMEOUT_SECONDS = 20

_SLUG_RE = re.compile(r'^/([a-z0-9][a-z0-9_-]*)/?$')
_ONSITE_MARKER = 'onsite-header'


class SgliveAPIError(Exception):
    """sglive didn't answer, or served a page this module doesn't recognise."""


@dataclass(frozen=True)
class SgliveGame:
    """One game on the sglive leaderboard."""

    slug: str
    name: str
    total: Decimal
    onsite: bool


def parse_money(text: str) -> Decimal:
    """``'$1,701.00'`` -> ``Decimal('1701.00')``."""
    cleaned = text.strip().lstrip('$').replace(',', '').strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as e:
        raise SgliveAPIError(f'sglive showed {text!r} where a dollar amount should be') from e
    if value < 0:
        raise SgliveAPIError(f'sglive showed a negative pool ({text!r})')
    return value.quantize(Decimal('0.01'))


class _LeaderboardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[tuple[str, str, str, bool]] = []
        self._onsite = False
        self._in_row = False
        self._cells: List[str] = []
        self._cell: Optional[List[str]] = None
        self._slug: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr = dict(attrs)
        if tag == 'img' and _ONSITE_MARKER in (attr.get('src') or ''):
            self._onsite = True
        elif tag == 'tr':
            self._in_row, self._cells, self._slug = True, [], None
        elif tag == 'td' and self._in_row:
            self._cell = []
        elif tag == 'a' and self._in_row and 'leaderboard-link' in (attr.get('class') or ''):
            match = _SLUG_RE.match(attr.get('href') or '')
            if match:
                self._slug = match.group(1)

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == 'td' and self._cell is not None:
            self._cells.append(''.join(self._cell).strip())
            self._cell = None
        elif tag == 'tr' and self._in_row:
            self._in_row = False
            if self._slug and len(self._cells) == 3:
                self.rows.append((self._slug, self._cells[1], self._cells[2], self._onsite))


def parse_leaderboard(html: str) -> List[SgliveGame]:
    """Every game on the sglive homepage, in the order the page lists them."""
    parser = _LeaderboardParser()
    parser.feed(html)
    parser.close()
    if not parser.rows:
        raise SgliveAPIError('sglive served no leaderboard rows; the page layout may have changed')
    games: dict[str, SgliveGame] = {}
    for slug, name, amount, onsite in parser.rows:
        games.setdefault(slug, SgliveGame(slug=slug, name=name, total=parse_money(amount), onsite=onsite))
    return list(games.values())


async def fetch_leaderboard(url: str = SGLIVE_URL) -> List[SgliveGame]:
    """Read and parse the sglive leaderboard, or raise :class:`SgliveAPIError`."""
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={'User-Agent': 'Wizzrobe prize pool sync'}) as resp:
                if resp.status != 200:
                    raise SgliveAPIError(f'sglive answered HTTP {resp.status}')
                html = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise SgliveAPIError(f"sglive didn't answer ({type(e).__name__})") from e
    return parse_leaderboard(html)
