#!/usr/bin/env python3
"""Native-bracket fixtures for the dev seed (split out of seed_dev.py).

Must run inside the target tenant's ``tenant_scope`` — called from
``seed_for_tenant``. Idempotent like the rest of the seed.

B13 grows the original single stage into a set of per-format mid-play states plus
a two-stage group→playoff chain, driving the real :class:`BracketService`
(create → add entrant → enroll → start → report_result → complete_stage →
advance_stage) so every persisted graph is internally consistent — the same code
paths the admin tab, the public page, and the REST endpoints exercise.

Native brackets live on their **own** demo tournaments, never on the
Challonge-mirrored ``tournament`` the rest of the seed builds: the exclusivity
guard (``BracketService._ensure_no_challonge_link``) forbids a native bracket on
a Challonge-linked tournament, and the two integrations never both run one
tournament in production. Each demo tournament is guarded by an
"is stage 0 already built?" check so re-running the seed neither duplicates rows
nor double-reports a result.
"""
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from application.services import BracketService
from models import (
    BracketEntrant,
    BracketEntryStatus,
    BracketFormat,
    BracketMatchGame,
    BracketMatchGameState,
    Match,
    Tenant,
    Tournament,
    TournamentPlayers,
    User,
)

# (display_name, user_key | None). ``None`` seeds a placeholder entrant (no linked
# user); a key links the entrant to a seeded ``User`` — every demo carries both.
EntrantSpec = Tuple[str, Optional[str]]


async def _demo_tournament(tenant: Tenant, name: str) -> Tournament:
    tournament, _ = await Tournament.get_or_create(
        name=name, tenant=tenant,
        defaults={
            "description": "Native-bracket demo fixture (no Challonge link).",
            "seed_generator": "alttpr",
            "is_active": True,
            "players_per_match": 2,
        },
    )
    return tournament


async def _already_built(service: BracketService, tournament_id: int) -> bool:
    """A demo is fully built once its stage-0 bracket exists (idempotency guard)."""
    return bool(await service.list_brackets(tournament_id))


async def _add_entrants(
    service: BracketService,
    actor: User,
    tournament_id: int,
    users: dict[str, User],
    specs: List[EntrantSpec],
) -> List[BracketEntrant]:
    entrants: List[BracketEntrant] = []
    for display_name, user_key in specs:
        user = users[user_key] if user_key else None
        entrants.append(
            await service.add_entrant(
                actor, tournament_id, display_name,
                user.id if user is not None else None,
            )
        )
    return entrants


async def _enroll_seeded(
    service: BracketService,
    actor: User,
    bracket_id: int,
    entrants: List[BracketEntrant],
) -> None:
    for seed, entrant in enumerate(entrants, start=1):
        await service.enroll(actor, bracket_id, entrant.id, seed=seed)


def _win_scores(best_of: int) -> Tuple[int, int]:
    """(winner_score, loser_score) for a clean best-of win (e.g. 1→1-0, 3→2-1)."""
    to_win = best_of // 2 + 1
    return to_win, to_win - 1


async def _report_earliest_open_round(
    service: BracketService, actor: User, bracket_id: int, *, best_of: int = 1
) -> None:
    """Report every OPEN match in the lowest-numbered open round, winner = entry1.

    Leaves later rounds OPEN/PENDING so the stage stays mid-play (deterministic:
    the lower entry-slot always wins). Records a clean best-of set score so the
    redesigned bracket cards have scores to display.
    """
    open_matches = await service.get_open_matches(bracket_id)
    if not open_matches:
        return
    earliest = min(m.round for m in open_matches)
    win, loss = _win_scores(best_of)
    for match in [m for m in open_matches if m.round == earliest]:
        await service.report_result(
            actor, match.id, match.entry1_id,
            entry1_score=win, entry2_score=loss,
        )


