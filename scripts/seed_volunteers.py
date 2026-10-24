"""Dev-seed fixtures for one tenant's volunteer scheduling surface.

Split out of ``seed_dev.py`` when that module crossed the 800-line budget,
following the ``seed_observability_for_tenant`` / ``seed_brackets_for_tenant``
convention already used there: one module per domain, called from inside the
caller's ``tenant_scope``. Idempotent (``get_or_create`` / existence-guarded)
and tenant-stamped like every other seeded row.

The seeded pool is deliberately assignable: the VOLUNTEER role grants in
``seed_dev.py`` mirror the qualified + available users created here,
so the Vol. Roster tab and the auto-scheduler have something real to show
(``VolunteerPoolService.assignable_volunteers`` admits ``Role.VOLUNTEER`` and
``Role.STAFF``).
"""

from datetime import date, datetime, timedelta

from application.services.volunteer.volunteer_role_mapping_service import (
    VolunteerRoleMappingService,
)
from application.utils.timezone import parse_local_datetime
from models import (
    Role,
    Tenant,
    User,
    VolunteerAssignment,
    VolunteerAvailability,
    VolunteerAvailabilityStatus,
    VolunteerPosition,
    VolunteerQualification,
    VolunteerRoleMapping,
    VolunteerShift,
)
from scripts.seed_support import FULL_RACERS


