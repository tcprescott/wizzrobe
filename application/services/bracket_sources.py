"""
Bracket sources - the shared contract for brackets that live on another site.

Each external bracket site a tournament can follow (start.gg today; Challonge
next) is one :class:`BracketSource`. Callers that touch "a linked bracket" loop
over :func:`sources` instead of naming a site, so a new site plugs in here
rather than adding another hand-written call at every spot:

* match confirmation calls :func:`on_match_confirmed`,
* My Schedule lists :func:`list_bookable_for_user` and books through
  :func:`schedule`,
* Admin → Tournaments syncs through :func:`sync_tournament` / :func:`sync_all`,
  links and unlinks through :func:`link` / :func:`unlink`, and counts entrants
  still waiting for a member through :func:`unmatched_entrant_counts`.

Challonge is registered for syncing and booking; its result push and its
best-of-1 My Schedule rows still run through ``ChallongeService`` directly
until it moves onto the rest of the contract (its best-of sets already list
here, a row per game). Native brackets live in Wizzrobe, so they have nothing to sync.
"""

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple
from urllib.parse import urlparse

from application.services.auth_service import AuthService
from application.services.bracket_service import BracketService
from application.services.bracket_set_service import BracketSetService, OpenGames
from application.services.challonge_service import ChallongeService
from application.services.feature_flag_service import FeatureFlagService
from application.services.round_schedule_service import RoundScheduleService
from application.services.startgg_service import StartggService
from application.utils.match_labels import (
    MatchupPart,
    challonge_round_label,
    entrant_name,
    is_undecided_side,
    sides_parts,
)
from application.utils.timezone import parse_local_datetime
from models import FeatureFlag, Match, Tournament, User

logger = logging.getLogger(__name__)


@dataclass
class BookableMatch:
    """One bracket matchup a player can book now, whatever site it came from."""

    source: str
    source_label: str
    key: int
    tournament: Tournament
    round_label: str
    opponent_name: str
    player_ids: List[int] = field(default_factory=list)
    window_text: Optional[str] = None
    ahead: bool = False
    best_of: int = 1
    games_booked: int = 0
    if_needed: bool = False


@dataclass
class UnbookedMatch:
    """One open bracket matchup nobody has booked yet, as staff see it.

    ``source`` is ``'challonge'``, ``'startgg'`` or ``'bracket'`` (native), and
    ``key`` that source's row id. ``unlinked`` names the players with no
    Wizzrobe account: the matchup can't be booked until staff match them.
    """

    source: str
    source_label: str
    key: int
    tournament: Tournament
    round_label: str
    player_names: List[str]
    player_ids: List[Optional[int]]
    unlinked: List[str] = field(default_factory=list)
    window_text: Optional[str] = None
    bracket_id: Optional[int] = None
    unlinked_entrants: List[Tuple[int, str]] = field(default_factory=list)
    best_of: int = 1
    game_number: int = 1
    games_booked: int = 0
    same_player_matches: List[Match] = field(default_factory=list)
    players_tbd: bool = False
    lost_bookings: List[Match] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.unlinked and not self.players_tbd

    @property
    def matchup(self) -> str:
        return ' vs '.join(self.player_names)

    @property
    def matchup_parts(self) -> List[MatchupPart]:
        """:attr:`matchup` in pieces, flagging the sides the bracket hasn't decided.

        Only an accountless side of a players-TBD matchup can be one: anywhere
        else a name is a player's, whatever it looks like.
        """
        return sides_parts([
            (name, self.players_tbd and uid is None and is_undecided_side(name))
            for name, uid in zip(self.player_names, self.player_ids, strict=False)
        ])


@dataclass
class SyncResult:
    tournament_id: int
    tournament_name: str
    source_label: str
    summary: str
    error: Optional[str] = None


class BracketSource(Protocol):
    key: str
    label: str
    flag: FeatureFlag

    def linked(self, tournament: Tournament) -> bool: ...

    async def sync(self, tournament: Tournament, actor: User) -> str: ...

    async def list_bookable_for_user(self, user: User) -> List[BookableMatch]: ...

    async def schedule(
        self, key: int, scheduled_date: str, scheduled_time: str, actor: User,
        *, allow_outside_round: bool = False,
    ) -> Match: ...

    async def on_match_confirmed(self, match: Match, actor: Optional[User]) -> None: ...

    async def link(self, tournament: Tournament, id_or_url: str, actor: User) -> str: ...

    async def unlink(self, tournament: Tournament, actor: User) -> None: ...

    async def count_unmatched(self, tournament_ids: List[int]) -> Dict[int, int]: ...


