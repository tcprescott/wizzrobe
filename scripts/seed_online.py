"""Dev-seed fixtures for the online-tournament features (PRs 1-10).

Split out of ``seed_dev.py`` to keep that file under the length guideline. These
functions are called from ``seed_dev.py`` — they assume an open Tortoise
connection and (for the per-tenant helper) an active ``tenant_scope``.

The per-tenant helper owns a **dedicated racetime.gg-managed tournament** rather
than wiring the general-purpose "Wizzrobe Dev Tournament" to a bot. Attaching a
bot flips ``Tournament.is_racetime_enabled``, which hides every on-site proctor
control (check-in, stations, start, finish) on that tournament's matches and
drops them from the Proctor Station board — so a tournament cannot be both the
racetime fixture and the proctor-lifecycle fixture.

What they cover:

- **Presets** (PR 1) — per-tenant ``Preset`` rows, one assigned to the online
  tournament, plus placeholder ``RandomizerCredential`` rows for keyed backends.
- **Provider identities** (PR 2) — two players linked to racetime handles, one to
  a Twitch handle, and an assertion that ``player_three``/``player_four`` stay
  unlinked (the fixtures the account-link probes start from).
- **Racetime bots** (PR 3/4) — platform-level (no tenant FK), one connected and
  one parked in an error state so the ``/platform`` health table shows both.
- **Racetime config + rooms** (PR 3/4/6) — the online tournament's bot +
  auto-open config, its own matches, a room profile, and one race room per room
  lifecycle state.
- **SpeedGaming ETL** (PR 7) and the **Discord Events mirror** (PR 8), both
  hanging off the same online tournament.
- **Async qualifiers** (PR 9/10) live in ``seed_qualifiers.py``, called from here
  so they stay part of the same online fixture set.
"""
from datetime import datetime, timedelta, timezone

from application.randomizer_credentials import all_specs, credentials_for
from application.services.preset_service import PresetService
from application.utils.timezone import now_local
from models import (
    BotStatus,
    DiscordEventSource,
    DiscordScheduledEvent,
    Match,
    MatchHardPresetOptIn,
    MatchPlayers,
    MembershipSource,
    Preset,
    PresetOverride,
    RaceRoomProfile,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeBotTenant,
    RacetimeRoom,
    RandomizerCredential,
    SpeedGamingEpisode,
    SpeedGamingEventLink,
    SyncStatus,
    Tenant,
    TenantMembership,
    Tournament,
    TournamentNotificationPreference,
    TournamentPlayers,
    User,
)
from scripts.seed_qualifiers import seed_qualifiers_for_tenant
from scripts.seed_support import backfill

ONLINE_TOURNAMENT_NAME = "Wizzrobe Online Series"

#: The operational metadata an online tournament actually carries. Kept beside
#: the tournament it describes: ``average_match_duration`` is what the schedule's
#: suggestion engine spaces races by and what the reports' expected-average
#: column reads, and both fall back to a hard-coded 90 minutes when it is NULL —
#: so a dev database could never tell a configured tournament from an unconfigured one.
_ONLINE_TOURNAMENT_META = {
    "tournament_format": "Round-robin groups into a double-elimination bracket",
    "rules_url": "https://example.invalid/wizzrobe/online-series/rules",
    "average_match_duration": 105,
    "max_match_duration": 210,
}


async def link_racetime_identities(users: dict[str, User]) -> None:
    """Link two players to racetime handles (PR 2).

    Deliberately grants no roles. This used to also make ``staff_user`` a global
    SUPER_ADMIN "so /platform is reachable in dev", which left the one user the
    validation loop drives admin views as indistinguishable from a platform
    admin — every staff-vs-super-admin difference silently untestable. The
    dedicated ``super_admin`` fixture (``seed_dev.seed_super_admin``) covers
    /platform instead.
    """
    racetime_links = [
        ("player_one", "aBcDeFg1", "PlayerOne"),
        ("player_two", "hIjKlMn2", "PlayerTwo"),
    ]
    for key, rt_id, rt_name in racetime_links:
        u = users[key]
        if u.racetime_user_id is None:
            u.racetime_user_id = rt_id
            u.racetime_username = rt_name
            u.racetime_linked_at = now_local()
            await u.save()


