#!/usr/bin/env python3
"""Challonge mirror fixtures for the dev seed (split out of seed_dev.py).

Must run inside the target tenant's ``tenant_scope`` — called from
``seed_for_tenant``. Idempotent like the rest of the seed.
"""
from datetime import date, datetime, timedelta

from models import (
    ChallongeApiUsage,
    ChallongeConnection,
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    Match,
    MatchPlayers,
    Tenant,
    Tournament,
    User,
)


async def seed_challonge_for_tenant(
    tenant: Tenant,
    users: dict[str, User],
    tournament: Tournament,
    staff: User,
    finished_match: Match,
    now_utc: datetime,
    today: date,
) -> None:
    # Player identity is global (one-time capture); the connection/bracket
    # mirror is per tenant.
    for uname, challonge_uid, challonge_name in [
        ("player_one", "cu_1001", "playerone"),
        ("player_two", "cu_1002", "playertwo"),
    ]:
        u = users[uname]
        if u.challonge_user_id is None:
            u.challonge_user_id = challonge_uid
            u.challonge_username = challonge_name
            u.challonge_linked_at = now_utc
            await u.save()

    if not tournament.challonge_tournament_id:
        tournament.challonge_tournament_id = f"cht_dev_{tenant.slug}"
        tournament.challonge_tournament_url = f"https://challonge.com/wizzrobe_{tenant.slug}"
        tournament.challonge_last_synced_at = now_utc
        # Mirrors what ChallongeService.link_tournament does: a bracket-run
        # tournament schedules only its own matchups. Set here because this seed
        # writes the FK directly rather than going through the service.
        tournament.allow_player_match_requests = False
        # Named fields only: ``tournament`` was loaded before seed_payouts set
        # the pool, and a full-row save would write that stale NULL back.
        await tournament.save(update_fields=[
            'challonge_tournament_id', 'challonge_tournament_url',
            'challonge_last_synced_at', 'allow_player_match_requests',
        ])
    # A two-stage bracket mid-groups, so Admin -> Challonge offers its next stage
    # action (Finalize groups and start bracket). Healed on an older dev DB too.
    if tournament.challonge_state is None:
        tournament.challonge_state = "group_stages_underway"
        tournament.challonge_group_stage = True
        await tournament.save(update_fields=['challonge_state', 'challonge_group_stage'])

    # A near-expiry token so the service-health board / tenant subset (PR 5)
    # has a live credential-warning to render. Heal older rows that predate the
    # expiry column too, so a re-seed against an existing dev DB still shows it.
    challonge_expiry = now_utc + timedelta(days=2)
    conn, _ = await ChallongeConnection.get_or_create(
        challonge_username="wizzrobe_service", tenant=tenant,
        defaults={
            "access_token": "dev-access-token-not-real",
            "refresh_token": "dev-refresh-token-not-real",
            "scopes": "me tournaments:read tournaments:write participants:write",
            "token_expires_at": challonge_expiry,
            "connected_by": staff,
        },
    )
    if conn.token_expires_at is None:
        conn.token_expires_at = challonge_expiry
        await conn.save()
    # Lets players sign themselves up for a Challonge bracket from the
    # Tournaments tab; healed onto a connection seeded before that existed.
    if "participants:write" not in (conn.scopes or "").split():
        conn.scopes = f"{conn.scopes or ''} participants:write".strip()
        await conn.save()
    # Remembered communities for the Challonge card's chip row (Settings → Integrations): one in use by
    # nothing (removable) beside the tournament-derived ones.
    config = dict(tenant.config or {})
    if not config.get("challonge_communities") or "challonge_auto_sync_minutes" not in config:
        config.setdefault("challonge_communities", [f"{tenant.slug}-community"])
        # Every hour: what SG Live runs, so Settings → Integrations shows the cost line.
        config.setdefault("challonge_auto_sync_minutes", 60)
        tenant.config = config
        await tenant.save(update_fields=["config"])

    participants: dict[str, ChallongeParticipant] = {}
    participant_specs = [
        ("cp_1", "Player One", "cu_1001", users["player_one"]),
        ("cp_2", "Player Two", "cu_1002", users["player_two"]),
        ("cp_3", "Player Three", None, users["player_three"]),
        ("cp_4", "Player Four", None, users["player_four"]),
        # Added to the bracket by name with nobody assigned yet: the state the
        # Entrants dialog exists to fix.
        ("cp_5", "Name-Only Entrant", None, None),
    ]
    for cp_id, name, challonge_uid, user in participant_specs:
        # An entrant with a user but no Challonge account can only have been
        # assigned by hand; heal rows seeded before the flag existed.
        manual = user is not None and challonge_uid is None
        part, _ = await ChallongeParticipant.get_or_create(
            tournament=tournament, challonge_participant_id=cp_id, tenant=tenant,
            defaults={"name": name, "challonge_user_id": challonge_uid, "user": user,
                      "user_assigned_manually": manual},
        )
        if part.user_assigned_manually != manual:
            part.user_assigned_manually = manual
            await part.save()
        participants[cp_id] = part

    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_1", tenant=tenant,
        defaults={
            "round": 1, "state": ChallongeMatchState.COMPLETE,
            "participant1": participants["cp_1"], "participant2": participants["cp_3"],
            "winner_participant": participants["cp_1"], "match": finished_match,
        },
    )
    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_2", tenant=tenant,
        defaults={
            "round": 1, "state": ChallongeMatchState.OPEN,
            "participant1": participants["cp_2"], "participant2": participants["cp_4"],
        },
    )
    # Staff booked cm_2's two players by hand (New match) instead of from the
    # bracket: the row on Not booked yet that suggests linking it.
    hand_booked, created = await Match.get_or_create(
        tournament=tournament, tenant=tenant, title="Booked by hand",
        defaults={"scheduled_at": now_utc + timedelta(days=1, hours=2)},
    )
    if created:
        for uname in ("player_two", "player_four"):
            await MatchPlayers.create(match=hand_booked, user=users[uname], tenant=tenant)
    # Open, but one side is the name-only entrant: the "Needs account" row on
    # Match Schedule's Not booked yet view, whose Fix entrant opens Entrants.
    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_4", tenant=tenant,
        defaults={
            "round": 1, "state": ChallongeMatchState.OPEN,
            "participant1": participants["cp_1"], "participant2": participants["cp_5"],
        },
    )
    # Booked in Wizzrobe, then reported on Challonge's own site before anyone
    # ran it: the "Played on Challonge" row and its Needs you card.
    played_elsewhere, created = await Match.get_or_create(
        tournament=tournament, tenant=tenant, title="Played on Challonge",
        defaults={"scheduled_at": now_utc + timedelta(hours=1)},
    )
    if created:
        for uname in ("player_three", "player_four"):
            await MatchPlayers.create(match=played_elsewhere, user=users[uname], tenant=tenant)
    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_5", tenant=tenant,
        defaults={
            "round": 1, "state": ChallongeMatchState.COMPLETE, "group_stage": True,
            "participant1": participants["cp_3"], "participant2": participants["cp_4"],
            "winner_participant": participants["cp_4"], "match": played_elsewhere,
        },
    )
    # Round 2, waiting on round 1: PENDING is what most of a freshly mirrored
    # bracket looks like, and both its slots are unknown — which is also the only
    # mirrored row with no participants to render.
    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_3", tenant=tenant,
        defaults={"round": 2, "state": ChallongeMatchState.PENDING},
    )

    # Elimination rounds waiting on players. cm_6 is on Not booked yet ›
    # Players TBD ("Player Two or Player Four vs Winner of B"); cm_7 staff
    # booked ahead, and Player One is already in it; the third booking lost
    # its matchup to a reset ("Bracket link lost" on the run sheet).
    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_6", tenant=tenant,
        defaults={"round": 3, "state": ChallongeMatchState.PENDING, "identifier": "F",
                  "slot1_prereq": "cm_2", "slot1_label": "Winner of A", "slot2_label": "Winner of B"},
    )
    ahead, created = await Match.get_or_create(
        tournament=tournament, tenant=tenant, title="Booked ahead",
        defaults={"scheduled_at": now_utc + timedelta(days=2),
                  "bracket_placeholders": ["Player Two or Player Four"]},
    )
    if created:
        await MatchPlayers.create(match=ahead, user=users["player_one"], tenant=tenant)
    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_7", tenant=tenant,
        defaults={"round": 3, "state": ChallongeMatchState.PENDING, "identifier": "G",
                  "participant1": participants["cp_1"], "slot2_prereq": "cm_2", "match": ahead},
    )
    await Match.get_or_create(
        tournament=tournament, tenant=tenant, title="Booked ahead, bracket reset",
        defaults={"scheduled_at": now_utc + timedelta(days=2, hours=2),
                  "bracket_placeholders": ["Winner of C", "Winner of D"], "bracket_link_lost": True},
    )

    # Mid-groups, so all of them are group matches; the round schedule bounds the
    # group stage and plans the elimination rounds Challonge hasn't created yet
    # (Admin -> Tournaments -> Round schedule…, and the window line on My Schedule).
    await ChallongeMatch.filter(
        tournament=tournament, challonge_match_id__in=["cm_1", "cm_2", "cm_3", "cm_4"], group_stage=False,
    ).update(group_stage=True)
    config = dict(tournament.config or {})
    if "round_schedule" not in config:
        day = now_utc.replace(minute=0, second=0, microsecond=0)
        config["round_schedule"] = {
            "group": {
                "opens_at": (day - timedelta(days=1)).isoformat(),
                "closes_at": (day + timedelta(days=3)).isoformat(),
            },
            "e1": {
                "opens_at": (day + timedelta(days=3)).isoformat(),
                "closes_at": (day + timedelta(days=4)).isoformat(),
                "daily_from": "18:00", "daily_until": "23:00",
            },
            "e0": {"closes_at": (day + timedelta(days=5)).isoformat(), "best_of": 3},
        }
        tournament.config = config
        await tournament.save(update_fields=["config"])
    elif "best_of" not in (config["round_schedule"].get("e0") or {}):
        config["round_schedule"]["e0"] = {**(config["round_schedule"].get("e0") or {}), "best_of": 3}
        tournament.config = config
        await tournament.save(update_fields=["config"])

    await _seed_best_of_set(tenant, users, tournament, participants, now_utc)

    await _seed_open_challonge_bracket(tenant, users, now_utc)

    usage_period = today.strftime("%Y-%m")
    await ChallongeApiUsage.get_or_create(
        period=usage_period, tenant=tenant, defaults={"request_count": 42},
    )
    print(f"    [{tenant.slug}] challonge ok")


