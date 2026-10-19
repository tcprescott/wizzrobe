#!/usr/bin/env python3
"""Seed the dev database with test fixtures across **two** tenants.

Run from the project root:
    poetry run python scripts/seed_dev.py

Idempotent — safe to re-run; existing records are left unchanged.
Requires the schema to already exist (run ./start.sh dev or aerich upgrade first).

Users are global (no tenant FK): the same people log in everywhere and hold
per-tenant roles/memberships. Everything else is tenant-scoped, so the fixtures
are seeded once **per tenant** with ``tenant`` threaded through every scoped
create — giving leak tests and manual dev cross-tenant data from day one. Tenant
A adopts the ``default`` slug the migration backfills (created empty on a fresh
dev DB); tenant B is a second community.
"""
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from tortoise import Tortoise

from application.services.audit_service import AuditActions
from application.services.feature_flag_service import FeatureFlagService
from application.services.mcp_auth_service import READ_SCOPE, WRITE_SCOPE
from application.tenant_context import tenant_scope
from application.utils.timezone import now_local, parse_local_datetime
from models import (
    AccommodationRequest,
    AccommodationStatus,
    ApiToken,
    ApiTokenOrigin,
    AuditLog,
    BrowserErrorIgnoreRule,
    EmergencyContact,
    FeatureFlag,
    FeatureFlagGroup,
    JoinRequestStatus,
    LogLevelOverride,
    Match,
    MatchComment,
    MatchCommentKind,
    McpOAuthClient,
    NotificationMode,
    NotificationPreference,
    NotificationPreset,
    NotificationRecipient,
    NotificationSetting,
    PlayerAvailability,
    ProfileNudgeDismissal,
    RacetimeBot,
    Role,
    Stage,
    Tenant,
    TenantFeatureFlag,
    TenantJoinRequest,
    Tournament,
    TournamentPlayers,
    TriforceText,
    User,
    UserRole,
    VolunteerAvailabilityStatus,
    WatchLayoutVote,
    WebPushSubscription,
)
from scripts.seed_access import seed_access_for_tenant
from scripts.seed_brackets import seed_brackets_for_tenant
from scripts.seed_branding import seed_logo_for_tenant
from scripts.seed_challonge import seed_challonge_for_tenant
from scripts.seed_check_in import seed_check_in_for_tenant, seed_check_in_identities
from scripts.seed_community_events import seed_community_events_for_tenant
from scripts.seed_crew import seed_crew_for_tenant
from scripts.seed_discord import seed_discord_for_tenant
from scripts.seed_equipment import seed_equipment_for_tenant
from scripts.seed_fledgling import seed_fledgling_tenant
from scripts.seed_match_day import seed_match_day_for_tenant
from scripts.seed_matches import seed_matches_for_tenant
from scripts.seed_observability import seed_observability_for_tenant
from scripts.seed_online import (
    assert_unlinked_probe_users,
    link_racetime_identities,
    link_twitch_identities,
    reset_unlinked_probe_users,
    seed_online_for_tenant,
    seed_racetime_bots,
)
from scripts.seed_onsite import seed_onsite_for_tenant
from scripts.seed_password import seed_password_sign_in
from scripts.seed_payouts import seed_matcherino_handles, seed_payouts_for_tenant
from scripts.seed_play_in import seed_play_in_for_tenant
from scripts.seed_preferences import seed_notification_channels, seed_table_preferences
from scripts.seed_sent_messages import seed_sent_messages
from scripts.seed_staff_requests import seed_staff_request_dms
from scripts.seed_startgg import seed_startgg_for_tenant
from scripts.seed_support import (
    RACER_SPECS,
    USER_SPECS,
    backfill,
    fixture_discord_id,
)
from scripts.seed_tokens import seed_tokens_for_tenant
from scripts.seed_venue import seed_venue_for_tenant
from scripts.seed_volunteers import seed_volunteers_for_tenant

# Two dev tenants. Tenant A reuses the migration's ``default`` slug; on a fresh
# dev DB the backfill creates it empty and this adopts it. Tenant B carries a
# custom ``domain`` so host-based routing is exercisable locally: browsers
# resolve ``*.localhost`` to 127.0.0.1, so http://second.localhost:8000/ serves
# the second community with no /etc/hosts edit.
TENANT_SPECS = [
    ("default", "Wizzrobe Default", 1000000000000000001, "a", None),
    ("second", "Second Community", 1000000000000000002, "b", "second.localhost:8000"),
]


# Per-user display-timezone preferences. Only consulted by a community that
# leaves the choice to its members (tenant 'second'), so these are what make the
# profile-preference branch reachable in dev — and what makes a still-hardcoded
# Eastern render obvious, since none of them is Eastern. Everyone else keeps
# NULL, which means "detect from my device".
TIMEZONE_SPECS = {
    'player_one': 'Europe/Berlin',
    'player_two': 'Asia/Tokyo',
    'player_three': 'America/Los_Angeles',
    # A half-hour offset, which is what catches code assuming whole-hour zones.
    'player_four': 'Asia/Kolkata',
}


async def seed_timezone_preferences(users: dict[str, User]) -> None:
    """Give a few fixtures an explicit timezone; leave the rest on detection."""
    for username, zone in TIMEZONE_SPECS.items():
        u = users.get(username)
        if u is not None and u.timezone != zone:
            u.timezone = zone
            await u.save()