async def link_twitch_identities(users: dict[str, User]) -> None:
    """Link one player to a Twitch identity, so the Profile page's Connected
    accounts card has a linked *and* an unlinked provider on the same screen.

    The ids avoid ``MockTwitchClient``'s canned set (``2000{1..4}``) on
    purpose, the way the racetime seed does: a seeded user clicking **Link**
    under ``MOCK_TWITCH`` then rebinds to a mock identity instead of colliding
    with another seeded row.
    """
    twitch_links = [
        ("player_one", "seedtw0001", "PlayerOneTV"),
    ]
    for key, tw_id, tw_name in twitch_links:
        u = users[key]
        if u.twitch_user_id is None:
            u.twitch_user_id = tw_id
            u.twitch_username = tw_name
            u.twitch_linked_at = now_local()
            await u.save()


PROBE_UNLINKED_USERS = ("player_three", "player_four")
_PROVIDER_PREFIXES = ("challonge", "twitch", "racetime", "startgg")


async def reset_unlinked_probe_users(users: dict[str, User]) -> None:
    """Return the link-probe fixtures to unlinked, whatever a dev left behind.

    ``player_three`` and ``player_four`` are the only users with no provider
    identity, which is what makes the Connected accounts card's unlinked state
    and the two-user collision sequence reachable. Clicking **Link** as either of
    them — the whole point of the fixture — writes a real row, so a re-seed has
    to clear it or the second run starts from a state the first one did not.
    Re-seeding is exactly when a developer expects fixture state to be restored.
    """
    for key in PROBE_UNLINKED_USERS:
        u = users[key]
        dirty = False
        for prefix in _PROVIDER_PREFIXES:
            if getattr(u, f"{prefix}_user_id") is not None:
                setattr(u, f"{prefix}_user_id", None)
                setattr(u, f"{prefix}_username", None)
                setattr(u, f"{prefix}_linked_at", None)
                dirty = True
        if dirty:
            await u.save()


async def assert_unlinked_probe_users(users: dict[str, User]) -> None:
    """Fail loudly if *the seed itself* linked a probe fixture.

    Runs after every other seed function, against users
    :func:`reset_unlinked_probe_users` already cleared — so anything found here
    was written by the seed in between, which would silently remove the last
    unlinked fixture. A developer's own manual link is not this: it was reset
    before the run started.
    """
    for key in PROBE_UNLINKED_USERS:
        u = users[key]
        await u.refresh_from_db()
        linked = [
            prefix for prefix in _PROVIDER_PREFIXES
            if getattr(u, f"{prefix}_user_id") is not None
        ]
        if linked:
            raise AssertionError(
                f"the seed linked {key}, which must stay unlinked (the dev fixture "
                f"every account-link probe starts from): {', '.join(linked)}"
            )


async def seed_racetime_bots() -> dict[str, RacetimeBot]:
    """Platform-level racetime bots (PR 3/4). Bots have no tenant FK — they are
    managed at ``/platform`` and authorized per tenant. Seed one healthy,
    connected bot plus one per remaining ``BotStatus``, so the /platform health
    table shows every variant it can render."""
    now = now_local()
    alttpr, _ = await RacetimeBot.get_or_create(
        category="alttpr",
        defaults={
            "client_id": "dev_alttpr_client_id",
            "client_secret": "dev_alttpr_client_secret_local_only",
            "name": "ALTTPR Dev Bot",
            "description": "Fixture racetime bot for local dev (MOCK_RACETIME).",
            "is_active": True,
            "status": BotStatus.CONNECTED,
            "status_message": "Connected (mock transport).",
            "last_connected_at": now,
            "last_checked_at": now,
        },
    )
    # One bot per BotStatus, because the /platform health table's whole job is
    # telling them apart: a board that only ever shows connected and error rows
    # cannot show whether the other two render (or sort) correctly.
    parked_bots = [
        ("smw", "SMW Dev Bot", BotStatus.ERROR,
         "Second fixture bot, parked in an error state.",
         "Authentication rejected (fixture)."),
        ("ootr", "OoTR Dev Bot", BotStatus.DISCONNECTED,
         "Configured and authorized, websocket currently down.",
         "Socket closed; retrying (fixture)."),
        ("smz3", "SMZ3 Dev Bot", BotStatus.UNKNOWN,
         "Never connected — the state a freshly added bot sits in.",
         None),
    ]
    for category, name, status, description, status_message in parked_bots:
        await RacetimeBot.get_or_create(
            category=category,
            defaults={
                "client_id": f"dev_{category}_client_id",
                "client_secret": f"dev_{category}_client_secret_local_only",
                "name": name,
                "description": description,
                "is_active": True,
                "status": status,
                "status_message": status_message,
                # An UNKNOWN bot has never been checked; the others have.
                "last_checked_at": None if status is BotStatus.UNKNOWN else now,
            },
        )
    print("  racetime bots ok (global)")
    return {"alttpr": alttpr}