async def seed_volunteers_for_tenant(
    tenant: Tenant,
    users: dict[str, User],
    staff: User,
    today: date,
    now_utc: datetime,
) -> None:
    """Seed positions, opt-ins, qualifications, availability, shifts and one assignment."""
    # staff_user is deliberately *not* granted VOLUNTEER_COORDINATOR: the gate is
    # ``is_staff(user) or is_volunteer_coordinator(user)``, so the grant bought
    # nothing and cost the seed its plain-STAFF fixture (one holder per role —
    # docs/reference/dev-seed.md). vc_user is the coordinator.

    # Descriptions are what the volunteer's own card shows as the standing job
    # brief, so two of the four carry one.
    position_specs = [
        ("Check-in Desk", 1, 1,
         "Greet arrivals at the main door, check them against the entrant list, "
         "hand out badges."),
        ("Race Proctor", 2, 1,
         "Run one race room: seat the players, roll the seed, start them, record "
         "the winner."),
        ("Broadcast Tech", 3, 3, None),  # multiple concurrent slots
        ("Admin Desk", 4, 1, None),
    ]
    positions: dict[str, VolunteerPosition] = {}
    default_slots: dict[str, int] = {}
    for name, order, slots, description in position_specs:
        p, _ = await VolunteerPosition.get_or_create(
            name=name, tenant=tenant, defaults={"display_order": order, "is_active": True},
        )
        if description and not p.description:
            p.description = description
            await p.save()
        positions[name] = p
        default_slots[name] = slots

    broadcast_tech = positions["Broadcast Tech"]
    if broadcast_tech.shift_length_minutes is None:
        broadcast_tech.shift_length_minutes = 240
        broadcast_tech.stagger_minutes = 120
        await broadcast_tech.save()

    # The admin on duty: whoever holds an Admin Desk shift gets match alerts
    # (player comments, reschedule requests) instead of every staff member.
    admin_desk = positions["Admin Desk"]
    if not admin_desk.staff_alerts:
        admin_desk.staff_alerts = True
        await admin_desk.save()

    qual_specs = [
        ("proctor_user", "Race Proctor"),
        ("sm_user", "Broadcast Tech"),
        ("player_one", "Check-in Desk"),
        ("player_one", "Admin Desk"),
        ("player_two", "Race Proctor"),
    ]
    for uname, pos_name in qual_specs:
        await VolunteerQualification.get_or_create(
            user=users[uname], position=positions[pos_name], tenant=tenant,
        )

    event_days = [today + timedelta(days=d) for d in range(3)]
    # Windows per volunteer, repeated across every event day. A list rather than
    # one window because a real declaration is not uniform: the last fixture
    # offers the afternoon and blocks the morning, which is the only place
    # ``UNAVAILABLE`` — the status the auto-scheduler must refuse to schedule
    # over — exists on a volunteer.
    avail_specs = {
        "proctor_user": [("08:00", "16:00", VolunteerAvailabilityStatus.PREFERRED)],
        "sm_user": [("12:00", "20:00", VolunteerAvailabilityStatus.AVAILABLE)],
        "player_one": [("08:00", "12:00", VolunteerAvailabilityStatus.AVAILABLE)],
        "player_two": [("16:00", "00:00", VolunteerAvailabilityStatus.AVAILABLE)],
        "player_three": [("08:00", "20:00", VolunteerAvailabilityStatus.AVAILABLE)],
        FULL_RACERS[0]: [("10:00", "18:00", VolunteerAvailabilityStatus.AVAILABLE)],
        FULL_RACERS[1]: [
            ("08:00", "12:00", VolunteerAvailabilityStatus.UNAVAILABLE),
            ("12:00", "20:00", VolunteerAvailabilityStatus.PREFERRED),
        ],
    }
    for uname, windows in avail_specs.items():
        u = users[uname]
        if await VolunteerAvailability.filter(user=u, tenant=tenant).exists():
            continue
        for day in event_days:
            day_str = day.isoformat()
            for start_hhmm, end_hhmm, status in windows:
                starts_at = parse_local_datetime(day_str, start_hhmm)
                ends_at = parse_local_datetime(day_str, end_hhmm)
                if ends_at <= starts_at:
                    ends_at = ends_at + timedelta(days=1)
                await VolunteerAvailability.create(
                    user=u, starts_at=starts_at, ends_at=ends_at, status=status, tenant=tenant,
                )

    blocks = [
        ("Shift 1", "08:00", "12:00"),
        ("Shift 2", "12:00", "16:00"),
        ("Shift 3", "16:00", "20:00"),
        ("Shift 4", "20:00", "00:00"),
    ]
    shift_index: dict[tuple[str, str], VolunteerShift] = {}
    for day in event_days[:2]:
        day_str = day.isoformat()
        for pos_name, pos in positions.items():
            if pos.is_staggered:
                coverage_start = parse_local_datetime(day_str, blocks[0][1])
                coverage_end = parse_local_datetime(day_str, blocks[-1][2])
                if coverage_end <= coverage_start:
                    coverage_end = coverage_end + timedelta(days=1)
                length = timedelta(minutes=pos.shift_length_minutes)
                stagger = timedelta(minutes=pos.stagger_minutes)
                cursor = coverage_start
                while cursor < coverage_end:
                    await VolunteerShift.get_or_create(
                        position=pos, starts_at=cursor, tenant=tenant,
                        defaults={"ends_at": min(cursor + length, coverage_end),
                                  "slots_needed": 1},
                    )
                    cursor += stagger
                continue
            for label, start_hhmm, end_hhmm in blocks:
                starts_at = parse_local_datetime(day_str, start_hhmm)
                ends_at = parse_local_datetime(day_str, end_hhmm)
                if ends_at <= starts_at:
                    ends_at = ends_at + timedelta(days=1)
                shift, _ = await VolunteerShift.get_or_create(
                    position=pos, starts_at=starts_at, label=label, tenant=tenant,
                    defaults={
                        "ends_at": ends_at, "slots_needed": default_slots[pos_name],
                    },
                )
                # Per-shift instructions, distinct from the position's standing
                # description: the volunteer's card renders the two separately.
                if pos_name == "Check-in Desk" and label == "Shift 1" and not shift.notes:
                    shift.notes = (
                        "Report to the info desk 10 minutes early. Radio channel 2. "
                        "Bring a jacket — the door is draughty."
                    )
                    await shift.save()
                shift_index[(day_str, f"{pos_name}|{label}")] = shift

    first_day = event_days[0].isoformat()
    proctor_shift = shift_index.get((first_day, "Race Proctor|Shift 1"))
    if proctor_shift:
        await VolunteerAssignment.get_or_create(
            shift=proctor_shift, user=users["proctor_user"], tenant=tenant,
            defaults={"assigned_by": staff},
        )

    # Hour totals: one volunteer over every tier, one exactly on 8, one short of
    # it, so the Vol. Roster chip renders all three states on a fresh seed.
    # proctor_user's single Race Proctor block above is the below-8 case.
    hours_specs = [
        ("player_two", "Admin Desk", ["Shift 1", "Shift 2", "Shift 3", "Shift 4"]),
        ("player_one", "Check-in Desk", ["Shift 1", "Shift 2"]),
    ]
    for uname, pos_name, labels in hours_specs:
        for label in labels:
            shift = shift_index.get((first_day, f"{pos_name}|{label}"))
            if shift:
                await VolunteerAssignment.get_or_create(
                    shift=shift, user=users[uname], tenant=tenant,
                    defaults={"assigned_by": staff},
                )

    # A deliberately overlapping pair, so the union fold is visible in the app
    # and not only in a unit test: the staggered Broadcast Tech blocks run
    # 08:00-12:00 and 10:00-14:00, which is six hours served, not eight.
    overlap_starts = [
        parse_local_datetime(first_day, "08:00"),
        parse_local_datetime(first_day, "10:00"),
    ]
    for starts_at in overlap_starts:
        shift = await VolunteerShift.get_or_none(
            position=broadcast_tech, starts_at=starts_at, tenant=tenant,
        )
        if shift:
            await VolunteerAssignment.get_or_create(
                shift=shift, user=users[FULL_RACERS[1]], tenant=tenant,
                defaults={"assigned_by": staff},
            )

    second_day = event_days[1].isoformat()
    draft_shift = shift_index.get((second_day, "Check-in Desk|Shift 1"))
    if draft_shift:
        # An unpublished autoscheduler draft: outlined on the coordinator's grid,
        # invisible on the volunteer's page until Publish draft.
        await VolunteerAssignment.get_or_create(
            shift=draft_shift, user=users["player_three"], tenant=tenant,
            defaults={"assigned_by": staff, "auto_generated": True},
        )

    # An upcoming shift on a page a volunteer can open: everything above lands
    # on the seed's first day and has usually finished by the time anyone looks,
    # so My Shifts (and its help screenshots) would show only "no upcoming
    # shifts". Tomorrow's noon Broadcast Tech block, inside sm_user's stated
    # window, with its own note so the card's "For this shift" box renders.
    upcoming_tech = await VolunteerShift.get_or_none(
        position=broadcast_tech, tenant=tenant,
        starts_at=parse_local_datetime(second_day, "12:00"),
    )
    if upcoming_tech:
        if not upcoming_tech.notes:
            upcoming_tech.notes = "Stage B camera desk. Grab a headset from the stream booth on the way in."
            await upcoming_tech.save()
        await VolunteerAssignment.get_or_create(
            shift=upcoming_tech, user=users["sm_user"], tenant=tenant,
            defaults={"assigned_by": staff},
        )

    # A published shift for the staff login, so Volunteer → Full Schedule shows
    # its "your shift" outline to whoever the browser checks sign in as.
    staff_desk = shift_index.get((second_day, "Admin Desk|Shift 3"))
    if staff_desk:
        await VolunteerAssignment.get_or_create(
            shift=staff_desk, user=staff, tenant=tenant,
            defaults={"assigned_by": staff},
        )

    await _seed_arrivals(tenant, positions["Admin Desk"], users, staff, now_utc)

    # Position → role mappings. Race Proctor → PROCTOR lands on proctor_user, who
    # already holds it by hand, so it shows a manual grant left alone; Broadcast
    # Tech → STREAM_MANAGER hands FULL_RACERS[1] a volunteer-sourced role. The
    # rows go through the real reconcile so the UserRole state is the app's own.
    for pos_name, role in (("Race Proctor", Role.PROCTOR), ("Broadcast Tech", Role.STREAM_MANAGER)):
        await VolunteerRoleMapping.get_or_create(
            position=positions[pos_name], app_role=role, tenant=tenant,
        )
    await VolunteerRoleMappingService().reconcile_all(staff)
    print(f"    [{tenant.slug}] volunteers ok")