def set_game_label(base: str, best_of: int, position: int) -> str:
    """A row for one unbooked game of a set, without a number.

    The number comes from when the game ends up being played, so a row that
    isn't booked yet can't promise one: ``Final · best of 3 · another game``.
    """
    if best_of <= 1:
        return base
    tail = '' if position == 1 else ' · last game' if position == best_of else ' · another game'
    return f'{base} · best of {best_of}{tail}'


def games_left_label(base: str, left: OpenGames) -> str:
    """Staff's one row for a partly booked set: ``Final · best of 3 · 2 games left``."""
    count = len(left.slots)
    return f"{base} · best of {left.best_of} · {count} game{'s' if count != 1 else ''} left"


def _set_rows(row: BookableMatch, left: OpenGames) -> List[BookableMatch]:
    """``row`` once per game still to book."""
    return [
        replace(
            row, best_of=left.best_of, games_booked=left.booked, if_needed=if_needed,
            round_label=set_game_label(row.round_label, left.best_of, left.booked + i),
        )
        for i, if_needed in enumerate(left.slots, start=1)
    ]


class StartggSource:
    key = 'startgg'
    label = 'start.gg'
    flag = FeatureFlag.STARTGG

    def linked(self, tournament: Tournament) -> bool:
        return bool(tournament.startgg_event_slug)

    async def sync(self, tournament: Tournament, actor: User) -> str:
        counts = await StartggService().sync_event(tournament.id, actor, force=True)
        return f"{counts.get('entrants', 0)} entrants, {counts.get('sets', 0)} matches"

    async def list_bookable_for_user(self, user: User) -> List[BookableMatch]:
        rows = await StartggService().list_unbooked_sets_for_user(user, include_partial_sets=True)
        rounds = RoundScheduleService()
        sets = BracketSetService()
        out: List[BookableMatch] = []
        for row in rows:
            me_first = row.entrant1.user_id == user.id
            opponent = row.entrant2 if me_first else row.entrant1
            left = await sets.open_games_for(self.key, row.id, row.match_id)
            out.extend(_set_rows(BookableMatch(
                source=self.key, source_label=self.label, key=row.id,
                tournament=row.tournament,
                round_label=row.round_text or f'Round {row.round}',
                opponent_name=entrant_name(opponent),
                player_ids=[row.entrant1.user_id, row.entrant2.user_id],
                window_text=await rounds.describe_for_startgg_set(row),
            ), left))
        return out

    async def schedule(
        self, key: int, scheduled_date: str, scheduled_time: str, actor: User,
        *, allow_outside_round: bool = False,
    ) -> Match:
        return await StartggService().schedule_set(
            key, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
        )

    async def link(self, tournament: Tournament, id_or_url: str, actor: User) -> str:
        counts = await StartggService().link_event(tournament.id, id_or_url, actor)
        return f"{counts.get('entrants', 0)} entrants, {counts.get('sets', 0)} matches"

    async def unlink(self, tournament: Tournament, actor: User) -> None:
        await StartggService().unlink_event(tournament.id, actor)

    async def count_unmatched(self, tournament_ids: List[int]) -> Dict[int, int]:
        return await StartggService().count_unmatched_entrants(tournament_ids)

    async def on_match_confirmed(self, match: Match, actor: Optional[User]) -> None:
        await StartggService().report_result_if_linked(match, actor)