async def _single_elim(
    service: BracketService, actor: User, tenant: Tenant, users: dict[str, User]
) -> None:
    tournament = await _demo_tournament(tenant, "Bracket Demo — Single Elimination")
    if await _already_built(service, tournament.id):
        return
    bracket = await service.create_bracket(
        actor, tournament.id, "Championship", BracketFormat.SINGLE_ELIM,
        # Per-round chrome: best-of and the window each round runs in, so the
        # round headers have metadata to render and the schedule dialog has a
        # window to confine its suggestion to. Round 3 is left open-ended to
        # exercise the half-configured case.
        config={
            "rounds": {
                "1": {
                    "best_of": 1,
                    "scheduled_at": "2026-08-01T18:00:00+00:00",
                    "scheduled_end": "2026-08-01T23:00:00+00:00",
                },
                "2": {
                    "best_of": 3,
                    "scheduled_at": "2026-08-01T20:00:00+00:00",
                    "scheduled_end": "2026-08-02T02:00:00+00:00",
                },
                "3": {"best_of": 5, "scheduled_at": "2026-08-02T18:00:00+00:00"},
            },
        },
    )
    entrants = await _add_entrants(
        service, actor, tournament.id, users,
        [
            ("Player One", "player_one"),
            ("Player Two", "player_two"),
            ("Player Three", "player_three"),
            ("Player Four", "player_four"),
            ("Wildcard Qualifier", None),
            ("Community Pick", None),
        ],
    )
    await _enroll_seeded(service, actor, bracket.id, entrants)
    await service.start_bracket(actor, bracket.id)
    # Round 1 resolved (byes auto-completed on start); quarter/semis open, final
    # still pending — a partially-formed elimination bracket. Round 1 is best-of-1.
    await _report_earliest_open_round(service, actor, bracket.id, best_of=1)


async def _draft_stage(
    service: BracketService, actor: User, tenant: Tenant, users: dict[str, User]
) -> None:
    """A stage still being authored — the one state every other demo skips past.

    DRAFT is where the admin's edit, delete, reseed, link-user and import-roster
    controls live, and a started stage shows none of them. The tournament also
    carries ``TournamentPlayers`` rows that are deliberately *not* all rostered,
    so "Import from tournament roster" has something to import.
    """
    tournament = await _demo_tournament(tenant, "Bracket Demo — Draft")
    if await _already_built(service, tournament.id):
        return
    for key in ("player_one", "player_two", "player_three", "player_four"):
        await TournamentPlayers.get_or_create(
            tournament=tournament, user=users[key], tenant=tenant,
        )
    bracket = await service.create_bracket(
        actor, tournament.id, "Round 1", BracketFormat.SINGLE_ELIM,
    )
    entrants = await _add_entrants(
        service, actor, tournament.id, users,
        [
            ("Player One", "player_one"),
            ("Player Two", "player_two"),
            # Unlinked on purpose: the state the admin warns about before Start,
            # and the one the link picker exists to resolve.
            ("Late Signup", None),
        ],
    )
    await _enroll_seeded(service, actor, bracket.id, entrants)


async def _cancelled_stage(
    service: BracketService, actor: User, tenant: Tenant, users: dict[str, User]
) -> None:
    """A stage started, part-played, then abandoned — the CANCELLED close-out.

    The one terminal state that writes no ranking. Seeded so the admin's cancelled
    row (and its absence from every public view) is visible without anyone having
    to abandon a demo bracket by hand.

    The tournament is archived once the stage is cancelled: an active one with no
    signup window reads as open, and the player Tournaments tab offered Sign up
    on an event the fixture says was abandoned.
    """
    tournament = await _demo_tournament(tenant, "Bracket Demo — Cancelled")
    if await _already_built(service, tournament.id):
        await _archive(tournament)
        return
    bracket = await service.create_bracket(
        actor, tournament.id, "Abandoned Bracket", BracketFormat.SINGLE_ELIM,
        config={"default_best_of": 3},
    )
    entrants = await _add_entrants(
        service, actor, tournament.id, users,
        [
            ("Player One", "player_one"),
            ("Player Two", "player_two"),
            ("Player Three", "player_three"),
            ("Player Four", "player_four"),
        ],
    )
    await _enroll_seeded(service, actor, bracket.id, entrants)
    await service.start_bracket(actor, bracket.id)
    await _report_earliest_open_round(service, actor, bracket.id, best_of=3)
    await service.cancel_stage(actor, bracket.id)
    await _archive(tournament)


