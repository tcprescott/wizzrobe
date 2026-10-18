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
    if not config.get("challonge_communities"):
        config["challonge_communities"] = [f"{tenant.slug}-community"]
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
    # Open, but one side is the name-only entrant: the "Needs account" row on
    # Match Schedule's Not booked yet view, whose Fix entrant opens Entrants.
    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_4", tenant=tenant,
        defaults={
            "round": 1, "state": ChallongeMatchState.OPEN,
            "participant1": participants["cp_1"], "participant2": participants["cp_5"],
        },
    )
    # Round 2, waiting on round 1: PENDING is what most of a freshly mirrored
    # bracket looks like, and both its slots are unknown — which is also the only
    # mirrored row with no participants to render.
    await ChallongeMatch.get_or_create(
        tournament=tournament, challonge_match_id="cm_3", tenant=tenant,
        defaults={"round": 2, "state": ChallongeMatchState.PENDING},
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
            "e0": {"closes_at": (day + timedelta(days=5)).isoformat()},
        }
        tournament.config = config
        await tournament.save(update_fields=["config"])

    await _seed_open_challonge_bracket(tenant, users, now_utc)

    usage_period = today.strftime("%Y-%m")
    await ChallongeApiUsage.get_or_create(
        period=usage_period, tenant=tenant, defaults={"request_count": 42},
    )
    print(f"    [{tenant.slug}] challonge ok")


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
