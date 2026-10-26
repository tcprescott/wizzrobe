"""Dev seed for the second on-site (non-racetime) tournament — "Wizzrobe Cup".

Split out of ``seed_dev.py`` for length, the way the volunteer and bracket
fixtures already are.

A venue event distinct from the general-purpose "Wizzrobe Dev Tournament": it
carries its own per-tournament "tournament days" override, and it walks the
proctor's board through the workflow in order — one match per step, plus the two
states the admin's half of the loop needs (a recorded result awaiting review, and
a finished match with no winner recorded). Deliberately never racetime-enabled:
a racetime bot hides check-in, stations, start and finish, which is exactly what
this fixture exists to exercise.
"""

from datetime import date, datetime, timedelta

from models import (
    CommentaryInvite,
    CommentaryInviteStatus,
    Commentator,
    Match,
    MatchPlayers,
    Stage,
    Tenant,
    Tournament,
    TournamentPlayers,
    User,
)
from scripts.seed_support import backfill

#: What a venue event fills in besides its name. The durations are the pair the
#: schedule's suggestion engine and the reports' expected-average column read
#: (both fall back to a hard-coded 90 minutes when NULL), and a Cup match is
#: shorter than a bracket race — so the two on-site fixtures no longer look
#: identical to anything that reasons about match length.
_ONSITE_META = {
    "tournament_format": "Swiss rounds into a single-elimination top 8",
    "rules_url": "https://example.invalid/wizzrobe/cup/rules",
    "average_match_duration": 75,
    "max_match_duration": 120,
}


async def _invite(tenant, match, inviter, invitee, status, responded_at) -> None:
    """One commentary invite in ``status``, unless the pair already has one in it."""
    keys = dict(match=match, inviter=inviter, invitee=invitee, tenant=tenant, status=status)
    if not await CommentaryInvite.filter(**keys).exists():
        await CommentaryInvite.create(**keys, responded_at=responded_at)