class ChallongeSource:
    """Sync and staff booking for now; see the module docstring."""

    key = 'challonge'
    label = 'Challonge'
    flag = FeatureFlag.CHALLONGE

    def linked(self, tournament: Tournament) -> bool:
        return bool(tournament.challonge_tournament_id)

    async def sync(self, tournament: Tournament, actor: User) -> str:
        counts = await ChallongeService().sync_bracket(tournament.id, actor, force=True)
        return f"{counts.get('participants', 0)} players, {counts.get('matches', 0)} matches"

    async def list_bookable_for_user(self, user: User) -> List[BookableMatch]:
        """The player's best-of sets only: a best-of-1 Challonge matchup is still
        listed by ``ChallongeService`` (with a reason when it can't be booked yet)."""
        if not ChallongeService.is_configured():
            return []
        rows = await ChallongeService().list_unscheduled_matches_for_user(user, include_partial_sets=True)
        rounds = RoundScheduleService()
        sets = BracketSetService()
        out: List[BookableMatch] = []
        for cm in rows:
            p1, p2 = cm.participant1, cm.participant2
            if p1 is None or p2 is None or p1.user_id is None or p2.user_id is None:
                continue
            left = await sets.open_games_for(self.key, cm.id, cm.match_id)
            if left.best_of <= 1:
                continue
            out.extend(_set_rows(BookableMatch(
                source=self.key, source_label=self.label, key=cm.id,
                tournament=cm.tournament, round_label=challonge_round_label(cm.round),
                opponent_name=entrant_name(p2 if p1.user_id == user.id else p1),
                player_ids=[p1.user_id, p2.user_id],
                window_text=await rounds.describe_for_challonge_match(cm),
            ), left))
        return out

    async def schedule(
        self, key: int, scheduled_date: str, scheduled_time: str, actor: User,
        *, allow_outside_round: bool = False,
    ) -> Match:
        return await ChallongeService().schedule_challonge_match(
            key, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
        )

    async def link(self, tournament: Tournament, id_or_url: str, actor: User) -> str:
        await ChallongeService().link_tournament(tournament.id, id_or_url, actor)
        return 'linked and synced'

    async def unlink(self, tournament: Tournament, actor: User) -> None:
        await ChallongeService().unlink_tournament(tournament.id, actor)

    async def count_unmatched(self, tournament_ids: List[int]) -> Dict[int, int]:
        return await ChallongeService().count_unmatched_participants(tournament_ids)

    async def on_match_confirmed(self, match: Match, actor: Optional[User]) -> None:
        return None


_SOURCES: List[BracketSource] = [StartggSource(), ChallongeSource()]


def sources() -> List[BracketSource]:
    return list(_SOURCES)


def by_key(key: str) -> BracketSource:
    for source in _SOURCES:
        if source.key == key:
            return source
    raise ValueError(f'Unknown bracket site: {key}')


async def live_sources() -> List[BracketSource]:
    """The sources whose feature is on in the current community."""
    live = await FeatureFlagService().enabled_flags()
    return [s for s in _SOURCES if s.flag in live]


async def source_for(tournament: Tournament) -> Optional[BracketSource]:
    """The live site this tournament follows, if any."""
    return next((s for s in await live_sources() if s.linked(tournament)), None)


async def on_match_confirmed(match: Match, actor: Optional[User]) -> None:
    """Tell every site about a confirmed result. One site failing never stops the others."""
    try:
        await BracketSetService().call_off_unneeded(match)
    except Exception:
        logger.exception('calling off unneeded set games failed for match %s', match.id)
    for source in await live_sources():
        try:
            await source.on_match_confirmed(match, actor)
        except Exception:
            logger.exception('%s result hook failed for match %s', source.key, match.id)


async def list_bookable_for_user(user: User) -> List[BookableMatch]:
    out: List[BookableMatch] = []
    for source in await live_sources():
        out.extend(await source.list_bookable_for_user(user))
    return out


async def schedule(
    source_key: str, key: int, scheduled_date: str, scheduled_time: str, actor: User,
    *, allow_outside_round: bool = False,
) -> Match:
    match = await by_key(source_key).schedule(
        key, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
    )
    await _hand_over_held_crew(match, actor)
    return match


async def _hand_over_held_crew(match: Match, actor: User) -> None:
    """Move crew who signed up for the matchup ahead onto its new booking."""
    from application.services.crew_hold_service import CrewHoldService

    await CrewHoldService().absorb(match, actor)