async def _seed_best_of_set(
    tenant: Tenant, users: dict[str, User], tournament: Tournament,
    participants: dict, now_utc: datetime,
) -> None:
    """A best-of-3 set booked as three matches: game 1 played, game 2 booked,
    game 3 "if needed" (run sheet "Game 2 of 3", BracketSetService)."""
    first, created = await Match.get_or_create(
        tournament=tournament, tenant=tenant, title="Best of 3, game 1",
        defaults={"scheduled_at": now_utc - timedelta(hours=2), "set_game": 1, "set_best_of": 3,
                  "started_at": now_utc - timedelta(hours=2), "finished_at": now_utc - timedelta(hours=1),
                  "confirmed_at": now_utc - timedelta(minutes=50)},
    )
    if created:
        for uname, rank in (("player_one", 1), ("player_two", 2)):
            await MatchPlayers.create(match=first, user=users[uname], finish_rank=rank, tenant=tenant)
    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_8", tenant=tenant,
        defaults={"round": 3, "state": ChallongeMatchState.OPEN, "identifier": "H",
                  "participant1": participants["cp_1"], "participant2": participants["cp_2"],
                  "match": first},
    )
    for number in (2, 3):
        game, created = await Match.get_or_create(
            tournament=tournament, tenant=tenant, title=f"Best of 3, game {number}",
            defaults={"scheduled_at": now_utc + timedelta(minutes=20 + 80 * (number - 2)),
                      "set_game": number, "set_best_of": 3, "set_first": first},
        )
        if created:
            for uname in ("player_one", "player_two"):
                await MatchPlayers.create(match=game, user=users[uname], tenant=tenant)


