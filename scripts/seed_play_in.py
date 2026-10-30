"""Dev-seed fixtures for a **group play-in race** — the one shape that is not 1v1.

Split out of ``seed_dev.py`` like the other per-domain modules: called from
inside the caller's ``tenant_scope``, idempotent, tenant-stamped.

Head-to-head is what this app runs almost all of the time, so a whole tournament
of multi-racer heats would misrepresent it. What does happen is a staff-run
**play-in**: ten or more racers in one start list, whose finish order seeds the
bracket. It therefore lives *inside the tournament whose bracket it feeds*, which
also makes it the fixture for a tournament holding matches of two different
roster sizes — ``players_per_match`` is a single number, so production produces
exactly this mismatch too.

Three things nothing else in the seed covers:

* **A roster longer than two.** Ten names is where a two-player assumption shows
  — the roster editor, the "A vs B" labels, the crew DM copy, the Discord embed.
* **Non-finishers beside finishers.** Eight placings and two forfeits in one
  match: a forfeit keeps its seat with neither a rank nor a finish time, which is
  how every DNF is recorded.
* **A start list still filling.** The second group is scheduled with a partial
  roster, the state a play-in sits in until signups close.
"""

from datetime import datetime, timedelta

from models import Match, MatchPlayers, Tenant, Tournament, TournamentPlayers, User

# Two short of the start list on purpose: the last two forfeited.
_GROUP_A_TIMES = (5102, 5240, 5333, 5418, 5502, 5677, 5810, 6044)


async def seed_play_in_for_tenant(
    tenant: Tenant,
    tournament: Tournament,
    staff: User,
    racers: list[User],
    now: datetime,
) -> None:
    """Seed the two play-in groups into ``tournament``. Idempotent.

    ``racers`` is the full pool (the four named players plus the play-in
    fillers); the groups take the first ten and the last eight of it.
    """
    for racer in racers:
        await TournamentPlayers.get_or_create(
            tournament=tournament, user=racer, tenant=tenant,
        )

    group_a = await _make_group(
        tenant, tournament, "Play-In Race — Group A", now - timedelta(hours=6),
    )
    if group_a is not None:
        group_a.started_at = group_a.scheduled_at
        group_a.finished_at = group_a.scheduled_at + timedelta(minutes=105)
        group_a.confirmed_at = group_a.finished_at + timedelta(minutes=10)
        await group_a.save()
        for index, racer in enumerate(racers[:10]):
            finished = index < len(_GROUP_A_TIMES)
            await MatchPlayers.get_or_create(
                match=group_a, user=racer, tenant=tenant,
                defaults={
                    "finish_rank": index + 1 if finished else None,
                    "finish_time": _GROUP_A_TIMES[index] if finished else None,
                },
            )

    group_b = await _make_group(
        tenant, tournament, "Play-In Race — Group B", now + timedelta(hours=5),
    )
    if group_b is not None:
        group_b.is_stream_candidate = True
        await group_b.save()
        for racer in racers[4:]:
            await MatchPlayers.get_or_create(
                match=group_b, user=racer, tenant=tenant,
            )

    print(
        f"    [{tenant.slug}] play-in races ok "
        f"(10-racer group A with two forfeits; group B still filling)"
    )


async def _make_group(
    tenant: Tenant, tournament: Tournament, title: str, scheduled_at: datetime,
) -> Match | None:
    """Create one play-in group, or return ``None`` when it already exists.

    Returning ``None`` on a re-run is what keeps the caller's roster and
    timestamp writes create-only, the same shape the other seed modules use.
    """
    match, created = await Match.get_or_create(
        title=title, tournament=tournament, tenant=tenant,
        defaults={"scheduled_at": scheduled_at, "is_stream_candidate": False},
    )
    return match if created else None