async def schedule_set(
    source_key: str, key: int, times: Sequence[Tuple[str, str]], actor: User,
    *, allow_outside_round: bool = False, ahead: bool = False,
) -> List[Match]:
    """Book a best-of set on a Challonge or start.gg matchup: one match per game.

    ``times`` is each game's ``(date, time)``; a single entry books a plain
    best-of-1. Every time is checked against the stage's window before
    anything is booked, so a refused game 3 doesn't leave games 1 and 2 behind.
    """
    BracketSetService.check_times(times)
    if len(times) > 1 and not allow_outside_round:
        rounds = RoundScheduleService()
        ref = {'challonge_match_id': key} if source_key == 'challonge' else {'startgg_set_id': key}
        for day, clock in times[1:]:
            await rounds.check_reference(parse_local_datetime(day, clock), actor, **ref)
    book = book_ahead if ahead else schedule
    first = await book(source_key, key, times[0][0], times[0][1], actor, allow_outside_round=allow_outside_round)
    if len(times) == 1:
        return [first]
    return [first, *await BracketSetService().book_followers(first, times, actor)]


async def book_set_game(
    source_key: str, key: int, scheduled_date: str, scheduled_time: str, actor: User,
    *, allow_outside_round: bool = False,
) -> Match:
    """Book one game of a matchup's best-of set; the first game booked leads it."""
    return await BracketSetService().book_game(
        source_key, key, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
    )


async def best_of_for(source_key: str, key: int) -> int:
    """How many games this matchup's stage plays, from the round schedule."""
    return await RoundScheduleService().best_of_for(
        challonge_match_id=key if source_key == 'challonge' else None,
        startgg_set_id=key if source_key == 'startgg' else None,
    )


async def _bookable_scope(actor: Optional[User]) -> Optional[List[int]]:
    """The tournaments ``actor`` may book for: ``None`` for staff (all of them)."""
    if await AuthService.is_staff(actor):
        return None
    admin_ids, _ = await AuthService.tournament_scope(actor)
    return sorted(admin_ids)


def _side(user_id: Optional[int], name: str, unlinked: List[str]) -> str:
    if user_id is None:
        unlinked.append(name)
    return name


async def list_unbooked(actor: Optional[User], *, include_players_tbd: bool = False) -> List[UnbookedMatch]:
    """Every open bracket matchup with no game booked that ``actor`` may book.

    Staff see the whole community and a tournament admin their own tournaments;
    anyone else sees nothing. Grouped by tournament (by name), each source's
    rows in bracket order. ``include_players_tbd`` adds the Challonge and
    start.gg matchups still waiting on a player, which staff can book ahead.
    """
    scope = await _bookable_scope(actor)
    if scope == []:
        return []
    out = await _list_open(scope, include_players_tbd)
    await _attach_same_player_matches(out)
    return out


async def list_open_for_crew() -> List[UnbookedMatch]:
    """Every open matchup in the community nobody has booked a game of, players known or not.

    What crew can sign up for ahead (:mod:`crew_hold_service`): the whole
    community's, whoever is looking, since the schedule it feeds is public to
    members. A set with a game booked already has a match to sign up on.
    """
    return [row for row in await _list_open(None, True, windows=False) if not row.games_booked]


class _NoWindows:
    """Stands in for the round schedule where nobody reads a matchup's booking window."""

    async def describe_for_challonge_match(self, _row) -> None:
        return None

    async def describe_for_startgg_set(self, _row) -> None:
        return None

    async def describe_for_bracket_match(self, _row) -> None:
        return None