async def _seed_open_challonge_bracket(
    tenant: Tenant, users: dict[str, User], now_utc: datetime,
) -> None:
    """A Challonge bracket still taking entrants, for the Tournaments tab's
    Sign up: player_one is already on it, player_two (linked) can sign up, and
    an unlinked player sees "Link Challonge to sign up"."""
    open_bracket, _ = await Tournament.get_or_create(
        name="Challonge Open", tenant=tenant,
        defaults={
            "description": "Single elimination, best of 3. The bracket runs on Challonge.",
            "is_active": True,
            "challonge_tournament_id": f"cht_open_{tenant.slug}",
            "challonge_tournament_url": f"https://challonge.com/wizzrobe_open_{tenant.slug}",
            "challonge_state": "pending",
            "challonge_last_synced_at": now_utc,
            "allow_player_match_requests": False,
            "signups_close_at": now_utc + timedelta(days=5),
        },
    )
    for cp_id, name, challonge_uid, user in [
        ("cpo_1", "playerone", "cu_1001", users["player_one"]),
        ("cpo_2", "Walk-in Entrant", None, None),
    ]:
        await ChallongeParticipant.get_or_create(
            tournament=open_bracket, challonge_participant_id=cp_id, tenant=tenant,
            defaults={"name": name, "challonge_user_id": challonge_uid, "user": user},
        )
