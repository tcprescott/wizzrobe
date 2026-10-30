"""Dev-seed fixtures for the async-qualifier subsystem (PR 9/10).

Split out of ``seed_online.py`` when that module crossed the 800-line budget,
following the one-module-per-domain convention the rest of the seed uses: called
from inside the caller's ``tenant_scope``, idempotent, tenant-stamped.

Two qualifiers, because the lockdown means a player can never see a leaderboard
on an open one: the **open** qualifier carries the reviewer-facing states (pools,
permalinks, and a run in every status a runner or reviewer can meet), and the
**closed** one is the only place the player-facing board, its Score/Estimate/Slots
columns and the "this qualifier closed" message are reachable by the role they
are written for.
"""

from datetime import datetime, timedelta, timezone

from models import (
    AsyncQualifier,
    AsyncQualifierLiveRace,
    AsyncQualifierLiveRaceStatus,
    AsyncQualifierPermalink,
    AsyncQualifierPool,
    AsyncQualifierReviewNote,
    AsyncQualifierReviewStatus,
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    Preset,
    Tenant,
    User,
)


async def seed_qualifiers_for_tenant(tenant: Tenant, preset: Preset) -> None:
    """Async Qualifier fixtures (PR 9): **two** qualifiers.

    The open one has four pools (one preset-tied, one live-race, one tied to a preset
    that cannot roll), permalinks, and
    runs across every state a reviewer or runner can meet — approved+scored (sets
    par), pending (the reviewer queue), rejected-with-a-note, forfeited, and voided
    by a reattempt — so the admin Qualifiers tab, reviewer queue, Runs tab and the
    lockdown (non-staff can't see the board) are all demonstrable.

    The **closed** one exists because the lockdown means a player can never see a
    leaderboard on an open qualifier: without a closed fixture the player-facing
    board, its Score/Estimate/Slots columns, and the "this qualifier closed"
    message are unreachable in dev by the role they are written for. Its three
    scored runs deliberately fill different numbers of slots so ``actual`` and
    ``estimate`` visibly differ."""
    now = datetime.now(timezone.utc)
    staff = await User.get_or_none(username="staff_user")
    runner_a = await User.get_or_none(username="player_three")
    runner_b = await User.get_or_none(username="player_four")

    qualifier, _ = await AsyncQualifier.get_or_create(
        name="Dev Async Qualifier", tenant=tenant,
        defaults={
            "description": (
                "Self-paced qualifier fixture for local dev. **Top 8** seed into the bracket.\n\n"
                "- One attempt per seed\n"
                "- Submit your VOD within 24 hours\n"
            ),
            "event_name": "Wizzrobe Dev Season",
            "opens_at": now - timedelta(days=1),
            "closes_at": now + timedelta(days=7),
            "runs_per_pool": 2,
            "allowed_reattempts": 1,
            "is_active": True,
            "config": {"par_sample_size": 3},
        },
    )
    if staff is not None:
        await qualifier.admins.add(staff)

    standard, _ = await AsyncQualifierPool.get_or_create(
        qualifier=qualifier, name="Standard Pool", tenant=tenant,
        defaults={"preset": preset},
    )
    bonus, _ = await AsyncQualifierPool.get_or_create(
        qualifier=qualifier, name="Bonus Pool", tenant=tenant,
    )
    async_preset, _ = await Preset.get_or_create(
        name="DK64 Async", randomizer="dk64r", tenant=tenant,
        defaults={"settings": {"preset": "dev"},
                  "description": "Rolls asynchronously — cannot fill a qualifier pool."},
    )
    await AsyncQualifierPool.get_or_create(
        qualifier=qualifier, name="Async-Randomizer Pool", tenant=tenant,
        defaults={"preset": async_preset},
    )

    async def _permalink(pool: AsyncQualifierPool, url: str) -> AsyncQualifierPermalink:
        pl, _ = await AsyncQualifierPermalink.get_or_create(
            pool=pool, url=url, tenant=tenant,
        )
        return pl

    p1 = await _permalink(standard, f"https://alttpr.com/en/h/dev-{tenant.slug}-std-1")
    await _permalink(standard, f"https://alttpr.com/en/h/dev-{tenant.slug}-std-2")
    await _permalink(standard, f"https://alttpr.com/en/h/dev-{tenant.slug}-std-3")
    # Its own seed: an .exists() guard keyed on a shared permalink would find another run and skip this one.
    p_stale = await _permalink(standard, f"https://alttpr.com/en/h/dev-{tenant.slug}-std-4")
    await _permalink(bonus, f"https://alttpr.com/en/h/dev-{tenant.slug}-bonus-1")
    await _permalink(bonus, f"https://alttpr.com/en/h/dev-{tenant.slug}-bonus-2")

    if runner_a is not None:
        run_a = await AsyncQualifierRun.filter(
            qualifier=qualifier, user=runner_a, permalink=p1
        ).first()
        if run_a is None:
            await AsyncQualifierRun.create(
                tenant=tenant, qualifier=qualifier, user=runner_a, permalink=p1,
                status=AsyncQualifierRunStatus.FINISHED,
                review_status=AsyncQualifierReviewStatus.APPROVED,
                started_at=now - timedelta(hours=3), finished_at=now - timedelta(hours=1, minutes=30),
                elapsed_seconds=5400, measured_seconds=5460,
                runner_vod_url="https://twitch.tv/videos/dev-a",
                reviewed_by=staff, reviewed_at=now - timedelta(hours=1), score=100.0,
            )
            if p1.par_time is None:
                p1.par_time = 5400
                p1.par_updated_at = now
                await p1.save()

    if runner_b is not None:
        run_b = await AsyncQualifierRun.filter(
            qualifier=qualifier, user=runner_b, permalink=p1
        ).first()
        if run_b is None:
            run_b = await AsyncQualifierRun.create(
                tenant=tenant, qualifier=qualifier, user=runner_b, permalink=p1,
                status=AsyncQualifierRunStatus.FINISHED,
                review_status=AsyncQualifierReviewStatus.PENDING,
                started_at=now - timedelta(hours=2), finished_at=now - timedelta(minutes=20),
                elapsed_seconds=6000, measured_seconds=9600,
                runner_vod_url="https://twitch.tv/videos/dev-b",
            )
        if staff is not None and not await AsyncQualifierReviewNote.filter(
            run=run_b, tenant=tenant
        ).exists():
            await AsyncQualifierReviewNote.create(
                tenant=tenant, run=run_b, author=staff,
                note="VOD checked through the halfway split; finish looks clean.",
            )

    if runner_b is not None and not await AsyncQualifierRun.filter(
        qualifier=qualifier, user=runner_b, permalink=p_stale,
    ).exists():
        await AsyncQualifierRun.create(
            tenant=tenant, qualifier=qualifier, user=runner_b, permalink=p_stale,
            status=AsyncQualifierRunStatus.FINISHED,
            review_status=AsyncQualifierReviewStatus.PENDING,
            started_at=now - timedelta(hours=11), finished_at=now - timedelta(hours=9),
            elapsed_seconds=5700, measured_seconds=7200,
        )

    p2 = await _permalink(bonus, f"https://alttpr.com/en/h/dev-{tenant.slug}-bonus-2")
    if runner_a is not None:
        rejected = await AsyncQualifierRun.filter(
            qualifier=qualifier, user=runner_a, permalink=p2
        ).first()
        if rejected is None:
            rejected = await AsyncQualifierRun.create(
                tenant=tenant, qualifier=qualifier, user=runner_a, permalink=p2,
                status=AsyncQualifierRunStatus.FINISHED,
                review_status=AsyncQualifierReviewStatus.REJECTED,
                started_at=now - timedelta(hours=8), finished_at=now - timedelta(hours=6),
                elapsed_seconds=4800, measured_seconds=7200,
                reviewed_by=staff, reviewed_at=now - timedelta(hours=5),
            )
        if staff is not None and not await AsyncQualifierReviewNote.filter(
            run=rejected, tenant=tenant
        ).exists():
            await AsyncQualifierReviewNote.create(
                tenant=tenant, run=rejected, author=staff,
                note="The VoD cuts off before the final boss, so the finish time "
                     "can't be verified. Use a reattempt and stream the whole run.",
            )

    p3 = await _permalink(standard, f"https://alttpr.com/en/h/dev-{tenant.slug}-std-2")
    if runner_b is not None:
        if not await AsyncQualifierRun.filter(
            qualifier=qualifier, user=runner_b, status=AsyncQualifierRunStatus.FORFEIT
        ).exists():
            await AsyncQualifierRun.create(
                tenant=tenant, qualifier=qualifier, user=runner_b, permalink=p3,
                status=AsyncQualifierRunStatus.FORFEIT,
                review_status=AsyncQualifierReviewStatus.APPROVED,
                started_at=now - timedelta(hours=5), finished_at=now - timedelta(hours=5),
                score=0.0,
            )
        p4 = await _permalink(standard, f"https://alttpr.com/en/h/dev-{tenant.slug}-std-3")
        if not await AsyncQualifierRun.filter(
            qualifier=qualifier, user=runner_b, reattempted=True
        ).exists():
            await AsyncQualifierRun.create(
                tenant=tenant, qualifier=qualifier, user=runner_b, permalink=p4,
                status=AsyncQualifierRunStatus.FORFEIT,
                review_status=AsyncQualifierReviewStatus.APPROVED,
                started_at=now - timedelta(hours=10), finished_at=now - timedelta(hours=10),
                score=0.0, reattempted=True,
                reattempt_reason="Emulator crashed on the opening cutscene.",
            )

    p5 = await _permalink(bonus, f"https://alttpr.com/en/h/dev-{tenant.slug}-bonus-1")
    if runner_a is not None and not await AsyncQualifierRun.filter(
        qualifier=qualifier, user=runner_a, status=AsyncQualifierRunStatus.IN_PROGRESS
    ).exists():
        await AsyncQualifierRun.create(
            tenant=tenant, qualifier=qualifier, user=runner_a, permalink=p5,
            status=AsyncQualifierRunStatus.IN_PROGRESS,
            review_status=AsyncQualifierReviewStatus.PENDING,
            started_at=now - timedelta(minutes=25),
        )
    if runner_b is not None and not await AsyncQualifierRun.filter(
        qualifier=qualifier, user=runner_b, status=AsyncQualifierRunStatus.DISQUALIFIED
    ).exists():
        dq = await AsyncQualifierRun.create(
            tenant=tenant, qualifier=qualifier, user=runner_b, permalink=p5,
            status=AsyncQualifierRunStatus.DISQUALIFIED,
            review_status=AsyncQualifierReviewStatus.REJECTED,
            started_at=now - timedelta(days=1), finished_at=now - timedelta(days=1),
            elapsed_seconds=3900, measured_seconds=3900,
            reviewed_by=staff, reviewed_at=now - timedelta(hours=20), score=0.0,
        )
        if staff is not None:
            await AsyncQualifierReviewNote.create(
                tenant=tenant, run=dq, author=staff,
                note="Ran the wrong permalink — disqualified, not a reattempt.",
            )

    live_pool, _ = await AsyncQualifierPool.get_or_create(
        qualifier=qualifier, name="Live Race Pool", tenant=tenant,
    )
    live_race_specs = [
        (1, "Dev Live Qualifier Race", AsyncQualifierLiveRaceStatus.SCHEDULED, True),
        (2, "Dev Live Race — Room Open", AsyncQualifierLiveRaceStatus.PENDING, True),
        (3, "Dev Live Race — Racing", AsyncQualifierLiveRaceStatus.IN_PROGRESS, True),
        (4, "Dev Live Race — Results In", AsyncQualifierLiveRaceStatus.FINISHED, True),
        (5, "Dev Live Race — Called Off", AsyncQualifierLiveRaceStatus.CANCELLED, True),
        (6, "Dev Live Race — Seed To Pick", AsyncQualifierLiveRaceStatus.SCHEDULED, False),
    ]
    for index, match_title, status, seeded in live_race_specs:
        permalink, _ = await AsyncQualifierPermalink.get_or_create(
            pool=live_pool, url=f"https://alttpr.com/en/h/dev-{tenant.slug}-live-{index}",
            tenant=tenant, defaults={"live_race": True},
        )
        race, _ = await AsyncQualifierLiveRace.get_or_create(
            pool=live_pool, match_title=match_title, tenant=tenant,
            defaults={"permalink": permalink if seeded else None, "status": status},
        )
        # Set on every run: a defaults= value never reaches a row that already exists.
        wanted = (["MysteryRacer"] if status == AsyncQualifierLiveRaceStatus.FINISHED
                  else None)
        if race.unmatched_handles != wanted:
            race.unmatched_handles = wanted
            await race.save()
        if (
            status is AsyncQualifierLiveRaceStatus.PENDING
            and runner_a is not None
            and not await AsyncQualifierRun.filter(
                qualifier=qualifier, status=AsyncQualifierRunStatus.PENDING
            ).exists()
        ):
            await AsyncQualifierRun.create(
                tenant=tenant, qualifier=qualifier, user=runner_a, permalink=permalink,
                status=AsyncQualifierRunStatus.PENDING,
                review_status=AsyncQualifierReviewStatus.PENDING,
            )

    await _seed_closed_qualifier(tenant)