async def seed_users() -> dict[str, User]:
    """Create the global (tenant-agnostic) users. Roles are granted per tenant."""
    specs = [(name, display) for name, display in USER_SPECS]
    specs += [(name, display) for name, display, _ in RACER_SPECS]
    users: dict[str, User] = {}
    for username, display_name in specs:
        discord_id = fixture_discord_id(username)
        # Convergence for a dev database seeded before ids were derived: the row
        # is found by its username and re-pointed, so its matches, roles and
        # memberships follow it instead of a second copy of every fixture
        # appearing beside the first.
        legacy = await User.get_or_none(username=username)
        if legacy is not None and legacy.discord_id != discord_id:
            legacy.discord_id = discord_id
            await legacy.save()
        u, created = await User.get_or_create(
            discord_id=discord_id,
            defaults={"username": username, "display_name": display_name, "is_active": True},
        )
        # The discord id is the identity key; the name is fixture metadata, so a
        # renamed or renumbered fixture has to converge on re-seed rather than
        # leave the previous occupant of that id wearing the wrong name in every
        # dev picker.
        if not created and (u.username != username or u.display_name != display_name):
            u.username, u.display_name = username, display_name
            await u.save()
        users[username] = u
    await seed_timezone_preferences(users)
    await seed_matcherino_handles(users)
    await seed_check_in_identities(users)
    await link_racetime_identities(users)
    await link_twitch_identities(users)
    # Before anything else writes a provider id: clicking Link as one of these
    # fixtures is what they exist for, so a re-seed restores them.
    await reset_unlinked_probe_users(users)
    await seed_password_sign_in(users)
    print("  users ok (global)")
    return users



async def seed_super_admin(users: dict[str, User]) -> None:
    """Grant the global ``SUPER_ADMIN`` role (``UserRole`` with ``tenant=NULL``).

    Granted outside ``seed_for_tenant`` because the row is deliberately
    tenant-less, and to no other seeded user — the point of the fixture is a
    platform admin whose authority in each community comes *only* from this role,
    so ``/platform`` and the cross-tenant admin paths are reachable in dev.
    """
    await UserRole.get_or_create(
        user=users['super_admin'], role=Role.SUPER_ADMIN, tenant=None,
        defaults={'granted_by': None},
    )
    print('  super admin ok (global, tenant=NULL)')


async def seed_log_levels(users: dict[str, User]) -> None:
    """One stored log-level override, so /platform's Logging section shows a
    set row next to the inherited ones. httpx at WARNING quiets one INFO line per
    outbound call, which is a setting a dev box is glad of anyway."""
    await LogLevelOverride.get_or_create(
        logger_name='httpx',
        defaults={'level': 'WARNING', 'updated_by': users['super_admin']},
    )
    print('  log level override ok (global)')


async def seed_browser_monitoring(users: dict[str, User]) -> None:
    """One super-admin ignore rule, so /platform's Monitoring tab shows an
    added rule (with its ⋮) below the built-in ones. Settings stay at their
    defaults: no row means on, replay on, everything sent."""
    await BrowserErrorIgnoreRule.get_or_create(
        pattern="Cannot read properties of null (reading 'focus')", match='message',
        defaults={'created_by': users['super_admin']},
    )
    print('  browser ignore rule ok (global)')


async def seed_feature_groups() -> dict:
    """Create the demo feature-flag groups (tiers). Idempotent by name.

    Migration 31 already creates 'Default' (empty, is_default) and 'Online
    Tournaments'; this also ensures a 'Full Access' tier for dev and returns all
    three keyed by role.
    """
    default, _ = await FeatureFlagGroup.get_or_create(
        name='Default',
        defaults={
            'description': 'Live fallback for tenants with no group assigned.',
            'flags': [], 'is_default': True,
        },
    )
    # update_or_create for the two demo tiers: migration 31 seeds 'Online
    # Tournaments' with the flags that existed *then*, so a get_or_create would
    # leave any flag added (or retired) since out of the tier this claims to
    # define. Group contents are a super-admin's editable data in production —
    # the migration is right not to rewrite them there — but in dev the seed is
    # the fixture set and gets to be authoritative.
    online, _ = await FeatureFlagGroup.update_or_create(
        name='Online Tournaments',
        defaults={'flags': ['async_qualifiers', 'racetime_rooms', 'speedgaming_etl']},
    )
    full, _ = await FeatureFlagGroup.update_or_create(
        name='Full Access',
        defaults={'flags': [f.value for f in FeatureFlag]},
    )
    print('  feature groups ok (Default / Online Tournaments / Full Access)')
    return {'default': default, 'online': online, 'full': full}


async def assign_feature_group(tenant: Tenant, groups: dict) -> None:
    """Assign a dev tenant to a tier plus one demo override.

    Tenant A → Full Access (everything live), with one feature the community has
    switched OFF (sticky enable override). Tenant B → Online Tournaments, with one
    extra feature force-granted as a per-tenant availability exception. Together
    they exercise group-derived, community-disabled, and override states.
    """
    # update_or_create, not get_or_create: migration 32 backfills every
    # ``established`` flag as available+enabled for pre-existing tenants, so a
    # get_or_create finds that row and silently keeps the demo state it means to
    # set — the community-opted-out case then never exists in dev.
    if tenant.slug == 'default':
        tenant.feature_group = groups['full']
        await tenant.save()
        await TenantFeatureFlag.update_or_create(
            tenant=tenant, flag=FeatureFlag.TRIFORCE_TEXTS.value,
            defaults={'available': None, 'enabled': False},  # community opted out
        )
    else:
        tenant.feature_group = groups['online']
        await tenant.save()
        await TenantFeatureFlag.update_or_create(
            tenant=tenant, flag=FeatureFlag.EQUIPMENT.value,
            defaults={'available': True, 'enabled': None},  # per-tenant exception
        )
    print(f"    [{tenant.slug}] feature tier ok")


