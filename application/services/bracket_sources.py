"""
Bracket sources - the shared contract for brackets that live on another site.

Each external bracket site a tournament can follow (start.gg today; Challonge
next) is one :class:`BracketSource`. Callers that touch "a linked bracket" loop
over :func:`sources` instead of naming a site, so a new site plugs in here
rather than adding another hand-written call at every spot:

* match confirmation calls :func:`on_match_confirmed`,
* My Schedule lists :func:`list_bookable_for_user` and books through
  :func:`schedule`,
* Admin → Tournaments syncs through :func:`sync_tournament` / :func:`sync_all`.

Challonge is registered for syncing and booking; its result push and My
Schedule rows still run through ``ChallongeService`` directly until it moves
onto the rest of the contract. Native brackets live in Wizzrobe, so they have nothing to sync.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol

from application.services.auth_service import AuthService
from application.services.bracket_service import BracketService
from application.services.challonge_service import ChallongeService
from application.services.feature_flag_service import FeatureFlagService
from application.services.round_schedule_service import RoundScheduleService
from application.services.startgg_service import StartggService
from application.utils.match_labels import challonge_round_label
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
    # "Book between Sat 6 pm and Sun 11 pm", from the tournament's round schedule.
    window_text: Optional[str] = None


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
    best_of: int = 1
    game_number: int = 1

    @property
    def ready(self) -> bool:
        return not self.unlinked

    @property
    def matchup(self) -> str:
        return ' vs '.join(self.player_names)


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
        rows = await StartggService().list_unbooked_sets_for_user(user)
        rounds = RoundScheduleService()
        out = []
        for row in rows:
            me_first = row.entrant1.user_id == user.id
            opponent = row.entrant2 if me_first else row.entrant1
            out.append(BookableMatch(
                source=self.key, source_label=self.label, key=row.id,
                tournament=row.tournament,
                round_label=row.round_text or f'Round {row.round}',
                opponent_name=opponent.name,
                player_ids=[row.entrant1.user_id, row.entrant2.user_id],
                window_text=await rounds.describe_for_startgg_set(row),
            ))
        return out

    async def schedule(
        self, key: int, scheduled_date: str, scheduled_time: str, actor: User,
        *, allow_outside_round: bool = False,
    ) -> Match:
        return await StartggService().schedule_set(
            key, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
        )

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
        return []

    async def schedule(
        self, key: int, scheduled_date: str, scheduled_time: str, actor: User,
        *, allow_outside_round: bool = False,
    ) -> Match:
        return await ChallongeService().schedule_challonge_match(
            key, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
        )

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
    return await by_key(source_key).schedule(
        key, scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
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


async def list_unbooked(actor: Optional[User]) -> List[UnbookedMatch]:
    """Every open bracket matchup with no game booked that ``actor`` may book.

    Staff see the whole community and a tournament admin their own tournaments;
    anyone else sees nothing. Grouped by tournament (by name), each source's
    rows in bracket order.
    """
    scope = await _bookable_scope(actor)
    if scope == []:
        return []
    live = await FeatureFlagService().enabled_flags()
    rounds = RoundScheduleService()
    out: List[UnbookedMatch] = []

    if FeatureFlag.CHALLONGE in live:
        for cm in await ChallongeService().list_unbooked_matches(scope):
            unlinked: List[str] = []
            p1, p2 = cm.participant1, cm.participant2
            names = [_side(p.user_id, p.name or 'TBD', unlinked) for p in (p1, p2)]
            out.append(UnbookedMatch(
                source='challonge', source_label='Challonge', key=cm.id,
                tournament=cm.tournament,
                round_label='Group stage' if cm.group_stage else challonge_round_label(cm.round),
                player_names=names, player_ids=[p1.user_id, p2.user_id], unlinked=unlinked,
                window_text=await rounds.describe_for_challonge_match(cm),
            ))

    if FeatureFlag.STARTGG in live:
        for row in await StartggService().list_unbooked_sets(scope):
            unlinked = []
            e1, e2 = row.entrant1, row.entrant2
            names = [_side(e.user_id, e.name, unlinked) for e in (e1, e2)]
            out.append(UnbookedMatch(
                source='startgg', source_label='start.gg', key=row.id,
                tournament=row.tournament,
                round_label=row.round_text or f'Round {row.round}',
                player_names=names, player_ids=[e1.user_id, e2.user_id], unlinked=unlinked,
                window_text=await rounds.describe_for_startgg_set(row),
            ))

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
            label = f'{bm.bracket.name} · {base}'
            if best_of > 1:
                label += f' · game {number} of {best_of}'
            out.append(UnbookedMatch(
                source='bracket', source_label='Bracket', key=bm.id,
                tournament=bm.bracket.tournament, round_label=label,
                player_names=names, player_ids=[a.user_id, b.user_id], unlinked=unlinked,
                window_text=await rounds.describe_for_bracket_match(bm),
                bracket_id=bm.bracket_id, best_of=best_of, game_number=number,
            ))

    out.sort(key=lambda u: (u.tournament.name.lower(), u.tournament.id))
    return out


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


def site_labels() -> Dict[str, str]:
    return {s.key: s.label for s in _SOURCES}