async def seed_online_for_tenant(
    tenant: Tenant,
    staff: User,
    players: list[User],
    bots: dict[str, RacetimeBot],
) -> Tournament:
    """Seed one tenant's racetime.gg-managed tournament and every online fixture
    hanging off it. Must run inside that tenant's ``tenant_scope``.

    Returns the tournament it created so the caller can report/extend it.
    """
    preset = await _seed_presets(tenant, staff)
    profile = await _seed_room_profile(tenant)
    tournament = await _seed_online_tournament(
        tenant, staff, players, preset, bots["alttpr"], profile,
    )
    scheduled, in_progress, finished = await _seed_online_matches(
        tenant, tournament, players,
    )
    await _seed_rooms(tenant, bots["alttpr"], scheduled, in_progress, finished)
    await _seed_held_race(tenant, tournament, bots["alttpr"], players)
    await _seed_speedgaming(tenant, tournament, scheduled)
    await _seed_discord_events(tenant, tournament, scheduled)
    await seed_qualifiers_for_tenant(tenant, preset)
    print(f"    [{tenant.slug}] online tournament ok")
    return tournament


async def _seed_presets(tenant: Tenant, staff: User) -> Preset:
    """Seed-rolling presets (PR 1) plus placeholder randomizer credentials.

    Returns the ALTTPR preset, which the online tournament and the qualifier's
    standard pool both point at."""
    preset, _ = await Preset.get_or_create(
        name="ALTTPR Open", tenant=tenant,
        defaults={
            "randomizer": "alttpr",
            "settings": {"glitches": "none", "goal": "ganon", "mode": "open"},
            "description": "Standard open-mode ALTTPR settings.",
        },
    )
    # The committed ``presets/`` files, imported the way a real community starts:
    # **Import built-ins** on the Presets tab. They are the settings actual events
    # ran (ALTTPR sglive2025/casualboots, OoTR sgl2026, SM Map Rando community race,
    # DK64 in its settings-string form, the Wind Waker SGL 2026 permalink), so the dev preset list holds real
    # multi-randomizer payloads instead of one three-key toy dict — which is what
    # the JSON editor, the payload-size handling and the per-randomizer filter are
    # written against. Idempotent by (randomizer, name) inside the service.
    await PresetService().import_builtins(staff)
    # One preset in the shape **Import from Randomizer** produces: a name and
    # description straight from the upstream catalogue, and settings that are a
    # branch plus a portable settings string rather than a full payload. Seeded
    # directly instead of through ``import_remote_presets`` so the row exists
    # whether or not this run has the DK64 mock queue enabled.
    await Preset.get_or_create(
        name="Season 5 Race Settings", tenant=tenant,
        defaults={
            "randomizer": "dk64r",
            "settings": {"_branch": "stable", "settings_string": "SEEDDEVSTABLE0001"},
            "description": "Medium length settings with a uniquely shuffled seasonal world.",
        },
    )
    # Placeholder randomizer credentials so the keyed backends stay *selectable*
    # in the Presets tab and tournament dialog, and the Randomizer Keys tab is not
    # empty in dev. The default tenant gets all three; the second tenant only the
    # OoT key, so dev demonstrates the per-tenant filter narrowing a selector (and
    # its dk64r preset staying editable without one). Dev rolls go through
    # MOCK_SEEDGEN, so these are never sent upstream.
    # The opt-in "harder" preset the online tournament offers. Same randomizer
    # as the standard one above, which the service requires: whichever preset
    # wins at roll time is what names the randomizer.
    await Preset.get_or_create(
        name="ALTTPR Open — Hard Mode", tenant=tenant,
        defaults={
            "randomizer": "alttpr",
            "settings": {"glitches": "none", "goal": "ganon", "mode": "open",
                         "item_placement": "advanced", "item_pool": "hard"},
            "description": "The open-mode settings with a hard item pool. Players opt into this.",
        },
    )
    wanted = all_specs() if tenant.slug == 'default' else credentials_for('ootr')
    for spec in wanted:
        await RandomizerCredential.get_or_create(
            tenant=tenant, randomizer=spec.randomizer, key=spec.key,
            defaults={"value": f"placeholder-dev-{spec.randomizer}-{spec.key}"},
        )
    return preset