async def seed_for_tenant(
    tenant: Tenant, users: dict[str, User], bots: dict[str, RacetimeBot]
) -> None:
    """Seed all tenant-scoped fixtures for one tenant.

    ``tenant`` is threaded through every scoped create/get_or_create (both the
    lookup and the row) so the data is isolated per tenant — the application
    threads tenant explicitly rather than through a global manager, and this
    script mirrors that contract.
    """
    with tenant_scope(tenant.id):
        # --- Memberships + roles (scripts/seed_access.py) ----------------
        await seed_access_for_tenant(tenant, users)

        # --- Stages, stations, system config (scripts/seed_venue.py) -----
        # The three-day window the venue hours, the volunteer fixtures and
        # player availability all describe.
        today = now_local().date()
        event_days = [today + timedelta(days=d) for d in range(3)]
        await seed_venue_for_tenant(tenant, today, event_days)

        # Tournament
        staff = users["staff_user"]
        # The operational metadata a real tournament carries. Not decoration:
        # ``average_match_duration`` is what the suggestion engine spaces matches
        # by and what the reports' expected-average column reads (both fall back
        # to a hard-coded 90 when it is NULL, so dev could never tell the two
        # apart), and the format/rules links are what the player-facing header
        # renders.
        # The Tournaments tab reads three signup states off these columns, so the
        # seed has to produce all three: this one is **open** (window running
        # now), Wizzrobe Cup is **closed** (see seed_onsite), and the qualifier
        # below has not opened yet. Its description is markdown because the card
        # renders it through the safe-markdown renderer, and a plain sentence
        # would never exercise that path.
        dev_tournament_meta = {
            "tournament_format": "Double elimination, best of 3",
            "bracket_url": f"https://challonge.com/wizzrobe_{tenant.slug}",
            "rules_url": "https://example.invalid/wizzrobe/rules",
            "average_match_duration": 90,
            "max_match_duration": 150,
            "triforce_access_message": (
                "Triforce texts are a **donation incentive**. "
                "[Donate here](https://example.invalid/wizzrobe/donate) and "
                "staff will unlock submissions for you."
            ),
            "randomizer_notes": (
                "Rolled from the `casualboots` preset on "
                "[alttpr.com](https://alttpr.com/en/randomizer).\n\n"
                "- Standard state, boots start\n"
                "- Defeat Ganon, 7/7 crystals\n"
            ),
        }
        dev_tournament_meta["signups_open_at"] = now_local() - timedelta(days=3)
        dev_tournament_meta["signups_close_at"] = now_local() + timedelta(days=14)
        tournament, _ = await Tournament.get_or_create(
            name="Wizzrobe Dev Tournament", tenant=tenant,
            defaults={
                "description": (
                    "Fixture tournament for local dev.\n\n"
                    "## Format\n\n"
                    "Double elimination, best of 3. Finals are best of 5.\n\n"
                    "- Sign up before the window closes\n"
                    "- Staff schedule your matches from the player pool\n"
                ),
                "seed_generator": "alttpr",
                "is_active": True,
                "players_per_match": 2,
                **dev_tournament_meta,
            },
        )
        await backfill(tournament, **dev_tournament_meta)
        # A short name, so the run sheets and the My Schedule timeline show the
        # abbreviation; the other fixtures leave it blank to show the fallback.
        await backfill(tournament, short_name="Dev Tourney")
        # An inactive tournament, so the Tournaments board shows its Inactive chip
        # and the row menu's Delete (offered only once a tournament is inactive).
        await Tournament.get_or_create(
            name="Wizzrobe Archive Cup", tenant=tenant,
            defaults={"is_active": False, "players_per_match": 2},
        )
        # A test tournament: the Tournaments board shows its Test chip, and the
        # event-info tournament list leaves it out.
        await Tournament.get_or_create(
            name="Challonge Integration Test", tenant=tenant,
            defaults={"is_active": True, "is_test": True, "players_per_match": 2},
        )
        await tournament.admins.add(staff)
        await tournament.crew_coordinators.add(staff)
        await tournament.crew_coordinators.add(users["cc_user"])
        # Heal the fixture that the duplicated discord id left behind: cc_user
        # used to collide with `outsider` on id ...011, so a dev database seeded
        # before the fix holds this coordinator grant against the account that is
        # supposed to belong to no community at all.
        await tournament.crew_coordinators.remove(users["outsider"])

        # The general-purpose fixture tournament is deliberately **on-premises**:
        # its matches are the proctor-lifecycle fixtures, and attaching a
        # racetime.gg bot hides every on-site control on them (and drops them
        # from the Proctor Station board). Racetime lives on its own tournament
        # now (scripts/seed_online.py), so a dev database seeded before the split
        # gets that wiring cleared here.
        if tournament.racetime_bot_id is not None or tournament.discord_events_enabled:
            tournament.racetime_bot = None
            tournament.racetime_auto_create_rooms = False
            tournament.discord_events_enabled = False
            await tournament.save()

        players = [users[k] for k in ("player_one", "player_two", "player_three", "player_four")]
        for p in players:
            await TournamentPlayers.get_or_create(tournament=tournament, user=p, tenant=tenant)
        print(f"    [{tenant.slug}] tournament ok")

        # Matches — one per lifecycle state, plus extra fixtures for variety
        stage1 = await Stage.get(name="Stage 1", tenant=tenant)
        stage2 = await Stage.get(name="Stage 2", tenant=tenant)
        stage3 = await Stage.get(name="Stage 3", tenant=tenant)
        now = now_local()

        fixtures = await seed_matches_for_tenant(
            tenant, tournament, staff, players,
            {"stage1": stage1, "stage2": stage2, "stage3": stage3}, now,
        )
        finished_match = fixtures["finished"]
        disputed_match = fixtures["disputed"]
        seed = fixtures["seed"]

        # --- Online tournament (scripts/seed_online.py) ----------------------
        # Its own racetime.gg-managed tournament with its own matches, kept apart
        # from the tournament above: a racetime bot hides every on-site control
        # on the matches it owns, so the two fixtures cannot share a tournament.
        await seed_online_for_tenant(tenant, staff, players, bots)

        # --- Second on-site tournament (scripts/seed_onsite.py) --------------
        # A distinct venue event with its own "tournament days" override and one
        # match per step of the proctor's workflow.
        onsite = await seed_onsite_for_tenant(tenant, staff, players, today, now, stage1, stage3)
        # A field longer than the Tournaments card's name preview, so its
        # "Show all" is reachable in dev.
        for name, _display, _full in RACER_SPECS:
            await TournamentPlayers.get_or_create(tournament=onsite, user=users[name], tenant=tenant)
        print(f"    [{tenant.slug}] on-site tournament ok")

        # --- Prize payouts (scripts/seed_payouts.py) -------------------------
        await seed_payouts_for_tenant(tenant, players)

        # --- Group play-in races (scripts/seed_play_in.py) -------------------
        # Staff-run races of ten, seeding the bracket above — the only rosters
        # longer than two, and the only match holding finishers and forfeits
        # together. They belong to this tournament because that is the bracket
        # they feed.
        racers = players + [users[name] for name, _display, _full in RACER_SPECS]
        await seed_play_in_for_tenant(tenant, tournament, staff, racers, now)

        # --- One realistic match day (scripts/seed_match_day.py) -------------
        # Density, not states: fifteen ordinary bracket matches across the three
        # stages so the schedule and the stage-utilisation report have a real day
        # to draw. Everything it creates is deliberately unremarkable.
        await seed_match_day_for_tenant(
            tenant, tournament, racers, [stage1, stage2, stage3], today, now,
        )

        # --- Crew signups (scripts/seed_crew.py) ----------------------------
        sm = users["sm_user"]
        proctor = users["proctor_user"]
        await seed_crew_for_tenant(
            tenant, staff, sm, proctor, players, fixtures, now,
        )
        print(f"    [{tenant.slug}] crew ok")

        # --- Volunteer scheduling (scripts/seed_volunteers.py) ---------------
        now_utc = datetime.now(timezone.utc)
        await seed_volunteers_for_tenant(tenant, users, staff, today, now_utc)

        # --- Player availability --------------------------------------------
        # Same three-day window the venue hours and the volunteer fixtures use.
        # Player availability is opt-out, so the rows are blocks and preferences;
        # player_four gets none on purpose — that is the default state, fully
        # available, which the suggestion service and the heatmap must both show.
        player_avail_specs = {
            "player_one": ("08:00", "12:00", VolunteerAvailabilityStatus.UNAVAILABLE),
            "player_two": ("18:00", "22:00", VolunteerAvailabilityStatus.PREFERRED),
            "player_three": ("20:00", "23:00", VolunteerAvailabilityStatus.UNAVAILABLE),
        }
        for uname, (start_hhmm, end_hhmm, status) in player_avail_specs.items():
            u = users[uname]
            if await PlayerAvailability.filter(user=u, tenant=tenant).exists():
                continue
            for day in event_days:
                day_str = day.isoformat()
                starts_at = parse_local_datetime(day_str, start_hhmm)
                ends_at = parse_local_datetime(day_str, end_hhmm)
                if ends_at <= starts_at:
                    ends_at = ends_at + timedelta(days=1)
                await PlayerAvailability.create(
                    user=u, starts_at=starts_at, ends_at=ends_at, status=status, tenant=tenant,
                )
        print(f"    [{tenant.slug}] player availability ok")

        # --- Equipment lending (scripts/seed_equipment.py) -------------------
        equipment = await seed_equipment_for_tenant(tenant, users, staff, now)

        # --- Event check-in (scripts/seed_check_in.py) ----------------------
        await seed_check_in_for_tenant(tenant, users)

        # --- API + room tokens (scripts/seed_tokens.py) -------------------
        await seed_tokens_for_tenant(tenant, staff, now)


        # --- Triforce texts --------------------------------------------------
        triforce_specs = [
            ("player_one", "You found the Triforce of Courage!", "Player One", True),
            ("player_two", "The hero's spirit lives on.", "Player Two", None),
            ("player_three", "not a real submission", "Player Three", False),
        ]
        for uname, text, author, approved in triforce_specs:
            u = users[uname]
            if await TriforceText.filter(tournament=tournament, user=u, text=text, tenant=tenant).exists():
                continue
            await TriforceText.create(
                tournament=tournament, user=u, text=text, author=author,
                approved=approved,
                approved_by=staff if approved is not None else None,
                approved_at=now if approved is not None else None,
                tenant=tenant,
            )
        print(f"    [{tenant.slug}] triforce texts ok")

        # --- Discord role mappings (scripts/seed_discord.py) ------------
        await seed_discord_for_tenant(tenant, tournament, users)

        # --- Challonge mirror (scripts/seed_challonge.py) --------------------
        await seed_challonge_for_tenant(
            tenant, users, tournament, staff, finished_match, now_utc, today,
        )

        # --- start.gg mirror (scripts/seed_startgg.py) -----------------------
        await seed_startgg_for_tenant(tenant, users, staff, now_utc)

        # --- Native brackets (scripts/seed_brackets.py) ----------------------
        # Seeded through BracketService, which enforces FeatureFlag.BRACKETS, so a
        # tenant whose tier lacks the feature gets no bracket rows — which is the
        # coherent fixture state anyway (bracket data in a community that cannot
        # see brackets was only ever confusing). Tenant A carries the flag, so
        # every bracket model is still covered.
        if await FeatureFlagService().is_enabled(FeatureFlag.BRACKETS):
            await seed_brackets_for_tenant(tenant, tournament, users)
        else:
            print(f"    [{tenant.slug}] brackets skipped (feature not live)")


        # --- Webhooks + telemetry (scripts/seed_observability.py) -------------
        await seed_observability_for_tenant(tenant, staff, finished_match, now_utc, users)

        # --- Community events (scripts/seed_community_events.py) -------------
        await seed_community_events_for_tenant(tenant, users, now_utc)

        # --- ADA accommodation requests ---------------------------------------
        # One in each state, so the staff sub-tab, its
        # "Show withdrawn" toggle and the Users-table filter all have something
        # to show. Written directly (no flag check) like the feedback rows.
        # player_four's is Arranged and then changed by the member, so the
        # "Changed since arranged" chip, the before/now dialog and the action
        # count (New + Acknowledged + changed = 3) all show on a fresh seed.
        accommodation_specs = [
            ("player_one", AccommodationStatus.NEW,
             "Step-free access to the stage, please.", None),
            ("player_two", AccommodationStatus.ARRANGED,
             "A chair with back support at my station.",
             "Venue confirmed a chair for station 3."),
            ("sm_user", AccommodationStatus.ACKNOWLEDGED,
             "Seat near an exit during stream shifts.", "Talked to venue; confirming Friday."),
            ("player_three", AccommodationStatus.WITHDRAWN, None,
             "Asked about quiet room; withdrawn after the schedule changed."),
        ]
        for uname, status, details, notes in accommodation_specs:
            await AccommodationRequest.get_or_create(
                tenant=tenant, user=users[uname],
                defaults={"status": status, "details": details, "staff_notes": notes},
            )
        await AccommodationRequest.get_or_create(
            tenant=tenant, user=users["player_four"],
            defaults={
                "status": AccommodationStatus.ARRANGED,
                "details": "Extra time between matches, and a seat away from the speakers.",
                "staff_notes": "Scheduled with 30 minutes between matches.",
                "changed_since_arranged": True,
                "arranged_details": "Extra time between matches.",
            },
        )
        print(f"    [{tenant.slug}] ADA accommodation requests ok (one per status)")

        # --- Emergency contacts -----------------------------------------------
        # player_two's has been opened by staff (the profile shows "last
        # opened"); player_one's hasn't. Everyone else has none, so the staff
        # dialog's empty state shows too.
        await EmergencyContact.get_or_create(
            tenant=tenant, user=users["player_two"],
            defaults={
                "name": "Jordan Rivera", "relationship": "Sibling", "phone": "+1 555 0142",
                "last_viewed_at": now_utc - timedelta(hours=2), "last_viewed_by": staff,
            },
        )
        await EmergencyContact.get_or_create(
            tenant=tenant, user=users["player_one"],
            defaults={"name": "Sam Okafor", "relationship": "Parent", "phone": "+44 20 7946 0958"},
        )
        print(f"    [{tenant.slug}] emergency contacts ok")

        # --- Audit log -------------------------------------------------------
        # One row per action a staff member actually takes on a match day, so the
        # Audit tab's action filter has more than one value to filter by.
        audit_specs = [
            (staff, AuditActions.TOURNAMENT_CREATED,
             {"tournament_id": tournament.id, "name": tournament.name}),
            (staff, AuditActions.MATCH_CREATED,
             {"match_id": finished_match.id, "title": finished_match.title}),
            (staff, AuditActions.MATCH_SEED_ROLLED,
             {"match_id": finished_match.id, "randomizer": "alttpr",
              "preset": None, "seed_url": seed.seed_url}),
            (staff, AuditActions.MATCH_FINISHED, {"match_id": finished_match.id}),
            (staff, AuditActions.MATCH_FLAGGED_FOR_REVIEW,
             {"match_id": disputed_match.id}),
            (staff, AuditActions.USER_ROLE_GRANTED,
             {"user_id": proctor.id, "role": Role.PROCTOR.value}),
            (staff, AuditActions.EQUIPMENT_CHECKED_OUT,
             {"equipment_id": equipment["Capture Card A"].id, "borrower_id": users["player_one"].id}),
            # The detail shapes the Audit Log report renders differently: a JSON
            # setting diffed per key, a previous_/current pair, a self-edit, a
            # stage resolved to its name, and a counts-only sync.
            (staff, AuditActions.MATCH_STAGE_ASSIGNED,
             {"match_id": finished_match.id, "stage_id": stage1.id}),
            (staff, AuditActions.SYSTEM_CONFIG_UPDATED,
             {"key": "tournament_hours_by_date",
              "old_value": json.dumps({"2026-10-22": {"open": "08:00", "close": "23:00"},
                                       "2026-10-23": {"open": "08:00", "close": "23:00"}}),
              "new_value": json.dumps({"2026-10-22": {"open": "08:00", "close": "23:59"},
                                       "2026-10-23": {"open": "08:00", "close": "23:59"}})}),
            (users["player_one"], AuditActions.USER_SELF_PROFILE_UPDATED,
             {"changed_fields": {"pronouns": "they/them"}, "target_user_id": users["player_one"].id}),
            (users["player_one"], AuditActions.PROFILE_NUDGE_DISMISSED,
             {"check": "challonge", "tenant_id": tenant.id}),
            (staff, AuditActions.ACCOMMODATION_STATUS_CHANGED,
             {"previous_status": "acknowledged", "status": "arranged", "request_id": 1,
              "user_id": users["player_one"].id}),
            (staff, AuditActions.CHECK_IN_EVENT_SYNCED,
             {"added": 3, "updated": 0, "withdrawn": 0, "total": 42, "event_id": 1}),
        ]
        # Per-row, not "only when the log is empty": anything else that writes an
        # audit row first — a preset import, a bracket created through its
        # service — would otherwise suppress this whole fixture set.
        for actor, action, details in audit_specs:
            payload = json.dumps(details, sort_keys=True)
            if not await AuditLog.filter(action=action, details=payload, tenant=tenant).exists():
                await AuditLog.create(
                    user=actor, action=action, details=payload, tenant=tenant,
                )
        print(f"    [{tenant.slug}] audit log ok")

        # A *decided* join request, so every state of the model exists in dev: the
        # pending one lives in 'fledgling' (the staff queue), this denied one is
        # the re-openable case — asking again updates this row rather than
        # appending a second — and the approved one is the ordinary outcome, the
        # record of how most members actually got in. The denial is fresh, so
        # 'outsider' sees the door's "you can ask again from …" cooldown state.
        await TenantJoinRequest.get_or_create(
            user=users['outsider'], tenant=tenant,
            defaults={
                'status': JoinRequestStatus.DENIED,
                'message': 'Can I get in?',
                'decided_by': staff,
                'decided_at': now_local(),
            },
        )
        await TenantJoinRequest.get_or_create(
            user=users['volunteer_only'], tenant=tenant,
            defaults={
                'status': JoinRequestStatus.APPROVED,
                'message': 'I commentate for a couple of events and would like to help.',
                'decided_by': staff,
                'decided_at': now_local() - timedelta(days=3),
            },
        )
        # Kept fresh on every run, not just the first: the cooldown door only
        # shows while the decline is under a week old.
        await TenantJoinRequest.filter(
            user=users['outsider'], tenant=tenant, status=JoinRequestStatus.DENIED,
        ).update(decided_at=now_utc)
        print(f"    [{tenant.slug}] join requests ok")