async def _list_open(
    scope: Optional[List[int]], include_players_tbd: bool, *, windows: bool = True,
) -> List[UnbookedMatch]:
    live = await FeatureFlagService().enabled_flags()
    rounds: Any = RoundScheduleService() if windows else _NoWindows()
    out: List[UnbookedMatch] = []

    sets = BracketSetService()

    async def partly_booked(source_key: str, key: int, first_id: Optional[int], base: str):
        """``(round label, games booked)``, or None for a set with nothing left to book."""
        if first_id is None:
            return base, 0
        left = await sets.open_games_for(source_key, key, first_id)
        if not left.slots:
            return None
        return games_left_label(base, left), left.booked

    if FeatureFlag.CHALLONGE in live:
        for cm in await ChallongeService().list_unbooked_matches(scope, include_partial_sets=True):
            if cm.participant1 is None or cm.participant2 is None:
                continue
            base = 'Group stage' if cm.group_stage else challonge_round_label(cm.round)
            found = await partly_booked('challonge', cm.id, cm.match_id, base)
            if found is None:
                continue
            unlinked: List[str] = []
            p1, p2 = cm.participant1, cm.participant2
            names = [_side(p.user_id, p.name or 'TBD', unlinked) for p in (p1, p2)]
            out.append(UnbookedMatch(
                source='challonge', source_label='Challonge', key=cm.id,
                tournament=cm.tournament, round_label=found[0], games_booked=found[1],
                player_names=names, player_ids=[p1.user_id, p2.user_id], unlinked=unlinked,
                window_text=await rounds.describe_for_challonge_match(cm),
            ))

    if FeatureFlag.STARTGG in live:
        for row in await StartggService().list_unbooked_sets(scope, include_partial_sets=True):
            base = row.round_text or f'Round {row.round}'
            found = await partly_booked('startgg', row.id, row.match_id, base)
            if found is None:
                continue
            unlinked = []
            e1, e2 = row.entrant1, row.entrant2
            names = [_side(e.user_id, e.name, unlinked) for e in (e1, e2)]
            out.append(UnbookedMatch(
                source='startgg', source_label='start.gg', key=row.id,
                tournament=row.tournament, round_label=found[0], games_booked=found[1],
                player_names=names, player_ids=[e1.user_id, e2.user_id], unlinked=unlinked,
                window_text=await rounds.describe_for_startgg_set(row),
            ))

    if include_players_tbd:
        out.extend(await _list_players_tbd(scope, live, rounds))

    if FeatureFlag.BRACKETS in live:
        brackets = BracketService()
        for bm in await brackets.list_unbooked_matchups(scope):
            unlinked = []
            a, b = bm.entry1.entrant, bm.entry2.entrant
            names = [_side(e.user_id, e.display_name, unlinked) for e in (a, b)]
            best_of = brackets.resolve_best_of(bm.bracket, bm)
            taken = {g.game_number for g in bm.games}
            number = next(n for n in range(1, best_of + 1) if n not in taken)
            base = f'Losers round {abs(bm.round)}' if bm.round < 0 else f'Round {bm.round}'
            label = set_game_label(f'{bm.bracket.name} · {base}', best_of, len(taken) + 1)
            out.append(UnbookedMatch(
                source='bracket', source_label='Bracket', key=bm.id,
                tournament=bm.bracket.tournament, round_label=label,
                player_names=names, player_ids=[a.user_id, b.user_id], unlinked=unlinked,
                window_text=await rounds.describe_for_bracket_match(bm),
                bracket_id=bm.bracket_id, best_of=best_of, game_number=number,
                unlinked_entrants=[(e.id, e.display_name) for e in (a, b) if e.user_id is None],
            ))

    out.sort(key=lambda u: (u.tournament.name.lower(), u.tournament.id))
    return out


async def _list_players_tbd(
    scope: Optional[List[int]], live: set, rounds: Any,
) -> List[UnbookedMatch]:
    """Unbooked Challonge and start.gg matchups still waiting on a player, for booking ahead."""
    out: List[UnbookedMatch] = []
    if FeatureFlag.CHALLONGE in live:
        challonge = ChallongeService()
        rows = await challonge.list_unbooked_pending(scope)
        sides = await challonge.placeholder_sides_for(rows)
        for cm in rows:
            ids, _ = sides[cm.id]
            out.append(UnbookedMatch(
                source='challonge', source_label='Challonge', key=cm.id, tournament=cm.tournament,
                round_label=challonge_round_label(cm.round),
                player_names=_tbd_names(cm.participant1, cm.participant2, sides[cm.id][1]),
                player_ids=ids, players_tbd=True,
                window_text=await rounds.describe_for_challonge_match(cm),
            ))
    if FeatureFlag.STARTGG in live:
        startgg = StartggService()
        rows = await startgg.list_unbooked_pending(scope)
        sides = await startgg.placeholder_sides_for(rows)
        for row in rows:
            ids, _ = sides[row.id]
            out.append(UnbookedMatch(
                source='startgg', source_label='start.gg', key=row.id, tournament=row.tournament,
                round_label=row.round_text or f'Round {row.round}',
                player_names=_tbd_names(row.entrant1, row.entrant2, sides[row.id][1]),
                player_ids=ids, players_tbd=True,
                window_text=await rounds.describe_for_startgg_set(row),
            ))
    if out:
        from application.services.match.match_service import MatchService

        lost: Dict[int, List[Match]] = {}
        for match in await MatchService().lost_placeholder_bookings(sorted({i.tournament.id for i in out})):
            lost.setdefault(match.tournament_id, []).append(match)  # type: ignore[attr-defined]
        for item in out:
            item.lost_bookings = lost.get(item.tournament.id, [])
    return out


