"""Auto-open worker for scheduled racetime rooms (PR 6).

A lightweight background loop (modeled on ``volunteer_reminder``) that opens a
racetime room ahead of each eligible scheduled match. Opt-in per tournament
(``racetime_auto_create_rooms`` + ``room_open_minutes_before``); a match is
eligible only when **all** its entrants have a linked racetime identity. The
worker is idempotent (one ``RacetimeRoom`` per match — creation returns the
existing room), tenant-safe (each match's work runs inside ``tenant_scope``), and
crash-resilient (a per-match failure is logged and retried next tick).

Rescheduling a match with an already-open room keeps the room — the worker never
creates a second one, and it does not touch an existing room's time.
"""

import logging
from datetime import datetime, timedelta, timezone

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 60

# Wide cross-tenant scan window; each match is re-checked against ITS tournament's
# room_open_minutes_before lead before a room is opened. A small grace on the
# lower bound lets a brief worker outage still catch a just-passed start.
MAX_LEAD_MINUTES = 7 * 24 * 60
GRACE_MINUTES = 15
# A best-of-N game held behind a long-running earlier game can slip well past
# GRACE_MINUTES before it may open, so series games get their own wider floor.
SERIES_GRACE_MINUTES = 12 * 60


async def _tick() -> None:
    from application.repositories import RacetimeRoomRepository
    from application.services.bracket_service import BracketService
    from application.services.race_room_service import RaceRoomService
    from application.services.user_service import UserService

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=GRACE_MINUTES)
    window_end = now + timedelta(minutes=MAX_LEAD_MINUTES)

    repo = RacetimeRoomRepository()
    bracket_service = BracketService()
    candidates = await repo.matches_due_for_auto_open(window_start, window_end)

    # Backstop: a series game deferred behind a long-running previous game can
    # slip past this scan's 15-minute lower bound. The push at game-end normally
    # opens it right away; this catches the case where the process died in
    # between. Everything downstream (the hold, then auto_open_if_eligible with
    # its lead-window guard) applies unchanged, so it can never open anything the
    # normal path wouldn't.
    seen = {m.id for m in candidates}
    for match in await bracket_service.series_matches_due(
        now - timedelta(minutes=SERIES_GRACE_MINUTES), window_end,
    ):
        if match.id not in seen:
            seen.add(match.id)
            candidates.append(match)

    if not candidates:
        return

    # A later game of a best-of-N series waits for the earlier one to end —
    # opening game 2's room mid-race would put the players in two rooms at once.
    # Batched (two queries for the whole candidate set, not one per match) and
    # applied here rather than inside auto_open_if_eligible so the push at
    # game-end can reuse that method without paying for the scan.
    #
    # Deliberately NOT gated on FeatureFlag.BRACKETS: turning the flag off
    # mid-tournament must not suddenly open every held room at once. The hold is
    # data-driven and self-limiting — no game rows means an empty set.
    held = await bracket_service.held_match_ids([m.id for m in candidates])
    if held:
        candidates = [m for m in candidates if m.id not in held]
        if not candidates:
            return

    service = RaceRoomService()
    system_user = await UserService().get_system_user()

    async def _open(match) -> None:
        room = await service.auto_open_if_eligible(match, now=now, actor=system_user)
        if room is not None:
            logger.info('auto-opened racetime room for match %s', match.id)

    await for_each_tenant_scoped(
        candidates,
        _open,
        tenant_id_of=lambda match: match.tenant_id,
        event_id_of=lambda match: match.event_id,
        logger=logger,
        describe=lambda match: f'match {getattr(match, "id", None)}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'race room auto-open', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