async def _seed_closed_qualifier(tenant: Tenant) -> None:
    """A finished qualifier whose results are public — the only way a player can
    see a leaderboard in dev (the active-window lockdown hides the open one)."""
    now = datetime.now(timezone.utc)
    staff = await User.get_or_none(username="staff_user")
    runners = [
        await User.get_or_none(username="player_two"),
        await User.get_or_none(username="player_three"),
        await User.get_or_none(username="player_four"),
    ]

    qualifier, _ = await AsyncQualifier.get_or_create(
        name="Dev Async Qualifier (Closed)", tenant=tenant,
        defaults={
            "description": "A finished qualifier — results are public, so the "
                           "player-facing leaderboard is reachable in dev.",
            "event_name": "Wizzrobe Dev Season",
            "opens_at": now - timedelta(days=21),
            "closes_at": now - timedelta(days=7),
            "runs_per_pool": 2,
            "allowed_reattempts": 1,
            "is_active": True,
            "config": {"par_sample_size": 3},
        },
    )
    if staff is not None:
        await qualifier.admins.add(staff)

    pool, _ = await AsyncQualifierPool.get_or_create(
        qualifier=qualifier, name="Qualifier Pool", tenant=tenant,
    )
    permalinks = []
    for n in (1, 2):
        pl, _ = await AsyncQualifierPermalink.get_or_create(
            pool=pool, url=f"https://alttpr.com/en/h/dev-{tenant.slug}-closed-{n}", tenant=tenant,
        )
        permalinks.append(pl)

    plan = [
        (runners[0], [(permalinks[0], 4500, 104.0), (permalinks[1], 5100, 98.0)]),
        (runners[1], [(permalinks[0], 5400, 92.0)]),
        (runners[2], [(permalinks[1], 6300, 74.0)]),
    ]
    for runner, results in plan:
        if runner is None:
            continue
        for permalink, seconds, score in results:
            if await AsyncQualifierRun.filter(
                qualifier=qualifier, user=runner, permalink=permalink
            ).exists():
                continue
            await AsyncQualifierRun.create(
                tenant=tenant, qualifier=qualifier, user=runner, permalink=permalink,
                status=AsyncQualifierRunStatus.FINISHED,
                review_status=AsyncQualifierReviewStatus.APPROVED,
                started_at=now - timedelta(days=10), finished_at=now - timedelta(days=10),
                elapsed_seconds=seconds, measured_seconds=seconds + 240,
                reviewed_by=staff, reviewed_at=now - timedelta(days=9), score=score,
            )
    for permalink in permalinks:
        if permalink.par_time is None:
            permalink.par_time = 5000
            permalink.par_updated_at = now - timedelta(days=9)
            await permalink.save()