def _tbd_names(first, second, labels: List[str]) -> List[str]:
    """Each side's name: the decided, linked entrant's, else the next placeholder in turn."""
    pending = iter(labels)
    return [
        entrant_name(e) if e is not None and e.user_id is not None else next(pending, 'TBD')
        for e in (first, second)
    ]


async def relink_ahead(source_key: str, key: int, match_id: int, actor: User) -> Match:
    """Attach a placeholder booking a bracket reset cut loose to this matchup."""
    if source_key == 'challonge':
        match = await ChallongeService().relink_ahead(key, match_id, actor)
    elif source_key == 'startgg':
        match = await StartggService().relink_ahead(key, match_id, actor)
    else:
        raise ValueError("Only Challonge and start.gg bookings are linked this way.")
    await _hand_over_held_crew(match, actor)
    return match


async def book_ahead(
    source_key: str, key: int, scheduled_date: str, scheduled_time: str, actor: User,
    *, allow_outside_round: bool = False,
) -> Match:
    """Book a Challonge or start.gg matchup before its players are known (staff, tournament admins)."""
    if source_key == 'challonge':
        match = await ChallongeService().book_ahead(
            key, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
        )
    elif source_key == 'startgg':
        match = await StartggService().book_ahead(
            key, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
        )
    else:
        raise ValueError("Only Challonge and start.gg matchups can be booked before their players are known.")
    await _hand_over_held_crew(match, actor)
    return match


async def _attach_same_player_matches(items: List[UnbookedMatch]) -> None:
    ready = [i for i in items if i.ready and not i.games_booked]
    if not ready:
        return
    from application.services.match.match_service import MatchService

    by_key: Dict[Tuple[int, frozenset], List[Match]] = {}
    for match in await MatchService().bracket_link_candidates(sorted({i.tournament.id for i in ready})):
        players = frozenset(p.user_id for p in match.players)  # type: ignore[attr-defined]
        by_key.setdefault((match.tournament_id, players), []).append(match)  # type: ignore[attr-defined]
    for item in ready:
        item.same_player_matches = by_key.get((item.tournament.id, frozenset(item.player_ids)), [])


async def link_existing(source_key: str, key: int, match_id: int, actor: User) -> None:
    """Record ``match_id``, scheduled by hand, as the match for this unbooked matchup.

    Staff and the tournament's admins only, and only a match with the matchup's
    own two players: each site settles the matchup by whose rank is whose.
    """
    if source_key == 'challonge':
        match = await ChallongeService().link_existing_match(key, match_id, actor)
    elif source_key == 'startgg':
        match = await StartggService().link_existing_match(key, match_id, actor)
    elif source_key == 'bracket':
        await BracketService().link_match_to_bracket_match(actor, key, match_id)
        return
    else:
        raise ValueError(f'Unknown bracket source: {source_key}')
    await _hand_over_held_crew(match, actor)


@dataclass(frozen=True)
class SiteResult:
    """A booked match whose result was reported on its bracket site."""

    site: str
    winner_user_id: Optional[int]
    winner_name: str
    no_winner: bool = False


async def results_on_site(match_ids: List[int]) -> Dict[int, SiteResult]:
    """``match id -> SiteResult`` for booked matches their bracket site already settled."""
    if not match_ids:
        return {}
    live = await FeatureFlagService().enabled_flags()
    out: Dict[int, SiteResult] = {}
    if FeatureFlag.CHALLONGE in live:
        for match_id, (uid, name) in (await ChallongeService().results_on_site(match_ids)).items():
            out[match_id] = SiteResult('Challonge', uid, name or '', no_winner=name is None)
    if FeatureFlag.STARTGG in live:
        for match_id, (uid, name) in (await StartggService().results_on_site(match_ids)).items():
            out[match_id] = SiteResult('start.gg', uid, name or '', no_winner=name is None)
    return out