async def _archive(tournament: Tournament) -> None:
    if tournament.is_active:
        tournament.is_active = False
        await tournament.save(update_fields=["is_active"])


async def _double_elim(
    service: BracketService, actor: User, tenant: Tenant, users: dict[str, User]
) -> None:
    tournament = await _demo_tournament(tenant, "Bracket Demo — Double Elimination")
    if await _already_built(service, tournament.id):
        return
    bracket = await service.create_bracket(
        actor, tournament.id, "Main Event", BracketFormat.DOUBLE_ELIM,
    )
    entrants = await _add_entrants(
        service, actor, tournament.id, users,
        [
            ("Player One", "player_one"),
            ("Player Two", "player_two"),
            ("Player Three", "player_three"),
            ("Bracket Demo Bye", None),
        ],
    )
    await _enroll_seeded(service, actor, bracket.id, entrants)
    await service.start_bracket(actor, bracket.id)
    # Report the winners-bracket first round: both losers drop into the losers
    # bracket, opening a losers-bracket round (and the winners final) mid-play.
    # Make the first WB match a forfeit (a no-show DQ) so the demo exercises the
    # "FF" card marker; report any remaining earliest-round matches with scores.
    open_matches = await service.get_open_matches(bracket.id)
    if open_matches:
        earliest = min(m.round for m in open_matches)
        earliest_matches = sorted(
            (m for m in open_matches if m.round == earliest),
            key=lambda m: m.position,
        )
        for index, match in enumerate(earliest_matches):
            if index == 0:
                await service.report_result(
                    actor, match.id, match.entry1_id, forfeit=True,
                )
            else:
                await service.report_result(
                    actor, match.id, match.entry1_id,
                    entry1_score=1, entry2_score=0,
                )


async def _swiss(
    service: BracketService, actor: User, tenant: Tenant, users: dict[str, User]
) -> None:
    tournament = await _demo_tournament(tenant, "Bracket Demo — Swiss")
    if await _already_built(service, tournament.id):
        return
    bracket = await service.create_bracket(
        actor, tournament.id, "Swiss Qualifier", BracketFormat.SWISS,
        config={"swiss_rounds": 3},
    )
    entrants = await _add_entrants(
        service, actor, tournament.id, users,
        [
            ("Player One", "player_one"),
            ("Player Two", "player_two"),
            ("Player Three", "player_three"),
            ("Player Four", "player_four"),
            ("Late Registrant", None),
        ],
    )
    await _enroll_seeded(service, actor, bracket.id, entrants)
    await service.start_bracket(actor, bracket.id)

    # Report exactly one round-1 pairing (leave the other open so no next round is
    # generated) — a mid-round state.
    matches = await service.list_matches(bracket.id)
    contests = [
        m for m in matches
        if m.state.value == 'open' and m.entry1_id and m.entry2_id
    ]
    if contests:
        first = min(contests, key=lambda m: m.position)
        await service.report_result(actor, first.id, first.entry1_id)

    # Drop one entrant mid-event: the bye recipient (its round-1 match is already
    # complete, so the still-open pairing is untouched). Drop at both the roster
    # level (BracketEntrantStatus) and the stage-participation level
    # (BracketEntryStatus) so Swiss re-pairing would exclude them.
    bye = next(
        (m for m in matches if m.entry2_id is None and m.entry1_id is not None),
        None,
    )
    if bye is not None:
        entries = {e.id: e for e in await service.list_entries(bracket.id)}
        entry = entries.get(bye.entry1_id)
        if entry is not None and entry.status == BracketEntryStatus.ACTIVE:
            await service.drop_entrant(actor, entry.entrant_id)
            await service.retire_entry(actor, entry.id)