async def _seed_online_tournament(
    tenant: Tenant,
    staff: User,
    players: list[User],
    preset: Preset,
    bot: RacetimeBot,
    profile: RaceRoomProfile,
) -> Tournament:
    """The racetime.gg-managed fixture tournament (PR 3) — the only seeded
    tournament with a ``racetime_bot``."""
    # Authorize the tenant to use the bot so live-race room opening (PR 10),
    # which resolves an authorized bot, has one to pick.
    await RacetimeBotTenant.get_or_create(
        bot=bot, tenant=tenant, defaults={"is_active": True},
    )
    tournament, _ = await Tournament.get_or_create(
        name=ONLINE_TOURNAMENT_NAME, tenant=tenant,
        defaults={
            "description": "Online fixture — races run in racetime.gg rooms.",
            "is_active": True,
            "players_per_match": 2,
            **_ONLINE_TOURNAMENT_META,
        },
    )
    await backfill(tournament, **_ONLINE_TOURNAMENT_META)
    await tournament.admins.add(staff)
    # staff_user runs this one but switched its match alerts off, so the
    # Profile → Notifications opt-out shows both states on a fresh seed.
    await TournamentNotificationPreference.update_or_create(
        user=staff, tournament=tournament, tenant=tenant, defaults={"staff_alerts": False},
    )
    for p in players:
        await TournamentPlayers.get_or_create(
            tournament=tournament, user=p, tenant=tenant,
        )
    if tournament.preset_id is None:
        tournament.preset = preset
    # The harder preset players can privately opt into instead. Without one on a
    # dev tournament the whole opt-in surface is invisible, which is what the
    # feature does for a community that has not configured it — so the fixture
    # has to supply it for the column to exist at all.
    if tournament.hard_preset_id is None:
        tournament.hard_preset = await Preset.get_or_none(
            name="ALTTPR Open — Hard Mode", tenant=tenant,
        )
    # The room profile the auto-opener applies to this tournament's rooms. It was
    # seeded but attached to nothing, so the "tournament → profile → room
    # settings" resolution every opened room goes through had no dev fixture —
    # rooms were only ever opened on the built-in defaults.
    if tournament.race_room_profile_id is None:
        tournament.race_room_profile = profile
    tournament.racetime_bot = bot
    tournament.racetime_auto_create_rooms = True
    tournament.room_open_minutes_before = 15
    tournament.racetime_default_goal = "Beat the game"
    # An online tournament is raced on racetime.gg, so a real one turns this on:
    # entrants are expected to have linked a racetime account. Nothing enforces
    # it yet (it is stored and edited, not checked), so this is the fixture for
    # the *configured* state — and player_three/player_four are enrolled here
    # while deliberately unlinked, which is the roster the check will meet.
    tournament.require_racetime_link = True
    # The room bot runs these rooms: invites, seed post, chat commands, and
    # results that wait for staff when they need a look. With player_three and
    # player_four unlinked, the board shows the "not linked" chip too.
    tournament.racetime_bot_runs_room = True
    await tournament.save()
    return tournament


