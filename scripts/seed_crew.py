"""Crew signups — one row per state the crew cells can render.

Commentators and trackers are a two-party workflow with four transitions
(signup, approval, acknowledgment, withdrawal) and the schedule's crew cell
draws a different control for each. So each row here earns its place by being a
*state*, not a person: pending, approved-and-acknowledged, approved-but-not-yet,
and a fully crewed restream.

The approved-but-unacknowledged row is the one to keep: it is the only fixture
that renders the Acknowledge control and the "Approved, awaiting acknowledgment"
marker, and while the seed lacked it neither was reachable in a dev database
(docs/features/match-participation.md).

These rows now feed three surfaces, not one: the board's crew cells, Reports →
Crew Activity with its Pending-only filter (the unapproved rows), and **My Crew**
on Home (the volunteer's own view — ``players[0]``'s approved-but-unacknowledged
tracker slot on ``future`` is the fixture that renders its Confirm button).
"""

from datetime import datetime, timedelta

from models import Commentator, Match, Tenant, Tracker, User


async def seed_crew_for_tenant(
    tenant: Tenant,
    staff: User,
    sm: User,
    proctor: User,
    players: list[User],
    matches: dict[str, Match],
    now: datetime,
) -> None:
    """Crew every seeded match in a different state. Idempotent."""
    checked_in = matches["checked_in"]
    in_progress = matches["in_progress"]
    finished = matches["finished"]
    stage3 = matches["stage3"]
    future = matches["future"]
    scheduled = matches["scheduled"]

    approved = {"approved": True, "approved_by": staff, "acknowledged_at": now}

    await Commentator.get_or_create(
        match=in_progress, user=sm, tenant=tenant, defaults=approved,
    )
    await Tracker.get_or_create(
        match=in_progress, user=proctor, tenant=tenant,
        defaults={"approved": False},
    )
    await Commentator.get_or_create(
        match=finished, user=proctor, tenant=tenant,
        defaults={**approved, "acknowledged_at": now - timedelta(hours=3)},
    )
    # The finished match went out on Stage 1, so it is what Reports → Crew
    # Activity's "Crew by tournament" counts; a tracker gives it both columns.
    await Tracker.get_or_create(
        match=finished, user=players[3], tenant=tenant,
        defaults={**approved, "acknowledged_at": now - timedelta(hours=3)},
    )
    await Commentator.get_or_create(
        match=future, user=sm, tenant=tenant, defaults=approved,
    )
    await Commentator.get_or_create(
        match=future, user=proctor, tenant=tenant,
        defaults={"approved": False},
    )
    await Tracker.get_or_create(
        match=stage3, user=sm, tenant=tenant, defaults=approved,
    )
    # So the proctor's My Crew carries all three live chips at once, which is
    # what the crew help article's screenshot shows.
    await Commentator.get_or_create(
        match=stage3, user=proctor, tenant=tenant,
        defaults={"approved": True, "approved_by": staff},
    )

    # Approved but **not yet acknowledged** — see the module docstring. Note the
    # absent ``acknowledged_at``, not a None one: that is the whole fixture.
    await Tracker.get_or_create(
        match=future, user=players[0], tenant=tenant,
        defaults={"approved": True, "approved_by": staff},
    )

    # Approved on a match with **no stage yet**: crew is only needed on stage,
    # so the stream manager's My Schedule timeline draws this slot dashed.
    await Tracker.get_or_create(
        match=scheduled, user=sm, tenant=tenant, defaults=approved,
    )

    # A **fully crewed** restream: two approved commentators and an approved
    # tracker on one match, which is what a stage match actually goes live with.
    # Every other seeded match is partly crewed, so the coverage surfaces (and
    # the crew card's full state) had nothing complete to show. The crew here are
    # the two players who are *not* in this match — which is also how a community
    # really staffs a restream.
    for commentator in (players[2], sm):
        await Commentator.get_or_create(
            match=checked_in, user=commentator, tenant=tenant, defaults=approved,
        )
    await Tracker.get_or_create(
        match=checked_in, user=players[3], tenant=tenant, defaults=approved,
    )