async def _round_robin(
    service: BracketService, actor: User, tenant: Tenant, users: dict[str, User]
) -> None:
    tournament = await _demo_tournament(tenant, "Bracket Demo — Round Robin")
    if await _already_built(service, tournament.id):
        return
    bracket = await service.create_bracket(
        actor, tournament.id, "Group Stage", BracketFormat.ROUND_ROBIN,
        config={"group_count": 2},
    )
    entrants = await _add_entrants(
        service, actor, tournament.id, users,
        [
            ("Player One", "player_one"),
            ("Player Two", "player_two"),
            ("Player Three", "player_three"),
            ("Player Four", "player_four"),
            ("Open Qualifier A", None),
            ("Open Qualifier B", None),
        ],
    )
    await _enroll_seeded(service, actor, bracket.id, entrants)
    await service.start_bracket(actor, bracket.id)
    # Report one match per group so both groups are partway through with a
    # partially-formed standings table (the rest stay open).
    matches = await service.list_matches(bracket.id)
    reported_groups: set = set()
    for match in sorted(matches, key=lambda m: (m.group_number or 0, m.round, m.position)):
        if match.state.value != 'open' or match.group_number in reported_groups:
            continue
        await service.report_result(actor, match.id, match.entry1_id)
        reported_groups.add(match.group_number)


async def _two_stage(
    service: BracketService, actor: User, tenant: Tenant, users: dict[str, User]
) -> None:
    tournament = await _demo_tournament(tenant, "Bracket Demo — Groups to Playoff")
    if await _already_built(service, tournament.id):
        return

    # Stage 0 — round-robin groups, run to completion so final_rank is written.
    groups = await service.create_bracket(
        actor, tournament.id, "Group Stage", BracketFormat.ROUND_ROBIN,
        stage_order=0, config={"group_count": 2},
    )
    entrants = await _add_entrants(
        service, actor, tournament.id, users,
        [
            ("Player One", "player_one"),
            ("Player Two", "player_two"),
            ("Player Three", "player_three"),
            ("Player Four", "player_four"),
        ],
    )
    await _enroll_seeded(service, actor, groups.id, entrants)
    await service.start_bracket(actor, groups.id)
    for match in await service.get_open_matches(groups.id):
        await service.report_result(actor, match.id, match.entry1_id)
    await service.complete_stage(actor, groups.id)

    playoff = await service.create_bracket(
        actor, tournament.id, "Playoff", BracketFormat.SINGLE_ELIM,
        stage_order=1,
        config={"advancement": {"count": 1, "per_group": True, "seeding": "snake"}},
    )
    await service.advance_stage(actor, tournament.id, from_stage_order=0)
    await service.start_bracket(actor, playoff.id)


async def _seed_a_series(
    service: BracketService, tenant: Tenant, users: dict[str, User]
) -> None:
    """Leave one Bo3 mid-series: game 1 won, game 2 scheduled and waiting.

    Builds the ``Match`` + :class:`BracketMatchGame` rows directly rather than
    through ``BracketService.schedule_bracket_match``: that path runs
    ``MatchService.create_match``, which enforces the tournament-hours window,
    seeds acknowledgments, and fans out Discord notifications into a queue no
    seed script runs. ``seed_dev.py`` builds its matches the same way.

    Game 2 being scheduled-but-unfinished is the state the racetime auto-open
    hold acts on, so ``/ui-validation`` can see a held game rather than only the
    happy path.
    """
    tournament = await Tournament.get_or_none(
        name="Bracket Demo — Single Elimination", tenant=tenant
    )
    if tournament is None:
        return
    brackets = await service.list_brackets(tournament.id)
    if not brackets:
        return
    open_matches = await service.get_open_matches(brackets[0].id)
    if not open_matches:
        return
    bracket_match = open_matches[0]
    best_of = service.resolve_best_of(brackets[0], bracket_match)

    # One game per state: game 1 decided, game 2 still waiting on it, and game 3
    # cancelled because a 2-0 would clinch the series. The cancelled row is the
    # reason ``BracketMatchGame`` keeps unplayed games instead of deleting them,
    # and nothing else in the seed produces it.
    game_states = {
        1: BracketMatchGameState.COMPLETE,
        2: BracketMatchGameState.SCHEDULED,
        3: BracketMatchGameState.CANCELLED,
    }
    for number, state in game_states.items():
        match, _ = await Match.get_or_create(
            title=(
                f"{tournament.name}: bracket match {bracket_match.id} "
                f"— Game {number} of {best_of}"
            ),
            tournament=tournament, tenant=tenant,
            defaults={
                "scheduled_at": datetime(2026, 8, 1, 20 + number, 0, tzinfo=timezone.utc),
            },
        )
        await BracketMatchGame.get_or_create(
            bracket_match=bracket_match, game_number=number, tenant=tenant,
            defaults={
                "match": match,
                "state": state,
                "winner_entry_id": (
                    bracket_match.entry1_id
                    if state is BracketMatchGameState.COMPLETE else None
                ),
            },
        )