async def seed_onsite_for_tenant(
    tenant: Tenant,
    staff: User,
    players: list[User],
    today: date,
    now: datetime,
    stage1: Stage,
    stage3: Stage,
) -> Tournament:
    """Seed the on-site tournament and its matches. Idempotent."""
    onsite, _ = await Tournament.get_or_create(
        name="Wizzrobe Cup", tenant=tenant,
        defaults={
            "description": "On-site fixture — no racetime.gg integration.",
            # **Closed** signups, the state the Tournaments tab has to render
            # without a button: the dev tournament covers open, and the
            # not-yet-open case is seeded on the archived one below.
            "signups_open_at": now - timedelta(days=60),
            "signups_close_at": now - timedelta(days=7),
            "seed_generator": "alttpr",
            "is_active": True,
            "players_per_match": 2,
            # A commentary-only community: this tournament runs restreams with
            # two commentators and no tracker. Without the requirement set to 0
            # every streamed match here would report a permanent coverage gap —
            # which is exactly the bug the setting exists to fix, so dev needs a
            # tournament that exercises the non-default side of it.
            "required_commentators": 2,
            "required_trackers": 0,
            # A non-default stage reminder lead, so the worker's per-tournament
            # re-check is exercised rather than the 30-minute default.
            "stage_reminder_minutes": 45,
            **_ONSITE_META,
            # Per-tournament "tournament days" override: its own event window
            # and per-day hours, distinct from the tenant default.
            "event_start_date": today,
            "event_end_date": today + timedelta(days=3),
            "tournament_hours": {
                today.isoformat(): {"open": "09:00", "close": "23:59"},
                (today + timedelta(days=1)).isoformat(): {"open": "12:00", "close": "23:59"},
            },
        },
    )
    await backfill(onsite, **_ONSITE_META)
    # ``backfill`` only fills NULLs, and these columns are NOT NULL defaulting to
    # 1/1 — so a dev database seeded before the crew requirement existed would
    # still show this commentary-only tournament a false gap. Converge it, but
    # only while it holds the column defaults, so a hand-set value survives.
    if (onsite.required_commentators, onsite.required_trackers) == (1, 1):
        onsite.required_commentators = 2
        onsite.required_trackers = 0
        await onsite.save()
    # Same reasoning for the stage reminder lead, which also defaults NOT NULL.
    if onsite.stage_reminder_minutes == 30:
        onsite.stage_reminder_minutes = 45
        await onsite.save()
    await onsite.admins.add(staff)
    for p in (players[0], players[1]):
        await TournamentPlayers.get_or_create(tournament=onsite, user=p, tenant=tenant)

    async def make_match(
        title: str,
        offset_hours: float,
        *,
        seated: bool = False,
        started: bool = False,
        finished: bool = False,
        winner_rank: bool = True,
        stations: tuple[str, str] | None = None,
        stage: Stage | None = None,
    ) -> Match:
        scheduled_at = now + timedelta(hours=offset_hours)
        match, created = await Match.get_or_create(
            title=title, tournament=onsite, tenant=tenant,
            defaults={
                "scheduled_at": scheduled_at,
                "stage": stage,
                "is_stream_candidate": stage is not None,
            },
        )
        if not created:
            return match
        if seated or started or finished:
            match.seated_at = scheduled_at - timedelta(minutes=10)
        if started or finished:
            match.started_at = scheduled_at
        if finished:
            match.finished_at = scheduled_at + timedelta(minutes=45)
        await match.save()
        for rank, player in enumerate([players[0], players[1]], 1):
            await MatchPlayers.get_or_create(
                match=match, user=player, tenant=tenant,
                defaults={
                    "finish_rank": rank if (finished and winner_rank) else None,
                    "assigned_station": stations[rank - 1] if stations else None,
                },
            )
        return match

    # One row per step of the proctor's workflow.
    await make_match("On-Site Scheduled", 2)
    await make_match("On-Site Overdue", -0.5)
    checked_in = await make_match(
        "On-Site Checked In", 0.25, seated=True, stations=('3', '7'), stage=stage1,
    )
    # Two approved commentators and **no tracker**: a fully staffed restream for
    # a tournament that does not use trackers, and this tournament's crew
    # requirement (2 commentators, 0 trackers) says so — the coverage reports
    # call it covered. The dev tournament requires one of each and staffs both,
    # so the two fixtures together show the requirement actually varying. Crew
    # are the two entrants not in this match, which is how a restream really
    # gets staffed.
    for commentator in (players[2], players[3]):
        await Commentator.get_or_create(
            match=checked_in, user=commentator, tenant=tenant,
            defaults={"approved": True, "approved_by": staff, "acknowledged_at": now},
        )
    # Scheduled, on a stage, and inside this tournament's 45-minute lead — the
    # row the stage reminder worker has to find on a freshly seeded database.
    stage_call = await make_match("On-Site Stage Call", 0.5, stage=stage3)
    # Pair commentary, both states: on the stage call players[2] is signed up
    # and waiting on players[3]'s answer (their My Schedule carries the invite);
    # on the scheduled match players[3] brought players[2], who accepted.
    await Commentator.get_or_create(match=stage_call, user=players[2], tenant=tenant)
    await _invite(tenant, stage_call, players[2], players[3], CommentaryInviteStatus.PENDING, None)
    paired = await Match.get(title="On-Site Scheduled", tournament=onsite, tenant=tenant)
    await Commentator.get_or_create(match=paired, user=players[3], tenant=tenant)
    await Commentator.get_or_create(
        match=paired, user=players[2], tenant=tenant, defaults={"invited_by": players[3]},
    )
    await _invite(tenant, paired, players[3], players[2], CommentaryInviteStatus.ACCEPTED, now)
    # The settled states the board never draws: an invite declined and one
    # taken back. Seed-only history, so the audit log has no row for them.
    for status in (CommentaryInviteStatus.DECLINED, CommentaryInviteStatus.CANCELLED):
        await _invite(tenant, stage_call, players[3], players[2], status, now - timedelta(hours=1))
    await make_match("On-Site In Progress", -1, seated=True, started=True, stations=('4', '8'), stage=stage3)
    # Recorded but not yet confirmed — the admin's review queue.
    await make_match(
        "On-Site Awaiting Review", -2,
        seated=True, started=True, finished=True, stations=('1', '5'), stage=stage1,
    )
    # Finished with *no* winner recorded: confirming this must be refused.
    await make_match(
        "On-Site Result Missing", -3,
        seated=True, started=True, finished=True, winner_rank=False, stations=('2', '6'),
    )

    await _seed_archived_season(tenant, staff, players, now)
    await _seed_announced_season(tenant, now)
    return onsite


