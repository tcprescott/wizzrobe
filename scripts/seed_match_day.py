"""One realistic match day, for the views that only make sense when it is busy.

**These rows are not state fixtures.** Every other seed module earns each row by
covering a distinct state; this one deliberately does the opposite — fifteen
ordinary bracket matches across the three stages, back to back through day one of
the event window, because a real event runs dozens of matches a day and the
schedule, the stage-utilisation report and the operations reports all render as
though nothing is happening when the database holds nine matches total.

Kept in its own module so the distinction stays visible: if this file is deleted,
no state goes uncovered — only the density does. Density beyond one day belongs
in ``scripts/loadtest``, not here (docs/reference/dev-seed.md).

The states follow the clock rather than being assigned: a slot that has already
passed is finished and confirmed, the slot containing *now* is racing, and the
rest are scheduled. That way the day reads correctly whenever the seed is run.
"""

from datetime import date, datetime, timedelta

from application.utils.timezone import parse_local_datetime
from models import Match, MatchPlayers, Stage, Tenant, Tournament, User

_SLOT_TIMES = ("10:00", "12:00", "14:00", "16:00", "18:00")

_ROUND_LABELS = (
    "Winners Round 1",
    "Winners Round 2",
    "Losers Round 1",
    "Winners Quarterfinal",
    "Losers Quarterfinal",
)


async def seed_match_day_for_tenant(
    tenant: Tenant,
    tournament: Tournament,
    racers: list[User],
    stages: list[Stage],
    day: date,
    now: datetime,
) -> None:
    """Fill ``day`` with back-to-back matches across ``stages``. Idempotent."""
    if not stages or len(racers) < 6:
        return

    day_str = day.isoformat()
    created = 0
    for slot_index, (start_hhmm, round_label) in enumerate(zip(_SLOT_TIMES, _ROUND_LABELS, strict=False)):
        starts_at = parse_local_datetime(day_str, start_hhmm)
        # Taken in order so nobody is booked onto two stages at once.
        offset = (slot_index * len(stages) * 2) % len(racers)
        for stage_index, stage in enumerate(stages):
            title = f"{round_label} — Match {stage_index + 1}"
            match, was_created = await Match.get_or_create(
                title=title, tournament=tournament, tenant=tenant,
                defaults={
                    "scheduled_at": starts_at,
                    "stage": stage,
                    "is_stream_candidate": True,
                },
            )
            if not was_created:
                continue
            created += 1
            pair = [
                racers[(offset + stage_index * 2 + n) % len(racers)]
                for n in (0, 1)
            ]
            _apply_clock_state(match, starts_at, now)
            await match.save()
            for rank, racer in enumerate(pair, 1):
                await MatchPlayers.get_or_create(
                    match=match, user=racer, tenant=tenant,
                    defaults={
                        "finish_rank": rank if match.finished_at is not None else None,
                    },
                )

    print(f"    [{tenant.slug}] match day ok ({created} matches across {len(stages)} stages)")


def _apply_clock_state(match: Match, starts_at: datetime, now: datetime) -> None:
    """Stamp the lifecycle timestamps a slot at ``starts_at`` would have by ``now``.

    Slots that have finished are confirmed, the one under way is running, and
    anything later is merely scheduled — so the day is coherent whatever time the
    seed happens to run at.
    """
    ends_at = starts_at + timedelta(minutes=95)
    if now >= ends_at:
        match.seated_at = starts_at - timedelta(minutes=10)
        match.started_at = starts_at
        match.finished_at = ends_at
        match.confirmed_at = ends_at + timedelta(minutes=5)
    elif now >= starts_at:
        match.seated_at = starts_at - timedelta(minutes=10)
        match.started_at = starts_at
    elif now >= starts_at - timedelta(minutes=15):
        match.seated_at = now