async def _seed_arrivals(
    tenant: Tenant,
    position: VolunteerPosition,
    users: dict[str, User],
    staff: User,
    now_utc: datetime,
) -> None:
    """A shift starting in a few minutes, so self check-in is visible on any run.

    Like the race rooms, this one is re-pointed at "now" on every run rather
    than created once: a fixed time would have passed by the next day's seed.
    player_two hasn't arrived (sign in as them to see the "I'm here" banner),
    proctor_user said they're running late, player_one checked themselves
    in, and staff called volunteer_only off (credited, no banner). A second
    shift later on has everyone called off, so the Shift Board hatches it. Admin Desk, because it maps to no app role: a Race Proctor shift would
    hand these users PROCTOR through the role mapping below.
    """
    starts_at = (now_utc + timedelta(minutes=15)).replace(second=0, microsecond=0)
    shift, _ = await VolunteerShift.update_or_create(
        position=position, label="Arrival demo", tenant=tenant,
        defaults={"starts_at": starts_at, "ends_at": starts_at + timedelta(hours=2),
                  "slots_needed": 4},
    )
    states = [
        (users["player_two"], {"checked_in_at": None, "checked_in_by": None, "late_minutes": None}),
        (users["proctor_user"], {"checked_in_at": None, "checked_in_by": None, "late_minutes": 20}),
        (users["player_one"], {"checked_in_at": now_utc, "checked_in_by": users["player_one"],
                               "late_minutes": None}),
        (users["volunteer_only"], {"checked_in_at": None, "checked_in_by": None, "late_minutes": None,
                                   "called_off_at": now_utc, "called_off_by": staff,
                                   "called_off_note": "We're overstaffed this afternoon, enjoy the show!"}),
    ]
    for user, state in states:
        await VolunteerAssignment.update_or_create(
            shift=shift, user=user, tenant=tenant,
            defaults={"assigned_by": staff, "acknowledged_at": now_utc, **state},
        )

    dark_starts = starts_at + timedelta(hours=3)
    dark, _ = await VolunteerShift.update_or_create(
        position=position, label="Call-off demo", tenant=tenant,
        defaults={"starts_at": dark_starts, "ends_at": dark_starts + timedelta(hours=2),
                  "slots_needed": 1},
    )
    await VolunteerAssignment.update_or_create(
        shift=dark, user=users["player_two"], tenant=tenant,
        defaults={"assigned_by": staff, "acknowledged_at": now_utc,
                  "called_off_at": now_utc, "called_off_by": staff},
    )
