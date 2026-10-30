"""Dev rooms the racetime room bot is in the middle of: a held result per hold
reason, and a restream whose seed waits on its broadcaster's !ready.

Called from ``seed_online`` with its online tournament, alttpr bot and players.
"""

from datetime import timedelta

from application.utils.timezone import now_local
from models import Match, MatchPlayers, RaceRoomStatus, RacetimeBot, RacetimeRoom, Tenant, Tournament, User


async def seed_room_bot_rooms(
    tenant: Tenant, tournament: Tournament, bot: RacetimeBot, players: list[User],
) -> None:
    await _seed_held_race(tenant, tournament, bot, players)
    await _seed_broadcaster_wait(tenant, tournament, bot, players)


async def _seed_held_race(
    tenant: Tenant, tournament: Tournament, bot: RacetimeBot, players: list[User],
) -> None:
    """One race per reason the room bot holds a result for staff, each waiting in
    the Needs you inbox under "Racetime results held" with its Check result
    dialog. No ranks on these matches: a held result isn't recorded yet."""
    from models import RaceRoomHoldReason

    a, b = players[0], players[1]

    def done(user: User, place: int, ms: int) -> dict:
        return {"user_id": user.racetime_user_id, "name": user.preferred_name,
                "status": "done", "place": place, "finish_ms": ms}

    specs = [
        (RaceRoomHoldReason.CLOSE_FINISH, "Close Finish", 20, (a, b),
         f"{a.preferred_name} and {b.preferred_name} finished 0.4s apart.",
         [done(a, 1, 5_702_100), done(b, 2, 5_702_500)], []),
        (RaceRoomHoldReason.FPA, "FPA Called", 50, (b, a),
         f"{a.preferred_name} called a fair play pause at 0:41:12.",
         [done(b, 1, 5_480_000), done(a, 2, 5_610_000)],
         [{"racetime_user_id": a.racetime_user_id, "name": a.preferred_name,
           "at": (now_local() - timedelta(hours=1)).isoformat(), "race_seconds": 2472,
           "note": "stream dropped"}]),
        (RaceRoomHoldReason.FORFEIT, "Forfeit", 80, (a, b),
         f"{b.preferred_name} forfeited.",
         [done(a, 1, 5_900_000),
          {"user_id": b.racetime_user_id, "name": b.preferred_name, "status": "dnf",
           "place": None, "finish_ms": None}], []),
        (RaceRoomHoldReason.UNMATCHED, "Runner Not Matched", 110, (a, players[2]),
         f"{players[2].preferred_name} has no racetime account linked.",
         [done(a, 1, 5_650_000),
          {"user_id": "zZ9unknown", "name": "MysteryRunner", "status": "done",
           "place": 2, "finish_ms": 5_700_000}], []),
    ]
    for reason, label, minutes_ago, racers, detail, entrants, fpa_calls in specs:
        finished_at = now_local() - timedelta(minutes=minutes_ago)
        match, created = await Match.get_or_create(
            title=f"Online Race — {label}", tournament=tournament, tenant=tenant,
            defaults={
                "scheduled_at": finished_at - timedelta(hours=1, minutes=40),
                "started_at": finished_at - timedelta(hours=1, minutes=35),
                "finished_at": finished_at,
            },
        )
        if created:
            for player in racers:
                await MatchPlayers.get_or_create(match=match, user=player, tenant=tenant)
        await RacetimeRoom.update_or_create(
            slug=f"alttpr/dev-room-held-{reason.value.replace('_', '-')}-{tenant.slug}",
            defaults={
                "tenant": tenant,
                "bot": bot,
                "category": "alttpr",
                "room_name": f"{label} — Bracket",
                "status": RaceRoomStatus.FINISHED,
                "match": match,
                "opened_at": match.scheduled_at - timedelta(minutes=15),
                "greeted_at": match.scheduled_at - timedelta(minutes=15),
                "seed_posted_at": match.scheduled_at - timedelta(minutes=10),
                "held_reason": reason,
                "held_detail": detail,
                "held_entrants": entrants,
                "fpa_calls": fpa_calls,
            },
        )


async def _seed_broadcaster_wait(
    tenant: Tenant, tournament: Tournament, bot: RacetimeBot, players: list[User],
) -> None:
    """A restreamed race past its start whose seed the room bot is holding for
    the broadcaster's !ready, with the start-time nudge already sent. Needs the
    event's Broadcasters switch on, as ``seed_crew`` does for its event."""
    from models import Broadcaster, Event

    start = now_local() - timedelta(minutes=2)
    match, created = await Match.get_or_create(
        title="Online Race — Waiting on Broadcaster", tournament=tournament, tenant=tenant,
        defaults={"scheduled_at": start},
    )
    if created:
        for player in players[:2]:
            await MatchPlayers.get_or_create(match=match, user=player, tenant=tenant)
    event = await Event.get(id=match.event_id)  # type: ignore[attr-defined]
    if (event.config or {}).get("broadcasters") != "true":
        event.config = {**(event.config or {}), "broadcasters": "true"}
        await event.save(update_fields=["config"])
    await Broadcaster.get_or_create(
        match=match, user=players[3], tenant=tenant, defaults={"approved": True},
    )
    await RacetimeRoom.update_or_create(
        slug=f"alttpr/dev-room-awaiting-broadcaster-{tenant.slug}",
        defaults={
            "tenant": tenant,
            "bot": bot,
            "category": "alttpr",
            "room_name": "Waiting on Broadcaster — Bracket",
            "status": RaceRoomStatus.OPEN,
            "match": match,
            "opened_at": start - timedelta(minutes=15),
            "greeted_at": start - timedelta(minutes=15),
            "broadcaster_nudged_at": start,
        },
    )
