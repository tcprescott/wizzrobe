"""Bulk behaviour of "Suggest a time": many matches asking in a row.

A small SGL-shaped event (two days, a 12-player room) booked one suggestion at a
time through the real service, the way a desk rush or an advance-booking wave
would. Single suggestions all look reasonable; the failures this guards against
(double-booked players, a room past its cap, group matches scattered to the last
night) only show up across the whole sequence.
"""

import json
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise

import pytest

import application.services.match.match_suggestion_service as mss
from application.services.match.match_suggestion_service import MatchSuggestionService
from application.utils.timezone import EASTERN_TZ
from models import Match, MatchPlayers, Tournament, User
from tests.factories import set_setting

DAYS = [date(2026, 10, 22), date(2026, 10, 23)]
CAP = 12


def _local(d: date, h: int, mi: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, mi, tzinfo=EASTERN_TZ)


@pytest.fixture
async def event(db, monkeypatch):
    await set_setting(name='event_start_date', value=DAYS[0].isoformat())
    await set_setting(name='event_end_date', value=DAYS[-1].isoformat())
    await set_setting(
        name='tournament_hours_by_date',
        value=json.dumps({d.isoformat(): {'open': '10:00', 'close': '22:00'} for d in DAYS}),
    )
    await set_setting(name='max_concurrent_players', value=str(CAP))
    clock = {'now': _local(date(2026, 10, 20), 12)}
    monkeypatch.setattr(mss, 'now_local', lambda tz=None: clock['now'].astimezone(tz or EASTERN_TZ))
    short = await Tournament.create(name='Short', average_match_duration=60)
    long = await Tournament.create(name='Long', average_match_duration=120)
    players = [await User.create(username=f'p{i}', discord_id=500 + i) for i in range(16)]
    return clock, [short, long], players


async def _book(tournament, pair, exclude_match_id=None) -> Match:
    when = await MatchSuggestionService().suggest_match_time(
        tournament.id, [p.id for p in pair], exclude_match_id=exclude_match_id,
    )
    match = await Match.create(tournament=tournament, scheduled_at=when)
    for p in pair:
        await MatchPlayers.create(match=match, user=p)
    return match


async def _windows():
    out = []
    for m in await Match.all().prefetch_related('tournament', 'players'):
        start = m.scheduled_at.astimezone(EASTERN_TZ)
        end = start + timedelta(minutes=m.tournament.average_match_duration)
        out.append((start, end, {mp.user_id for mp in m.players}))
    return out


def _peak(windows) -> int:
    return max(
        sum(len(p) for s, e, p in windows if s <= t < e)
        for t, _, _ in windows
    )


def _clashes(windows) -> int:
    by_player = defaultdict(list)
    for s, e, players in windows:
        for p in players:
            by_player[p].append((s, e))
    clashes = 0
    for spans in by_player.values():
        spans.sort()
        clashes += sum(1 for a, b in pairwise(spans) if b[0] < a[1])
    return clashes


def _round_robin(players):
    return [(a, b) for i, a in enumerate(players) for b in players[i + 1:]]


async def test_advance_booking_wave_fills_early_without_clashes(event):
    clock, (short, long), players = event
    rng = random.Random(3)
    requests = []
    for group, tournament in ((players[:4], short), (players[4:8], short), (players[8:12], long)):
        requests += [(tournament, pair) for pair in _round_robin(group)]
    # Players 0 and 8 are in two tournaments' groups, as at SGL.
    requests += [(long, pair) for pair in _round_robin([players[0], players[12], players[13], players[14]])]
    rng.shuffle(requests)

    for tournament, pair in requests:
        await _book(tournament, pair)

    windows = await _windows()
    assert _clashes(windows) == 0
    assert _peak(windows) <= CAP
    # Group play finishes on the first day instead of drifting to the last night.
    assert all(s.date() == DAYS[0] for s, _, _ in windows)


async def test_desk_rush_spills_past_the_next_four_hours_instead_of_overfilling(event):
    clock, (short, long), players = event
    clock['now'] = _local(DAYS[0], 10)
    pairs = [(players[i], players[i + 1]) for i in range(0, 16, 2)]

    for _ in range(3):
        for pair in pairs:
            await _book(long, pair)

    windows = await _windows()
    assert len(windows) == 24
    assert _clashes(windows) == 0
    assert _peak(windows) <= CAP
    assert max(s for s, _, _ in windows) >= clock['now'] + timedelta(hours=4)


async def test_rescheduling_does_not_count_the_match_against_itself(event):
    clock, (short, _), players = event
    pair = (players[0], players[1])
    match = await _book(short, pair)
    first = match.scheduled_at

    again = await MatchSuggestionService().suggest_match_time(
        short.id, [p.id for p in pair], exclude_match_id=match.id,
    )
    assert again == first

    without_exclusion = await MatchSuggestionService().suggest_match_time(
        short.id, [p.id for p in pair],
    )
    assert without_exclusion >= first + timedelta(minutes=60)