async def _seed_online_matches(
    tenant: Tenant, tournament: Tournament, players: list[User],
) -> tuple[Match, Match, Match]:
    """One match per online lifecycle state, with its own roster.

    Never stamps ``seated_at``: an online race is not checked in at a station —
    the race room drives the lifecycle, which is exactly why these matches live
    apart from the on-site ones.
    """
    now = now_local()

    async def make_match(
        title: str,
        offset_hours: float,
        p1: User,
        p2: User,
        *,
        started: bool = False,
        finished: bool = False,
    ) -> Match:
        scheduled_at = now + timedelta(hours=offset_hours)
        match, created = await Match.get_or_create(
            title=title, tournament=tournament, tenant=tenant,
            defaults={"scheduled_at": scheduled_at, "is_stream_candidate": True},
        )
        if not created:
            return match
        if started or finished:
            match.started_at = scheduled_at
        if finished:
            match.finished_at = scheduled_at + timedelta(hours=1, minutes=36)
            match.confirmed_at = match.finished_at + timedelta(minutes=5)
        await match.save()
        for rank, player in enumerate([p1, p2], 1):
            await MatchPlayers.get_or_create(
                match=match, user=player, tenant=tenant,
                defaults={"finish_rank": rank if finished else None},
            )
        return match

    scheduled = await make_match("Online Scheduled Race", 2, players[0], players[1])
    # One more upcoming race, so the board carries both halves of the opt-in and
    # not just the agreed one: this is the state a player sees while their own
    # answer is in and they cannot tell whether anyone else's is.
    half = await make_match("Online Race — One Opt-In", 4, players[1], players[2])
    # Staff's override, the state where the players' own control steps aside.
    forced_hard = await make_match(
        "Online Race — Staff Set Hard", 6, players[0], players[3],
    )
    in_progress = await make_match(
        "Online Race In Progress", -1, players[2], players[3], started=True,
    )
    finished = await make_match(
        "Online Race Finished", -3, players[0], players[2], started=True, finished=True,
    )
    # Hard-preset opt-ins, covering the three states the column renders:
    # unanimous (both players in, still rollable), a single opt-in waiting on the
    # other player, and a staff override that disregards both. The third match is
    # left with no rows at all, which is the fourth state — the plain offer.
    for match, opted_in in (
        (scheduled, (players[0], players[1])),
        (half, (players[1],)),
    ):
        for player in opted_in:
            await MatchHardPresetOptIn.get_or_create(
                match=match, user=player, tenant=tenant,
            )
    if in_progress.preset_override is None:
        in_progress.preset_override = PresetOverride.STANDARD
        await in_progress.save()
    # The other direction of the same control. Both are seeded because they read
    # differently on the board and in the DM, and the forced-hard one sits on an
    # upcoming match so its control is still live to look at.
    if forced_hard.preset_override is None:
        forced_hard.preset_override = PresetOverride.HARD
        await forced_hard.save()

    return scheduled, in_progress, finished


async def _seed_room_profile(tenant: Tenant) -> RaceRoomProfile:
    """The reusable room settings a tournament opens its rooms with (PR 4/6).

    Returned rather than merely created, so the online tournament can point at
    it — a profile attached to nothing is a profile the room-opening path never
    resolves.
    """
    profile, _ = await RaceRoomProfile.get_or_create(
        name="Bracket Match", tenant=tenant,
        defaults={
            "goal": "Beat the game",
            "invitational": True,
            "unlisted": False,
            "auto_start": True,
            "allow_comments": True,
            "allow_midrace_chat": True,
            "allow_non_entrant_chat": False,
            "chat_message_delay": 0,
            "start_delay": 15,
            "time_limit": 24,
            "streaming_required": True,
        },
    )
    return profile


