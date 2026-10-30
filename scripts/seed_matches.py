"""Dev-seed fixtures for the general-purpose tournament's matches.

Split out of ``seed_dev.py`` for length, following the one-module-per-domain
convention the volunteer, equipment, on-site and race-night fixtures already
use: called from inside the caller's ``tenant_scope``, idempotent, and
tenant-stamped like every other seeded row.

This is the lifecycle fixture set — one match per state a proctor or admin can
meet (scheduled / checked-in / in-progress / finished / disputed / off-stream /
undated) — plus everything that hangs off those matches: station seating, the
rolled seeds, the dispute flag, acknowledgments in each of their states,
watchers, stream volunteers, and the tournament notification preference.

Returns the matches by key because the rest of the seed builds on them: crew
signs up for them, the Challonge mirror points at the finished one, the audit
fixtures cite them.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from application.services._seedgen_wwr import decode_permalink as decode_wwr_permalink
from application.services._seedgen_wwr import reseed as reseed_wwr_permalink
from models import (
    ForfeitReason,
    GeneratedSeeds,
    Match,
    MatchAcknowledgment,
    MatchNotificationLevel,
    MatchPlayers,
    MatchRescheduleRequest,
    MatchStreamVolunteer,
    MatchWatcher,
    ProviderTask,
    ProviderTaskStatus,
    RescheduleRequestKind,
    RescheduleRequestStatus,
    Stage,
    Tenant,
    Tournament,
    TournamentNotificationPreference,
    User,
)


async def seed_matches_for_tenant(
    tenant: Tenant,
    tournament: Tournament,
    staff: User,
    players: list[User],
    stages: dict[str, Stage],
    now: datetime,
) -> dict[str, Match | GeneratedSeeds]:
    """Seed the lifecycle matches and their extras; return them by key."""
    stage1, stage2, stage3 = stages["stage1"], stages["stage2"], stages["stage3"]

    async def make_match(
        title: str,
        offset_hours: float | None,
        *,
        seated: bool = False,
        started: bool = False,
        finished: bool = False,
        confirmed: bool = True,
        p1: User | None = None,
        p2: User | None = None,
        stage: Stage | None = None,
        stream_candidate: bool = True,
        comment: str | None = None,
    ) -> Match:
        scheduled_at = now + timedelta(hours=offset_hours) if offset_hours is not None else None
        match, created = await Match.get_or_create(
            title=title,
            tournament=tournament,
            tenant=tenant,
            defaults={
                "scheduled_at": scheduled_at,
                "stage": stage,
                "is_stream_candidate": stream_candidate,
                "comment": comment,
            },
        )
        if not created:
            return match
        anchor = scheduled_at or now
        if seated or started or finished:
            match.seated_at = anchor - timedelta(minutes=10)
        if started or finished:
            match.started_at = anchor
        if finished:
            match.finished_at = anchor + timedelta(hours=1)
            if confirmed:
                match.confirmed_at = anchor + timedelta(hours=1, minutes=5)
        await match.save()
        for rank, player in enumerate([p1, p2], 1):
            if player:
                await MatchPlayers.get_or_create(
                    match=match,
                    user=player,
                    tenant=tenant,
                    defaults={"finish_rank": rank if finished else None},
                )
        return match

    scheduled_match = await make_match("Scheduled Match",   2,   p1=players[0], p2=players[1])
    checked_in_match = await make_match("Checked-In Match",  0,   seated=True,  p1=players[0], p2=players[1], stage=stage1)
    in_progress_match = await make_match("In-Progress Match", -1,  seated=True, started=True,  p1=players[2], p2=players[3], stage=stage2)
    finished_match = await make_match("Finished Match",    -3,  seated=True, started=True, finished=True, p1=players[0], p2=players[2], stage=stage1)
    stage3_match = await make_match(
        "Stage 3 Rematch", 3, seated=True, p1=players[1], p2=players[3], stage=stage3,
        comment="Requested a rematch after a disconnect last round.",
    )
    off_stream_match = await make_match(
        "Off-Stream Match", 4, p1=players[2], p2=players[0], stream_candidate=False,
    )
    future_match = await make_match(
        "Grand Finals", 30, p1=players[3], p2=players[1], stage=stage1,
        comment="Best of 3, winner takes the trophy.",
    )
    disputed_match = await make_match(
        "Disputed Match", -5, seated=True, started=True, finished=True, confirmed=False,
        p1=players[1], p2=players[2], stage=stage2,
        comment="Result under review — desync reported by both players.",
    )
    for title, offset, reason, note, winner, out in (
        ("Forfeited Match", -6, ForfeitReason.NO_SHOW, "Called twice, no answer.", players[3], players[0]),
        ("Withdrawn Match", -7, ForfeitReason.WITHDREW, None, players[1], players[3]),
        ("Disqualified Match", -8, ForfeitReason.DISQUALIFIED, "Outside assistance.", players[2], players[1]),
    ):
        forfeit_match = await make_match(
            title, offset, seated=True, started=True, finished=True,
            p1=winner, p2=out, stream_candidate=False,
        )
        if forfeit_match.forfeit_reason is None:
            forfeit_match.forfeit_reason = reason
            forfeit_match.forfeit_note = note
            await forfeit_match.save()
            await MatchPlayers.filter(match=forfeit_match, user=out, tenant=tenant).update(finish_rank=None)
    await make_match(
        "TBD Match", None, p1=players[3], p2=players[0], stream_candidate=False,
    )
    await Match.get_or_create(
        title="ALTTPR Showcase Race",
        tournament=tournament,
        tenant=tenant,
        defaults={
            "scheduled_at": now + timedelta(hours=5),
            "stage": stage1,
            "description": "Top 4 from Friday's open bracket. Players are added once **Saturday's semis** finish.",
        },
    )
    # Exactly one: title=None is a usable natural key only while it's unique in the tournament.
    await make_match(None, 6, p1=players[1], p2=players[2], stage=stage2)
    for seated_match, labels in (
        (checked_in_match, ("1", "5")),
        (in_progress_match, ("2", "6")),
    ):
        seated_players = await MatchPlayers.filter(
            match=seated_match, tenant=tenant,
        ).order_by("id")
        for seated_player, label in zip(seated_players, labels, strict=False):
            if seated_player.assigned_station is None and tenant.slug == "default":
                seated_player.assigned_station = label
                await seated_player.save()

    print(f"    [{tenant.slug}] matches ok")

    seed, _ = await GeneratedSeeds.get_or_create(
        seed_url="https://alttpr.com/en/h/DevSeed0",
        tenant=tenant,
        defaults={
            "seed_info": json.dumps({"logic": "NoGlitches", "spoilers": "off"}),
            "randomizer": "alttpr",
            "settings_snapshot": {"glitches": "none", "mode": "open", "goal": "ganon"},
            "provider_meta": {
                "provider": "alttpr", "operation": "generate_seed",
                "attempts": 1, "latency_ms": 4210, "surface": "match",
            },
        },
    )
    if finished_match.generated_seed_id is None:
        finished_match.generated_seed = seed
        await finished_match.save()

    disputed_seed, _ = await GeneratedSeeds.get_or_create(
        seed_url="https://alttpr.com/en/h/DevSeed1",
        tenant=tenant,
        defaults={
            "seed_info": json.dumps({"logic": "Glitched", "spoilers": "mystery"}),
            "randomizer": "alttpr",
            "settings_snapshot": {"glitches": "overworld_glitches", "mode": "open"},
            "provider_meta": {
                "provider": "alttpr", "operation": "generate_seed",
                "attempts": 2, "latency_ms": 11840, "surface": "match",
            },
        },
    )
    if disputed_match.generated_seed_id is None:
        disputed_match.generated_seed = disputed_seed
        await disputed_match.save()

    upcoming_seed, _ = await GeneratedSeeds.get_or_create(
        seed_url="https://alttpr.com/en/h/DevSeed2",
        tenant=tenant,
        defaults={
            "seed_info": json.dumps({"logic": "NoGlitches", "spoilers": "off"}),
            "randomizer": "alttpr",
            "settings_snapshot": {"glitches": "none", "mode": "open", "goal": "ganon"},
            "provider_meta": {
                "provider": "alttpr", "operation": "generate_seed",
                "attempts": 1, "latency_ms": 3980, "surface": "match",
            },
        },
    )
    for rolled, code in ((seed, ['Bow', 'Hookshot', 'Bombs', 'Lamp', 'Cape']),
                         (upcoming_seed, ['Moon Pearl', 'Ether', 'Shovel', 'Heart', 'Big Key'])):
        if rolled.seed_code is None:
            rolled.seed_code = code
            await rolled.save(update_fields=["seed_code"])
    if stage3_match.generated_seed_id is None:
        stage3_match.generated_seed = upcoming_seed
        await stage3_match.save()

    wwr_reference = json.loads(
        (Path(__file__).resolve().parent.parent / "presets" / "wwr" / "sgl2026.json").read_text()
    )["permalink"]
    wwr_seed, _ = await GeneratedSeeds.get_or_create(
        seed_url=reseed_wwr_permalink(wwr_reference, "DevSeedWindWaker"),
        tenant=tenant,
        defaults={
            "seed_info": "Generated seed for the off-stream match",
            "randomizer": "wwr",
            "settings_snapshot": {
                "reference_permalink": wwr_reference,
                "version": decode_wwr_permalink(wwr_reference).version,
                "seed_name": "DevSeedWindWaker",
            },
            "provider_meta": {
                "provider": "wwr", "operation": "generate_seed",
                "attempts": 1, "latency_ms": 2, "surface": "match",
            },
        },
    )
    if off_stream_match.generated_seed_id is None:
        off_stream_match.generated_seed = wwr_seed
        await off_stream_match.save()

    provider_tasks = [
        (scheduled_match, ProviderTaskStatus.RUNNING, 'dev-task-running', None),
        (future_match, ProviderTaskStatus.QUEUED, None, None),
        (finished_match, ProviderTaskStatus.SUCCEEDED, 'dev-task-done', None),
        (disputed_match, ProviderTaskStatus.FAILED, 'dev-task-failed',
         'DK64 Randomizer failed to generate the seed.'),
        (stage3_match, ProviderTaskStatus.ABANDONED, 'dev-task-abandoned',
         'Timed out waiting for the provider.'),
    ]
    for match, status, upstream_id, error in provider_tasks:
        await ProviderTask.get_or_create(
            match=match, status=status, tenant=tenant,
            defaults={
                "provider": "dk64r",
                "operation": "generate_seed",
                "provider_task_id": upstream_id,
                "provider_params": {"branch": "stable"},
                "settings_snapshot": (
                    {"branch": "stable", "generate_spoilerlog": False}
                    if upstream_id else None
                ),
                "error": error,
            },
        )

    if not disputed_match.needs_review:
        disputed_match.needs_review = True
        await disputed_match.save()

    for player in (players[0], players[1]):
        await MatchAcknowledgment.get_or_create(
            match=checked_in_match, user=player, tenant=tenant,
            defaults={"acknowledged_at": now, "auto_acknowledged": False},
        )
    await MatchAcknowledgment.get_or_create(
        match=scheduled_match, user=players[0], tenant=tenant,
        defaults={"acknowledged_at": now, "auto_acknowledged": False},
    )
    await MatchAcknowledgment.get_or_create(
        match=scheduled_match, user=players[1], tenant=tenant,
        defaults={"acknowledged_at": None, "auto_acknowledged": False},
    )
    await MatchAcknowledgment.get_or_create(
        match=future_match, user=players[3], tenant=tenant,
        defaults={"acknowledged_at": now, "auto_acknowledged": True},
    )
    await MatchAcknowledgment.get_or_create(
        match=future_match, user=players[1], tenant=tenant,
        defaults={"acknowledged_at": None, "auto_acknowledged": False},
    )

    for m in (scheduled_match, in_progress_match, disputed_match):
        await MatchWatcher.get_or_create(user=staff, match=m, tenant=tenant)

    for m, volunteers in (
        (scheduled_match, (players[0], players[1])),
        (future_match, (players[3],)),
    ):
        for volunteer in volunteers:
            await MatchStreamVolunteer.get_or_create(user=volunteer, match=m, tenant=tenant)

    reschedule_specs = [
        (scheduled_match, players[0], RescheduleRequestKind.RESCHEDULE,
         RescheduleRequestStatus.PENDING, now + timedelta(hours=6),
         "Work shifted my evening — could we push this back a few hours?",
         None, True),
        (off_stream_match, players[2], RescheduleRequestKind.RESCHEDULE,
         RescheduleRequestStatus.PENDING, now + timedelta(hours=8),
         "Stuck at work until late — could we start a few hours later?",
         None, False),
        (stage3_match, players[1], RescheduleRequestKind.CANCEL,
         RescheduleRequestStatus.PENDING, None,
         "My opponent and I both forfeited this bracket — it doesn't need playing.",
         None, False),
        (future_match, players[3], RescheduleRequestKind.RESCHEDULE,
         RescheduleRequestStatus.APPROVED, now + timedelta(days=1, hours=2),
         "Flight lands too late for the original slot.",
         "Moved you to the later slot — stage 1 is free then.", True),
        (future_match, players[1], RescheduleRequestKind.RESCHEDULE,
         RescheduleRequestStatus.SUPERSEDED, now + timedelta(days=2),
         "Can we move this to Sunday instead?", None, False),
        (finished_match, players[0], RescheduleRequestKind.RESCHEDULE,
         RescheduleRequestStatus.DECLINED, now - timedelta(days=2),
         "Could we start an hour later?",
         "Sorry — the restream slot is locked in with the broadcast team.", False),
        (in_progress_match, players[2], RescheduleRequestKind.RESCHEDULE,
         RescheduleRequestStatus.WITHDRAWN, now - timedelta(hours=20),
         "Might have a conflict — will confirm.", None, False),
    ]
    for match_row, asker, kind, status, proposed, reason, note, agreed in reschedule_specs:
        decided = status is not RescheduleRequestStatus.PENDING
        await MatchRescheduleRequest.get_or_create(
            match=match_row, requested_by=asker, status=status, tenant=tenant,
            defaults={
                "kind": kind,
                "proposed_at": proposed,
                "reason": reason,
                "original_scheduled_at": match_row.scheduled_at,
                "opponent_agreed_at": now - timedelta(hours=1) if agreed else None,
                "decision_note": note,
                "decided_by": staff if decided and note else None,
                "decided_at": now - timedelta(hours=2) if decided else None,
            },
        )

    notification_specs = [
        (staff, MatchNotificationLevel.ALL),
        (players[0], MatchNotificationLevel.STREAMED),
        (players[1], MatchNotificationLevel.STREAMED_AND_CANDIDATES),
        (players[2], MatchNotificationLevel.NONE),
    ]
    for user, level in notification_specs:
        await TournamentNotificationPreference.get_or_create(
            user=user, tournament=tournament, tenant=tenant,
            defaults={"match_notifications": level},
        )
    print(f"    [{tenant.slug}] match extras ok")

    return {
        "scheduled": scheduled_match,
        "checked_in": checked_in_match,
        "in_progress": in_progress_match,
        "finished": finished_match,
        "stage3": stage3_match,
        "future": future_match,
        "disputed": disputed_match,
        "seed": seed,
    }
