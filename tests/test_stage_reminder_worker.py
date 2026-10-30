"""The pre-match stage reminder loop.

Drives ``_tick`` against the real in-memory ORM: which matches it picks up, the
once-only stamp, and the per-tournament lead it re-checks each match against
after the wide cross-tenant scan.
"""

from datetime import datetime, timedelta, timezone

import pytest

import application.services.match.stage_reminder as worker
from models import Match, MatchPlayers, Stage, Tournament
from tests.factories import make_user

UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_match(
    *, minutes_ahead: float, stage: Stage | None, lead: int = 30, finished: bool = False,
) -> Match:
    tournament = await Tournament.create(
        name=f'T{minutes_ahead}-{lead}', stage_reminder_minutes=lead,
    )
    scheduled_at = _now() + timedelta(minutes=minutes_ahead)
    match = await Match.create(
        title='Game', tournament=tournament, stage=stage, scheduled_at=scheduled_at,
        finished_at=scheduled_at if finished else None,
    )
    player = await make_user(int(abs(minutes_ahead) * 100) + lead + (1 if finished else 0))
    await MatchPlayers.create(match=match, user=player)
    return match


@pytest.fixture
async def stage(db) -> Stage:
    return await Stage.create(name='Kraid')


async def test_fires_once_inside_the_window(stage, stub_discord_queue):
    match = await _make_match(minutes_ahead=20, stage=stage)

    await worker._tick()

    assert len(stub_discord_queue) == 1
    assert (await Match.get(id=match.id)).stage_reminder_sent_at is not None


async def test_a_second_tick_sends_nothing(stage, stub_discord_queue):
    await _make_match(minutes_ahead=20, stage=stage)
    await worker._tick()
    for coro in stub_discord_queue:
        coro.close()
    stub_discord_queue.clear()

    await worker._tick()

    assert stub_discord_queue == []


async def test_a_match_with_no_stage_is_left_alone(stage, stub_discord_queue):
    match = await _make_match(minutes_ahead=20, stage=None)

    await worker._tick()

    assert stub_discord_queue == []
    assert (await Match.get(id=match.id)).stage_reminder_sent_at is None


async def test_a_finished_match_is_left_alone(stage, stub_discord_queue):
    match = await _make_match(minutes_ahead=20, stage=stage, finished=True)

    await worker._tick()

    assert stub_discord_queue == []
    assert (await Match.get(id=match.id)).stage_reminder_sent_at is None


async def test_a_ninety_minute_lead_reminds_at_ninety_not_thirty(stage, stub_discord_queue):
    """The per-tournament re-check, which is the whole point of the column."""
    early = await _make_match(minutes_ahead=60, stage=stage, lead=90)
    default = await _make_match(minutes_ahead=61, stage=stage, lead=30)

    await worker._tick()

    assert len(stub_discord_queue) == 1
    assert (await Match.get(id=early.id)).stage_reminder_sent_at is not None
    assert (await Match.get(id=default.id)).stage_reminder_sent_at is None


async def test_a_zero_lead_sends_no_reminder(stage, stub_discord_queue):
    match = await _make_match(minutes_ahead=5, stage=stage, lead=0)

    await worker._tick()

    assert stub_discord_queue == []
    assert (await Match.get(id=match.id)).stage_reminder_sent_at is None


async def test_a_lead_beyond_the_scan_window_warns_and_still_reminds(
    stage, stub_discord_queue, caplog,
):
    match = await _make_match(
        minutes_ahead=30, stage=stage, lead=worker.MAX_LEAD_MINUTES + 60,
    )

    with caplog.at_level('WARNING'):
        await worker._tick()

    assert any('exceeds the' in record.message for record in caplog.records)
    assert len(stub_discord_queue) == 1
    assert (await Match.get(id=match.id)).stage_reminder_sent_at is not None


async def test_a_match_beyond_the_scan_window_is_not_scanned(stage, stub_discord_queue):
    match = await _make_match(
        minutes_ahead=worker.MAX_LEAD_MINUTES + 120, stage=stage,
        lead=worker.MAX_LEAD_MINUTES + 60,
    )

    await worker._tick()

    assert stub_discord_queue == []
    assert (await Match.get(id=match.id)).stage_reminder_sent_at is None


def test_tick_seconds_is_positive():
    assert worker.TICK_SECONDS > 0