async def _seed_announced_season(tenant: Tenant, now: datetime) -> Tournament:
    """Next season, announced but not yet taking signups.

    The third signup state, and the only one with no fixture otherwise: without
    it the Tournaments tab's "Opening soon" section never renders in dev and
    nobody sees what an announced-but-shut card looks like. Active with no
    matches and no entrants on purpose — that is exactly what a tournament looks
    like the day it is announced.
    """
    announced, _ = await Tournament.get_or_create(
        name="Wizzrobe Cup — Next Season", tenant=tenant,
        defaults={
            "description": (
                "Signups open in a month. Same format as last season.\n\n"
                "- Open to anyone in the community\n"
                "- One qualifier round, then a double-elimination bracket\n"
            ),
            "seed_generator": "alttpr",
            "is_active": True,
            "players_per_match": 2,
            "tournament_format": "Double elimination, best of 3",
            "average_match_duration": 85,
            "signups_open_at": now + timedelta(days=30),
            "signups_close_at": now + timedelta(days=60),
        },
    )
    await backfill(
        announced,
        signups_open_at=now + timedelta(days=30),
        signups_close_at=now + timedelta(days=60),
    )
    return announced


async def _seed_archived_season(
    tenant: Tenant, staff: User, players: list[User], now: datetime,
) -> Tournament:
    """Last season's event, closed out: ``is_active=False`` with its results kept.

    A community does not delete a finished season — it archives it and starts the
    next one, so at any moment a real database holds both. Every other seeded
    tournament is active, which left the inactive half of every
    ``is_active=True`` filter (the profile page's tournament lists, the
    notification-preference list, the admin selectors) with nothing to exclude,
    and the tournament table's Active column uniform.
    """
    archived, _ = await Tournament.get_or_create(
        name="Wizzrobe Cup — Last Season", tenant=tenant,
        defaults={
            "description": "Closed out. Kept for its results and its triforce texts.",
            "seed_generator": "alttpr",
            "is_active": False,
            "players_per_match": 2,
            "tournament_format": "Single elimination, best of 3",
            "average_match_duration": 80,
            "max_match_duration": 130,
        },
    )
    await archived.admins.add(staff)
    for player in players[:2]:
        await TournamentPlayers.get_or_create(
            tournament=archived, user=player, tenant=tenant,
        )
    final, created = await Match.get_or_create(
        title="Last Season — Grand Final", tournament=archived, tenant=tenant,
        defaults={"scheduled_at": now - timedelta(days=60), "is_stream_candidate": True},
    )
    if created:
        final.seated_at = final.scheduled_at - timedelta(minutes=10)
        final.started_at = final.scheduled_at
        final.finished_at = final.scheduled_at + timedelta(minutes=95)
        final.confirmed_at = final.finished_at + timedelta(minutes=5)
        await final.save()
        for rank, player in enumerate(players[:2], 1):
            await MatchPlayers.get_or_create(
                match=final, user=player, tenant=tenant, defaults={"finish_rank": rank},
            )
    return archived