async def _seed_rooms(
    tenant: Tenant,
    bot: RacetimeBot,
    scheduled: Match,
    in_progress: Match,
    finished: Match,
) -> None:
    """One race room per room state (PR 4/6), and finish times on the finished
    race's players.

    Keyed on the globally-unique slug with ``update_or_create``: the open room's
    slug predates the tournament split, when it hung off the general-purpose
    tournament's match, so a dev database seeded before the split is re-pointed
    at the online match rather than left dangling on an on-site one.
    """
    room_specs = [
        ("dev-room", "Scheduled Race — Bracket", RaceRoomStatus.OPEN, scheduled),
        ("dev-room-live", "Race In Progress — Bracket", RaceRoomStatus.IN_PROGRESS, in_progress),
        ("dev-room-done", "Finished Race — Bracket", RaceRoomStatus.FINISHED, finished),
        # A cancelled room, detached from any match. ``RacetimeRoom.match`` is a
        # OneToOne, so a room cannot share a match with a live one — and this is
        # the shape the real thing leaves behind anyway: the room is cancelled,
        # the match it was opened for is deleted or re-opened elsewhere, and
        # ``SET_NULL`` leaves the record standing on its own.
        ("dev-room-void", "Cancelled Room — Bracket", RaceRoomStatus.CANCELLED, None),
    ]
    for suffix, room_name, status, match in room_specs:
        await RacetimeRoom.update_or_create(
            slug=f"alttpr/{suffix}-{tenant.slug}",
            defaults={
                "tenant": tenant,
                "bot": bot,
                "category": "alttpr",
                "room_name": room_name,
                "status": status,
                "match": match,
                "opened_at": (
                    match.scheduled_at - timedelta(minutes=15)
                    if match is not None and match.scheduled_at else None
                ),
            },
        )

    for mp, secs in zip(
        await MatchPlayers.filter(match=finished).order_by("finish_rank"),
        (5400, 5760), strict=False,
    ):
        if mp.finish_time is None:
            mp.finish_time = secs
            await mp.save()


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
        # player_three never linked racetime, so the bot couldn't place them.
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