async def _seed_live_states(
    service: BracketService, tenant: Tenant, users: dict[str, User]
) -> None:
    """Leave three matchups in the live states the bracket now renders (U2).

    A state the seed never creates is a state nobody can review, and the whole
    point of the live bracket is the hours *between* "scheduled" and "confirmed"
    — which no other fixture reaches. This leaves, in the double-elim demo:

    * a game **in progress** (``started_at`` set, no ``finished_at``) → LIVE,
    * one **awaiting result** (finished, unconfirmed) → AWAITING_RESULT,
    * one **released** (its game row deleted, matchup open and unbooked) →
      the rebook path U3a produces.

    ``Match`` rows are built directly, for the reasons ``_seed_a_series``
    documents. Idempotent: each match is keyed by title and each game by
    ``(bracket_match, game_number)``.
    """
    tournament = await Tournament.get_or_none(
        name="Bracket Demo — Double Elimination", tenant=tenant
    )
    if tournament is None:
        return
    brackets = await service.list_brackets(tournament.id)
    if not brackets:
        return
    open_matches = await service.get_open_matches(brackets[0].id)
    if len(open_matches) < 3:
        return

    live_at = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
    specs = (
        ('live', open_matches[0], {'started_at': live_at}),
        ('awaiting result', open_matches[1], {
            'started_at': live_at, 'finished_at': datetime(
                2026, 8, 3, 19, 30, tzinfo=timezone.utc,
            ),
        }),
    )
    for label, bracket_match, stamps in specs:
        match, _ = await Match.get_or_create(
            title=f"{tournament.name}: matchup {bracket_match.id} — {label}",
            tournament=tournament, tenant=tenant,
            defaults={'scheduled_at': live_at, **stamps},
        )
        await BracketMatchGame.get_or_create(
            bracket_match=bracket_match, game_number=1, tenant=tenant,
            defaults={'match': match, 'state': BracketMatchGameState.SCHEDULED},
        )

    # The third matchup is left OPEN with no game row at all — what a released
    # slot looks like once U3a has handed it back.


async def seed_brackets_for_tenant(
    tenant: Tenant,
    tournament: Tournament,
    users: dict[str, User],
) -> None:
    """Seed one bracket per format in a mid-play state plus a two-stage chain.

    ``tournament`` is the tenant's Challonge-mirrored tournament and is left
    untouched (native brackets get their own demo tournaments — see module
    docstring). Runs inside the caller's ``tenant_scope``.
    """
    service = BracketService()
    actor = users["staff_user"]

    await _single_elim(service, actor, tenant, users)
    await _draft_stage(service, actor, tenant, users)
    await _cancelled_stage(service, actor, tenant, users)
    await _double_elim(service, actor, tenant, users)
    await _swiss(service, actor, tenant, users)
    await _round_robin(service, actor, tenant, users)
    await _two_stage(service, actor, tenant, users)
    await _seed_a_series(service, tenant, users)
    await _seed_live_states(service, tenant, users)
    print(f"    [{tenant.slug}] brackets ok")