async def record_site_result(match_id: int, actor: User) -> SiteResult:
    """Copy the bracket site's winner onto the Wizzrobe match and confirm it."""
    from application.services.match.match_schedule_service import MatchScheduleService
    from application.services.match.match_service import MatchService

    result = (await results_on_site([match_id])).get(match_id)
    if result is None:
        raise ValueError("The bracket site doesn't have a result for this match yet. Sync it and try again.")
    if result.no_winner:
        raise ValueError(
            f"{result.site} closed this match with no winner, so there's no result to record. "
            f"Call it off instead."
        )
    match = await MatchService().get_by_id(match_id)
    if match is None:
        raise ValueError('That match no longer exists.')
    await match.fetch_related('players')
    winner = next((p for p in match.players if result.winner_user_id and p.user_id == result.winner_user_id), None)  # type: ignore[attr-defined]
    if winner is None:
        raise ValueError(
            f"{result.winner_name or 'The winner'} on {result.site} isn't one of this "
            f"match's players. Record the result by hand."
        )
    await MatchService().record_match_result(match_id, winner.id, actor=actor, announce=False)
    await MatchScheduleService().close_played_elsewhere(match, actor, result.site)
    return result


async def sync_tournament(tournament: Tournament, actor: User) -> SyncResult:
    source = await source_for(tournament)
    if source is None:
        raise ValueError(f"{tournament.name} isn't linked to a bracket site.")
    summary = await source.sync(tournament, actor)
    return SyncResult(tournament.id, tournament.name, source.label, summary)


async def sync_all(tournaments: List[Tournament], actor: User,
                   on_progress: Optional[Callable[[str], None]] = None) -> List[SyncResult]:
    """Sync every linked tournament in the list; a failure is reported, not raised.

    ``on_progress(name)`` is called before each tournament's sync.
    """
    results: List[SyncResult] = []
    for tournament in tournaments:
        source = await source_for(tournament)
        if source is None:
            continue
        if on_progress is not None:
            on_progress(tournament.name)
        try:
            summary = await source.sync(tournament, actor)
            results.append(SyncResult(tournament.id, tournament.name, source.label, summary))
        except (ValueError, PermissionError) as e:
            results.append(SyncResult(tournament.id, tournament.name, source.label, '', error=str(e)))
    return results


_SITE_HOSTS = {'challonge.com': 'challonge', 'start.gg': 'startgg'}


def site_for_link(value: str) -> Optional[str]:
    """The site key a pasted bracket link points at, or None for a bare id."""
    value = (value or '').strip()
    if not value:
        return None
    host = (urlparse(value if '://' in value else f'https://{value}').hostname or '').lower()
    for domain, key in _SITE_HOSTS.items():
        if host == domain or host.endswith('.' + domain):
            return key
    return None


async def link(source_key: str, tournament: Tournament, id_or_url: str, actor: User) -> str:
    """Link ``tournament`` to a bracket on ``source_key`` and mirror it."""
    return await by_key(source_key).link(tournament, id_or_url, actor)


async def unlink(tournament: Tournament, actor: User) -> str:
    """Detach ``tournament`` from whichever live site it follows; returns that site's label."""
    source = await source_for(tournament)
    if source is None:
        raise ValueError(f"{tournament.name} isn't linked to a bracket site.")
    await source.unlink(tournament, actor)
    return source.label


async def unmatched_entrant_counts(tournaments: List[Tournament]) -> Dict[int, int]:
    """Linked-bracket entrants still waiting for a Wizzrobe user, per tournament id.

    A soft read for the Tournaments board: a site that fails just shows no count.
    """
    counts: Dict[int, int] = {}
    for source in await live_sources():
        ids = [t.id for t in tournaments if source.linked(t)]
        if not ids:
            continue
        try:
            counts.update(await source.count_unmatched(ids))
        except Exception:
            logger.warning('Unmatched-entrant count failed for %s', source.label, exc_info=True)
    return counts


def site_labels() -> Dict[str, str]:
    return {s.key: s.label for s in _SOURCES}