async def _seed_speedgaming(
    tenant: Tenant, tournament: Tournament, scheduled_match: Match,
) -> None:
    """SpeedGaming ETL fixtures (PR 7): an event link, a synced episode, and a
    sourced match with a mixed real+placeholder roster so the admin SpeedGaming
    tab shows sync health and the schedule shows a match with the read-only
    'Synced from SpeedGaming' badge. Placeholder ``speedgaming_id`` is namespaced
    per tenant (it is globally unique).

    The link is keyed on ``(tenant, event_slug)`` with ``update_or_create``, and
    an already-materialized sourced match is re-pointed, so a dev database seeded
    before the online tournament existed moves its SG fixtures across instead of
    leaving them on the on-site tournament.
    """
    now = datetime.now(timezone.utc)

    link, _ = await SpeedGamingEventLink.update_or_create(
        event_slug=f"wiz-{tenant.slug}", tenant=tenant,
        defaults={
            "tournament": tournament,
            "content_type": None,
            "active": True,
            "sync_interval_minutes": 15,
            "lookahead_hours": 72,
            "last_synced_at": now,
            "last_status": "ok",
        },
    )

    episode, _ = await SpeedGamingEpisode.get_or_create(
        sg_episode_id=f"dev-{tenant.slug}-1", tenant=tenant,
        defaults={
            "event_link": link,
            "title": "Synced Bracket — Round 1",
            "scheduled_at": now + timedelta(days=1),
            "payload": {"id": f"dev-{tenant.slug}-1", "title": "Synced Bracket — Round 1"},
            "content_hash": "devseedhash",
            "sync_status": SyncStatus.SYNCED,
            "synced_at": now,
        },
    )

    # One episode per remaining SyncStatus. The SpeedGaming tab exists to show
    # sync *health*, and health is the non-synced rows: an episode discovered but
    # not yet materialized, one a lifecycle guard held back, one whose upstream
    # vanished, and one whose transform failed with the reason attached.
    unhealthy_specs = [
        (2, "Discovered — Not Yet Materialized", SyncStatus.PENDING, None),
        (3, "Held Back — Match Already Started", SyncStatus.SKIPPED, None),
        (4, "Withdrawn Upstream", SyncStatus.CANCELLED, None),
        (5, "Transform Failed", SyncStatus.ERROR,
         "players[1].displayName missing from the upstream payload"),
    ]
    for index, title, status, sync_error in unhealthy_specs:
        await SpeedGamingEpisode.get_or_create(
            sg_episode_id=f"dev-{tenant.slug}-{index}", tenant=tenant,
            defaults={
                "event_link": link,
                "title": title,
                "scheduled_at": now + timedelta(days=1, hours=index),
                "payload": {"id": f"dev-{tenant.slug}-{index}", "title": title},
                "content_hash": f"devseedhash{index}",
                "sync_status": status,
                "sync_error": sync_error,
                # Only a row that reached the app has a synced_at.
                "synced_at": now if status is SyncStatus.SKIPPED else None,
            },
        )

    sourced_match = await Match.filter(speedgaming_episode=episode).first()
    if sourced_match is None:
        sourced_match = await Match.create(
            tenant=tenant, tournament=tournament,
            scheduled_at=now + timedelta(days=1),
            title="Synced Bracket — Round 1",
            speedgaming_episode=episode,
        )
    elif sourced_match.tournament_id != tournament.id:
        sourced_match.tournament = tournament
        await sourced_match.save()

    placeholder, _ = await User.get_or_create(
        speedgaming_id=f"sg_dev_{tenant.slug}",
        defaults={
            "username": f"sg_dev_{tenant.slug}",
            "display_name": "Unmatched SG Player",
            "is_placeholder": True,
            "is_active": False,
            "dm_notifications": False,
        },
    )

    # A real player alongside the placeholder (from the scheduled match's roster).
    real_player = await MatchPlayers.filter(match=scheduled_match).prefetch_related("user").first()
    roster = [placeholder]
    if real_player is not None:
        roster.append(real_player.user)
    for user in roster:
        # Membership first, then enrol, then add to the match — the order the
        # real ETL uses. Without the membership the placeholder is a person on
        # this community's schedule who does not belong to it, which the
        # membership-coverage audit reports as a gap.
        await TenantMembership.get_or_create(user=user, tenant=tenant)
        if user is placeholder:
            await TenantMembership.filter(user=user, tenant=tenant).update(
                source=MembershipSource.IMPORT,
            )
        await TournamentPlayers.get_or_create(tournament=tournament, user=user, tenant=tenant)
        await MatchPlayers.get_or_create(match=sourced_match, user=user, tenant=tenant)


async def _seed_discord_events(
    tenant: Tenant, tournament: Tournament, scheduled_match: Match,
) -> None:
    """Discord Events mirror fixtures (PR 8): opt the tournament into the mirror
    and seed one already-mirrored :class:`DiscordScheduledEvent` for the scheduled
    match, so the admin Discord Events tab shows an opted-in tournament and a
    non-empty mirrored-events table. ``discord_event_id`` is namespaced per tenant
    (it is globally unique). Requires the tenant to have a linked guild (seed_dev
    sets one).

    Keyed on that globally-unique ``discord_event_id`` rather than the source
    match: a dev database seeded before the tournament split already holds this
    id against the on-site tournament's match, and re-using it under a new
    ``source_id`` would collide.
    """
    if not tournament.discord_events_enabled:
        tournament.discord_events_enabled = True
        tournament.discord_event_duration_minutes = 90
        await tournament.save()

    guild_id = tenant.discord_guild_id
    if guild_id is None:
        return

    # A stable synthetic Discord event id per tenant (well outside real snowflakes).
    discord_event_id = 3900000000000000000 + tenant.id
    await DiscordScheduledEvent.update_or_create(
        discord_event_id=discord_event_id,
        defaults={
            "tenant": tenant,
            "guild_id": guild_id,
            "source_type": DiscordEventSource.MATCH,
            "source_id": scheduled_match.id,
            "title": scheduled_match.title or "Online Scheduled Race",
            "scheduled_at": scheduled_match.scheduled_at,
            "content_hash": "devseedhash",
            "synced_at": datetime.now(timezone.utc),
        },
    )