async def seed_mcp_oauth(users: dict[str, User]) -> None:
    """Seed a registered MCP client and two deterministic dev OAuth tokens.

    Global, like the users above: an OAuth grant belongs to no community. The
    tokens let /api-validation and manual curl runs exercise ``/mcp`` without
    driving the whole browser-based authorization flow, exactly as the dev PAT
    strings do for the REST API. Non-secret fixtures; only hashes are stored.

    Two of them, because the surface a token sees depends on the consent
    decision behind it: the read-only bearer is served the read tools alone,
    the writing one is served those plus the match writes. A single seeded
    token would leave half the server unreachable from a dev loop.

    ``McpAuthorizationCode`` is deliberately not seeded — see below.
    """
    client, _ = await McpOAuthClient.get_or_create(
        client_id="devseed-local-client",
        defaults={
            "client_name": "Dev Seed MCP Client",
            "redirect_uris": ["http://127.0.0.1:6274/oauth/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    staff = users["staff_user"]
    grants = (
        ("Dev Seed MCP Token", "wizzrobe_mcp_devseed_local_only_do_not_use",
         "wizzrobe_mcpref_devseed_local_only_do_not_use", True, READ_SCOPE),
        ("Dev Seed MCP Write Token", "wizzrobe_mcp_devseedwrite_local_only_do_not_use",
         "wizzrobe_mcpref_devseedwrite_local_only_do_not_use", False,
         f"{READ_SCOPE} {WRITE_SCOPE}"),
    )
    for name, oauth_bearer, refresh_bearer, read_only, scope in grants:
        if not await ApiToken.filter(user=staff, name=name).exists():
            now = datetime.now(timezone.utc)
            await ApiToken.create(
                user=staff, name=name, tenant=None,
                oauth_client=client, origin=ApiTokenOrigin.OAUTH.value,
                read_only=read_only, scope=scope,
                token_hash=hashlib.sha256(oauth_bearer.encode()).hexdigest(),
                token_prefix=oauth_bearer[:17],
                expires_at=now + timedelta(days=3650),
                refresh_token_hash=hashlib.sha256(refresh_bearer.encode()).hexdigest(),
                refresh_expires_at=now + timedelta(days=3650),
            )
        kind = "read-only" if read_only else "write"
        print(f"  mcp oauth ok (global; {kind} dev bearer: {oauth_bearer})")


# seed-exempt: McpAuthorizationCode — ephemeral single-use rows minted mid-flow
# and consumed seconds later; a seeded one would always be stale/expired.


async def seed_profile_reminders(tenant: Tenant, users: dict[str, User]) -> None:
    """Profile reminder rules on the second community, and one dismissal in each.

    Rules only go on ``second``, so the banner shows up somewhere without filling
    every /ui-validation screenshot of the default community. There, pronouns is
    dismissible and staff_user has dismissed it, and the timezone rule is
    mandatory (it applies because that community follows each member's own
    clock). In ``default`` the dismissal outlives a rule that is switched off,
    which is the state a community that tried the feature and turned it off is in.
    """
    if tenant.slug == 'second':
        rules = {'rules': [
            {'check': 'pronouns', 'dismissible': True, 'note': None},
            {'check': 'timezone', 'dismissible': False,
             'note': 'Match times are booked in your timezone, so we need it set.'},
        ]}
        if (tenant.config or {}).get('profile_nudges') != rules:
            config = dict(tenant.config or {})
            config['profile_nudges'] = rules
            tenant.config = config
            await tenant.save()
    await ProfileNudgeDismissal.get_or_create(
        tenant=tenant, user=users['staff_user'], check='pronouns',
    )


async def seed_notification_settings(tenant: Tenant, users: dict[str, User]) -> None:
    """A handful of Admin → Notifications overrides, and one player's own choices, on the default community.

    Rows exist only where staff chose something other than the catalogue
    default, so each one here differs from it: together they show every
    recipient column and every mode as "changed" on the settings page. The
    second community keeps the defaults, which is what most communities see.
    """
    if tenant.slug != 'default':
        return
    for notice, recipient, mode in (
        ('match_confirmed', NotificationRecipient.WATCHERS, NotificationMode.OFF),
        ('match_scheduled', NotificationRecipient.FOLLOWERS, NotificationMode.OFF),
        ('match_finished', NotificationRecipient.PLAYERS, NotificationMode.SILENT),
        ('matchup_ready', NotificationRecipient.PLAYERS, NotificationMode.PING),
        ('match_checked_in', NotificationRecipient.CREW, NotificationMode.SILENT),
        ('shift_reminder', NotificationRecipient.VOLUNTEERS, NotificationMode.SILENT),
        ('player_comment', NotificationRecipient.ON_DUTY, NotificationMode.SILENT),
        ('crew_signup', NotificationRecipient.ADMINS, NotificationMode.SILENT),
        ('volunteer_late', NotificationRecipient.STAFF, NotificationMode.OFF),
        ('welcome', NotificationRecipient.MEMBERS, NotificationMode.SILENT),
    ):
        await NotificationSetting.update_or_create(
            tenant=tenant, notice=notice, recipient=recipient,
            defaults={'mode': mode, 'updated_by': users['staff_user']},
        )

    # Profile → Notifications: one player on each preset, two of them with
    # per-message changes (one an opt-in message turned on) so the Customize
    # list shows changed rows.
    for username, preset, overrides in (
        ('player_one', NotificationPreset.NEEDS_ME,
         {'match_finished:players': 'off', 'match_scheduled:watchers': 'off',
          'opponent_acknowledged:players': 'ping'}),
        ('player_two', NotificationPreset.QUIET, {}),
        ('player_three', NotificationPreset.EVERYTHING, {'match_confirmed:players': 'silent'}),
    ):
        await NotificationPreference.update_or_create(
            tenant=tenant, user=users[username], defaults={'preset': preset, 'overrides': overrides},
        )

async def seed_watch_layout_votes(tenant: Tenant) -> None:
    """A few rows in the retired layout-trial table, which the seed-coverage test still counts."""
    for session_id, layout, vote in (
        ('seed-browser-1', 'one_page', 'up'),
        ('seed-browser-1', 'rail', 'down'),
        ('seed-browser-2', 'tabs', 'up'),
        ('seed-browser-3', 'rail', None),
    ):
        await WatchLayoutVote.get_or_create(
            tenant=tenant, session_id=session_id, layout=layout, defaults={'vote': vote},
        )


async def seed_match_comments(tenant: Tenant, users: dict[str, User]) -> None:
    """One comment of each kind on an upcoming match, so the sheet's thread,
    its tags and the run sheet's counts all show in a dev environment."""
    match = await Match.filter(
        tenant=tenant, started_at__isnull=True, scheduled_at__isnull=False,
        players__user=users['player_two'],
    ).order_by('scheduled_at').first()
    if match is None:
        return
    for author, kind, body in (
        ('player_two', MatchCommentKind.PLAYER,
         "My flight lands at 12:20, so I might be **ten minutes late**."),
        ('staff_user', MatchCommentKind.STAFF_PRIVATE,
         "Hold the slot. Swap with the next match if they're not here in time."),
        ('proctor_user', MatchCommentKind.STAFF_TO_PLAYERS,
         "You're on station 4, by the window."),
    ):
        await MatchComment.get_or_create(
            tenant=tenant, match=match, author=users[author], kind=kind, body=body,
        )


async def seed_push_devices(users: dict[str, User]) -> None:
    """One phone with device notifications on; ``.invalid`` so a push can't land."""
    ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) Version/18.4 Mobile/15E148 Safari/604.1'
    await WebPushSubscription.get_or_create(
        endpoint='https://push.example.invalid/seed/player_one-iphone',
        defaults={'user': users['player_one'], 'p256dh': 'seed', 'auth': 'seed', 'user_agent': ua},
    )


async def seed_all() -> None:
    """Seed everything into the already-initialized ORM connection.

    Split from ``seed()`` so the pytest suite can run the full seed against its
    own in-memory connection — see tests/test_seed_coverage.py.
    """
    users = await seed_users()
    await seed_super_admin(users)
    await seed_log_levels(users)
    await seed_browser_monitoring(users)
    await seed_table_preferences(users)
    await seed_mcp_oauth(users)
    await seed_push_devices(users)
    await seed_notification_channels(users)
    bots = await seed_racetime_bots()
    groups = await seed_feature_groups()
    for slug, name, guild_id, _label, domain in TENANT_SPECS:
        tenant, created = await Tenant.get_or_create(
            slug=slug,
            defaults={"name": name, "discord_guild_id": guild_id, "domain": domain},
        )
        # The migration backfills the ``default`` tenant with the guild id
        # from config (NULL on a fresh dev DB); give it a dev guild so the
        # role-mapping fixtures below have a non-null guild to attach to.
        if tenant.discord_guild_id is None:
            tenant.discord_guild_id = guild_id
            await tenant.save()
        # Idempotently adopt the custom domain (e.g. on a pre-existing dev DB).
        if domain and tenant.domain != domain:
            tenant.domain = domain
            await tenant.save()
        # Give the "second" community a custom brand palette so /ui-validation
        # and dev environments exercise the per-tenant theme path — the Ocean
        # preset, which reads clearly against the default gold/ember.
        if slug == 'second':
            from application.services.tenant_theme_service import THEME_PRESETS
            theme = dict(THEME_PRESETS['Ocean'])
            if (tenant.config or {}).get('theme') != theme:
                config = dict(tenant.config or {})
                config['theme'] = theme
                tenant.config = config
                await tenant.save()
        # One community of each timezone mode, so both paths are reachable in a
        # dev environment without touching the admin UI first. Tenant A pins
        # Eastern (the on-site SpeedGaming Live shape, and what the migration
        # gives every existing community); tenant B follows each member's own
        # clock with a non-US default, which is the shape online tournaments
        # need and the one that catches "still hardcoded to Eastern".
        tz_settings = (
            {'mode': 'pinned', 'name': 'America/New_York'} if slug == 'default'
            else {'mode': 'user', 'name': 'Europe/London'}
        )
        if (tenant.config or {}).get('timezone') != tz_settings:
            config = dict(tenant.config or {})
            config['timezone'] = tz_settings
            tenant.config = config
            await tenant.save()
        print(f"  tenant '{slug}' ({'created' if created else 'exists'}, id={tenant.id})")
        # The default community shows an uploaded SVG logo; the second keeps the
        # colour square, so both header shapes are reachable.
        if slug == 'default':
            await seed_logo_for_tenant(tenant)
        # Tier first: the per-tenant fixtures below consult the live flags (a
        # service that enforces a flag refuses to seed data for a tenant that
        # lacks the feature), so availability has to be settled before seeding.
        await assign_feature_group(tenant, groups)
        await seed_for_tenant(tenant, users, bots)
        await seed_profile_reminders(tenant, users)
        await seed_notification_settings(tenant, users)
        await seed_watch_layout_votes(tenant)
        await seed_match_comments(tenant, users)
        await seed_staff_request_dms(tenant, users)
        await seed_sent_messages(tenant, users)
    await seed_fledgling_tenant(users, groups)
    # Last, because the per-tenant Challonge mirror above also links identities:
    # the unlinked fixtures have to be unlinked after everything that could link
    # them has run.
    await assert_unlinked_probe_users(users)


async def seed() -> None:
    # Lazy, both of them: building TORTOISE_ORM requires DB_* env vars, and
    # importing this module must stay env-free so tests can import seed_all.
    # load_dotenv() at import time was not env-free — it pushed the dev .env
    # (MOCK_DISCORD, MOCK_SEEDGEN) into os.environ for the whole pytest worker,
    # so any later test on that worker silently ran in mock mode.
    from dotenv import load_dotenv
    load_dotenv()

    from application.utils.environment import is_production
    if is_production():
        # The seed plants fixed, published bearer and room tokens for every
        # tenant it creates; against a production database that is a staff
        # credential anyone can read off GitHub.
        raise SystemExit('Refusing to seed: ENVIRONMENT is production.')

    from migrations.tortoise_config import TORTOISE_ORM

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        await seed_all()
    finally:
        await Tortoise.close_connections()
        _discard_unsent()
    print("Seeding complete.")


def _discard_unsent() -> None:
    """Drop the DMs and event deliveries the seed queued for a worker it never runs.

    Starting a bracket queues each matchup-ready DM; with no queue worker in
    this process they were left un-awaited and Python printed a
    ``RuntimeWarning: coroutine ... was never awaited`` per send at exit.
    """
    from application.events import dispatch_queue
    from application.services.discord import discord_queue

    dropped = discord_queue.discard_pending() + dispatch_queue.discard_pending()
    if dropped:
        print(f"  {dropped} queued notification(s) not sent: the seed runs without the bot.")


if __name__ == "__main__":
    asyncio.run(seed())
