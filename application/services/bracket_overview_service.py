"""The whole of one tournament's bracket in one read, whatever site runs it.

Agents (the MCP ``get_tournament_bracket`` tool) and REST
(``GET /api/tournaments/{id}/bracket``) want the full picture of a bracket in a
single answer: its phases and pools, every set with its two sides, where each
side comes from and where the winner and loser go, scores and results, the
Wizzrobe matches booked for it, and the standings. This service flattens a
native bracket, a start.gg mirror and a Challonge mirror into one shape.

It reads only what Wizzrobe already holds. The start.gg and Challonge mirrors are
kept fresh by their sync workers, so nothing here calls either site; that is a
rule, because Challonge's API is metered (``ChallongeApiUsage``). Challonge's
mirror keeps no score line of its own, so a Challonge set's scores come from its
booked Wizzrobe games when it has them.

Ids are the ones the booking tools take: a set's ``id`` is the
``BracketMatch``/``StartggSet``/``ChallongeMatch`` row id, the same ``key``
``list_unbooked_matches`` reports.

Visibility follows the public bracket pages: a native tournament with no
published stage is not found to anyone but Staff, and an inactive tournament is
not found to anyone but its admins. Staff and tournament admins also get each
entrant's Wizzrobe ``user_id``, the account mapping they fix on the Entrants
dialogs; everyone else gets the bracket without it.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from application.errors import require_found
from application.repositories.match_repository import MatchRepository
from application.services.auth_service import AuthService
from application.services.bracket_engines.round_names import RoundNode, round_names
from application.services.bracket_service import BracketService
from application.services.bracket_set_service import BracketSetService
from application.services.challonge_service import ChallongeService
from application.services.match.match_status import resolve as resolve_status
from application.services.startgg_service import StartggService
from application.services.tournament_service import TournamentService
from application.utils.match_labels import challonge_round_label
from models import (
    Bracket,
    BracketFormat,
    BracketMatch,
    BracketMatchState,
    Match,
    Tournament,
    User,
)

SOURCE_NATIVE = 'native'
SOURCE_STARTGG = 'startgg'
SOURCE_CHALLONGE = 'challonge'
SET_STATES = ('pending', 'open', 'complete')
_ELIM_FORMATS = (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM)


@dataclass
class SlotView:
    """One side of a set: who is in it, or what will fill it."""

    entrant_id: Optional[int] = None
    name: Optional[str] = None
    user_id: Optional[int] = None
    score: Optional[int] = None
    # "Winner of A3" while the side is undecided.
    placeholder: Optional[str] = None
    from_set_id: Optional[int] = None
    from_outcome: Optional[str] = None


@dataclass
class FeedView:
    set_id: int
    slot: Optional[int] = None


@dataclass
class BookingView:
    match_id: int
    status: str
    scheduled_at: Optional[datetime] = None
    game: Optional[str] = None
    stage: Optional[str] = None


@dataclass
class SetView:
    id: int
    round: Optional[int]
    round_label: str
    state: str
    slots: List[SlotView]
    source_id: Optional[str] = None
    identifier: Optional[str] = None
    best_of: Optional[int] = None
    winner_entrant_id: Optional[int] = None
    forfeit: bool = False
    completed_at: Optional[datetime] = None
    winner_to: Optional[FeedView] = None
    loser_to: Optional[FeedView] = None
    bookings: List[BookingView] = field(default_factory=list)


@dataclass
class StandingView:
    entrant_id: int
    name: str
    placement: Optional[int] = None
    wins: int = 0
    losses: int = 0
    draws: int = 0
    points: Optional[float] = None


@dataclass
class PoolView:
    name: str
    id: Optional[int] = None
    format: Optional[str] = None
    complete: bool = False
    sets_total: int = 0
    sets_complete: int = 0
    standings: List[StandingView] = field(default_factory=list)
    sets: List[SetView] = field(default_factory=list)


@dataclass
class PhaseView:
    id: int
    name: str
    order: int
    pools: List[PoolView]
    source_id: Optional[str] = None
    format: Optional[str] = None
    state: Optional[str] = None
    bookable: Optional[bool] = None


@dataclass
class EntrantView:
    id: int
    name: str
    user_id: Optional[int] = None
    status: Optional[str] = None


@dataclass
class TournamentBracketView:
    tournament_id: int
    tournament: str
    source: str
    phases: List[PhaseView]
    entrants: List[EntrantView]
    url: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    filtered: bool = False


def _value(enum_or_str: Any) -> Optional[str]:
    return getattr(enum_or_str, 'value', enum_or_str)


def _outcome(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    return 'loser' if label.lower().startswith('loser') else 'winner'


def _booking(match: Match, game: Optional[str], game_state: Any = None) -> BookingView:
    stage = getattr(match, 'stage', None)
    return BookingView(
        match_id=match.id,
        status=resolve_status(match=match, game_state=game_state).value,
        scheduled_at=match.scheduled_at,
        game=game,
        stage=stage.name if stage is not None and not isinstance(stage, int) else None,
    )


@dataclass
class _Filters:
    phase: Optional[str] = None
    pool: Optional[str] = None
    state: Optional[str] = None
    entrant: Optional[str] = None
    include_sets: bool = True

    @property
    def active(self) -> bool:
        return any((self.phase, self.pool, self.state, self.entrant)) or not self.include_sets

    def phase_matches(self, phase: PhaseView) -> bool:
        if not self.phase:
            return True
        wanted = self.phase.strip().lower()
        return wanted in (str(phase.id), (phase.source_id or '').lower(), phase.name.lower())

    def pool_matches(self, pool: PoolView) -> bool:
        if not self.pool:
            return True
        wanted = self.pool.strip().lower()
        return wanted in (str(pool.id), pool.name.lower())

    def set_matches(self, row: SetView) -> bool:
        if self.state and row.state != self.state:
            return False
        if self.entrant:
            wanted = self.entrant.strip().lower()
            return any(
                (s.name and wanted in s.name.lower()) or str(s.entrant_id) == wanted
                for s in row.slots
            )
        return True


class BracketOverviewService:
    """Read-only: one tournament's bracket, flattened. See the module docstring."""

    def __init__(self) -> None:
        self.match_repository = MatchRepository()

    async def get_tournament_bracket(
        self,
        viewer: Optional[User],
        tournament_id: int,
        *,
        phase: Optional[str] = None,
        pool: Optional[str] = None,
        state: Optional[str] = None,
        entrant: Optional[str] = None,
        include_sets: bool = True,
    ) -> TournamentBracketView:
        """The tournament's bracket, trimmed by the optional filters.

        ``phase`` and ``pool`` match an id or a name, ``state`` is one of
        ``pending``/``open``/``complete``, and ``entrant`` keeps the sets a
        name (or entrant id) plays in. Pool counts and standings always cover
        the whole pool, whatever the set filters drop. Raises ``ValueError``
        for a bad ``state`` and ``NotFoundError`` when the viewer can't see it.
        """
        if state is not None and state not in SET_STATES:
            raise ValueError(f"state must be one of {', '.join(SET_STATES)}")
        filters = _Filters(phase=phase, pool=pool, state=state, entrant=entrant, include_sets=include_sets)
        tournament = require_found(
            await TournamentService().get_for_viewer(viewer, tournament_id), 'Tournament',
        )
        show_users = await self._sees_accounts(viewer, tournament.id)
        if tournament.startgg_event_slug:
            view = await self._startgg(tournament, show_users)
        elif tournament.challonge_tournament_id:
            view = await self._challonge(tournament, show_users)
        else:
            view = await self._native(viewer, tournament, show_users)
        return self._apply(view, filters)

    @staticmethod
    async def _sees_accounts(viewer: Optional[User], tournament_id: int) -> bool:
        return await AuthService.is_staff(viewer) or await AuthService.is_tournament_admin(viewer, tournament_id)

    @staticmethod
    def _apply(view: TournamentBracketView, filters: _Filters) -> TournamentBracketView:
        if not filters.active:
            return view
        phases = []
        for phase in view.phases:
            if not filters.phase_matches(phase):
                continue
            pools = [p for p in phase.pools if filters.pool_matches(p)]
            for p in pools:
                p.sets = [s for s in p.sets if filters.set_matches(s)] if filters.include_sets else []
            if filters.entrant or filters.state:
                pools = [p for p in pools if p.sets or not filters.include_sets]
            if pools or not (filters.pool or filters.entrant or filters.state):
                phase.pools = pools
                phases.append(phase)
        view.phases = phases
        view.filtered = True
        return view

    # ------------------------------------------------------------------
    # Booked games, for the sites whose set links to the game that leads it
    # ------------------------------------------------------------------
    async def _bookings_by_lead(self, lead_ids: Iterable[Optional[int]]) -> Dict[int, List[BookingView]]:
        firsts = sorted({i for i in lead_ids if i})
        if not firsts:
            return {}
        games = await self.match_repository.games_of_sets_with_stage(firsts)
        labels = await BracketSetService().labels_for(games)
        out: Dict[int, List[BookingView]] = {}
        for game in games:
            lead = getattr(game, 'set_first_id', None) or game.id
            out.setdefault(lead, []).append(_booking(game, labels.get(game.id)))
        for rows in out.values():
            rows.sort(key=lambda b: (b.game or '', b.scheduled_at or datetime.max, b.match_id))
        return out

    @staticmethod
    def _best_of(bookings: Sequence[BookingView], lead: Optional[Match]) -> Optional[int]:
        if lead is not None and getattr(lead, 'set_best_of', None):
            return lead.set_best_of
        return 1 if bookings else None

    # ------------------------------------------------------------------
    # start.gg
    # ------------------------------------------------------------------
    async def _startgg(self, tournament: Tournament, show_users: bool) -> TournamentBracketView:
        event = await StartggService().get_event_view(tournament.id)
        all_sets = [row for phase in event['phases'] for pool in phase['pools'] for row in pool['sets']]
        by_remote = {row.set_id: row.id for row in all_sets}
        leads = {row.match_id: row.match for row in all_sets if row.match_id}  # type: ignore[attr-defined]
        bookings = await self._bookings_by_lead(leads)

        sets: Dict[int, SetView] = {}
        for row in all_sets:
            slots = []
            for n in (1, 2):
                ent = getattr(row, f'entrant{n}')
                label = getattr(row, f'slot{n}_label')
                slots.append(SlotView(
                    entrant_id=ent.id if ent else None,
                    name=ent.name if ent else None,
                    user_id=ent.user_id if ent and show_users else None,
                    score=getattr(row, f'score{n}'),
                    placeholder=None if ent else (label or 'TBD'),
                    from_set_id=by_remote.get(getattr(row, f'slot{n}_prereq') or ''),
                    from_outcome=_outcome(label),
                ))
            booked = bookings.get(row.match_id, [])  # type: ignore[attr-defined]
            sets[row.id] = SetView(
                id=row.id, source_id=row.set_id, identifier=row.identifier,
                round=row.round, round_label=row.round_text or f'Round {row.round}',
                state=_value(row.state) or 'pending', slots=slots,
                winner_entrant_id=row.winner_id,  # type: ignore[attr-defined]
                completed_at=row.completed_at, bookings=booked,
                best_of=self._best_of(booked, leads.get(row.match_id)),  # type: ignore[attr-defined]
            )
        self._link_forward(sets.values())

        phases = []
        for item in event['phases']:
            phase = item['phase']
            pools = []
            for pool_view in item['pools']:
                pool = pool_view['pool']
                pools.append(PoolView(
                    id=pool.id, name=pool.identifier, format=pool.bracket_type or phase.bracket_type,
                    complete=pool.complete, sets_total=pool_view['total'], sets_complete=pool_view['played'],
                    standings=[
                        StandingView(
                            entrant_id=r['entrant'].id, name=r['entrant'].name,
                            placement=r['placement'], wins=r['wins'], losses=r['losses'],
                        )
                        for r in pool_view['standings']
                    ],
                    sets=[sets[row.id] for row in pool_view['sets']],
                ))
            phases.append(PhaseView(
                id=phase.id, source_id=phase.phase_id, name=phase.name, order=phase.phase_order,
                format=phase.bracket_type, state=phase.state, bookable=item['bookable'], pools=pools,
            ))
        return TournamentBracketView(
            tournament_id=tournament.id, tournament=tournament.name, source=SOURCE_STARTGG,
            url=event['event_url'], last_synced_at=event['last_synced_at'], phases=phases,
            entrants=[
                EntrantView(id=e.id, name=e.name, user_id=e.user_id if show_users else None)  # type: ignore[attr-defined]
                for e in event['entrants']
            ],
        )

    # ------------------------------------------------------------------
    # Challonge (mirror only: no Challonge API calls)
    # ------------------------------------------------------------------
    async def _challonge(self, tournament: Tournament, show_users: bool) -> TournamentBracketView:
        mirror = await ChallongeService().get_bracket_mirror(tournament.id)
        participants = {p.id: p for p in mirror['participants']}
        rows = mirror['matches']
        by_remote = {cm.challonge_match_id: cm.id for cm in rows}
        lead_ids = [cm.match_id for cm in rows]  # type: ignore[attr-defined]
        bookings = await self._bookings_by_lead(lead_ids)
        leads = {m.id: m for m in await self.match_repository.get_by_ids_with_players(
            [i for i in lead_ids if i],
        )}

        def name_of(pk: Optional[int]) -> Optional[str]:
            p = participants.get(pk) if pk else None
            return (p.name or f'Participant {p.challonge_participant_id}') if p else None

        groups: Dict[bool, List[SetView]] = {True: [], False: []}
        for cm in sorted(rows, key=lambda c: (not c.group_stage, abs(c.round or 0), c.round or 0, c.identifier or '', c.id)):
            lead = leads.get(cm.match_id)  # type: ignore[attr-defined]
            booked = bookings.get(cm.match_id, [])  # type: ignore[attr-defined]
            ranks = self._lead_scores(lead)
            slots = []
            for n in (1, 2):
                pk = getattr(cm, f'participant{n}_id')
                label = getattr(cm, f'slot{n}_label')
                p = participants.get(pk) if pk else None
                slots.append(SlotView(
                    entrant_id=pk, name=name_of(pk),
                    user_id=p.user_id if p and show_users else None,  # type: ignore[attr-defined]
                    score=ranks.get(p.user_id) if p else None,  # type: ignore[attr-defined]
                    placeholder=None if pk else (label or 'TBD'),
                    from_set_id=by_remote.get(getattr(cm, f'slot{n}_prereq') or ''),
                    from_outcome=_outcome(label),
                ))
            groups[cm.group_stage].append(SetView(
                id=cm.id, source_id=cm.challonge_match_id, identifier=cm.identifier,
                round=cm.round, round_label=challonge_round_label(cm.round) or 'Group stage',
                state=_value(cm.state) or 'pending', slots=slots,
                winner_entrant_id=cm.winner_participant_id,  # type: ignore[attr-defined]
                bookings=booked, best_of=self._best_of(booked, lead),
            ))
        self._link_forward([s for g in groups.values() for s in g])

        phases = []
        for order, (in_groups, name) in enumerate(((True, 'Group stage'), (False, 'Final stage'))):
            sets = groups[in_groups]
            if not sets:
                continue
            done = sum(1 for s in sets if s.state == 'complete')
            phases.append(PhaseView(
                id=order + 1, name=name, order=order, format=None if in_groups else tournament.challonge_tournament_type,
                state='complete' if done == len(sets) else 'active',
                pools=[PoolView(
                    name=name, complete=done == len(sets), sets_total=len(sets), sets_complete=done,
                    standings=self._tally(sets), sets=sets,
                )],
            ))
        return TournamentBracketView(
            tournament_id=tournament.id, tournament=tournament.name, source=SOURCE_CHALLONGE,
            url=mirror['url'], last_synced_at=mirror['last_synced_at'], phases=phases,
            entrants=[
                EntrantView(
                    id=p.id, name=name_of(p.id) or '', status='active' if p.active else 'dropped',
                    user_id=p.user_id if show_users else None,  # type: ignore[attr-defined]
                )
                for p in sorted(participants.values(), key=lambda p: (p.name or '').lower())
            ],
        )

    @staticmethod
    def _lead_scores(lead: Optional[Match]) -> Dict[int, int]:
        """``user_id -> games won`` from a confirmed best-of-1 booking.

        Challonge's mirror keeps no score line, so a confirmed single game
        reads 1-0 to its winner. A set's game count isn't summed here: the
        set's own result is the winner, and its games carry their statuses.
        """
        if lead is None or not lead.confirmed_at or (getattr(lead, 'set_best_of', None) or 1) > 1:
            return {}
        ranks = {p.user_id: p.finish_rank for p in lead.players if p.finish_rank}  # type: ignore[attr-defined]
        if not ranks:
            return {}
        best = min(ranks.values())
        return {uid: 1 if rank == best else 0 for uid, rank in ranks.items()}

    # ------------------------------------------------------------------
    # Native brackets
    # ------------------------------------------------------------------
    async def _native(
        self, viewer: Optional[User], tournament: Tournament, show_users: bool,
    ) -> TournamentBracketView:
        service = BracketService()
        require_found(await service.get_visible_tournament(viewer, tournament.id), 'Tournament')
        entrants = {e.id: e for e in await service.list_entrants(tournament.id)}
        phases = [
            await self._native_phase(service, bracket, entrants, show_users)
            for bracket in await service.list_visible_brackets(viewer, tournament.id)
        ]
        return TournamentBracketView(
            tournament_id=tournament.id, tournament=tournament.name, source=SOURCE_NATIVE,
            url=f'/tournament/{tournament.id}/brackets', phases=phases,
            entrants=[
                EntrantView(
                    id=e.id, name=e.display_name, status=_value(e.status),
                    user_id=e.user_id if show_users else None,  # type: ignore[attr-defined]
                )
                for e in sorted(entrants.values(), key=lambda e: e.display_name.lower())
            ],
        )

    async def _native_phase(
        self, service: BracketService, bracket: Bracket, entrants: Dict[int, Any], show_users: bool,
    ) -> PhaseView:
        entries = {e.id: e for e in await service.list_entries(bracket.id)}
        matches = await service.list_matches(bracket.id)
        elimination = bracket.format in _ELIM_FORMATS
        names = round_names(
            [RoundNode(id=m.id, round=m.round, winner_to_id=m.winner_to_id, loser_to_id=m.loser_to_id)  # type: ignore[attr-defined]
             for m in matches],
            double_elim=bracket.format == BracketFormat.DOUBLE_ELIM,
            elimination=elimination,
        )
        by_id = {m.id: m for m in matches}

        def entrant_of(entry_id: Optional[int]) -> Optional[Any]:
            entry = entries.get(entry_id) if entry_id else None
            return entrants.get(entry.entrant_id) if entry else None  # type: ignore[attr-defined]

        def label(m: BracketMatch) -> str:
            name = names.get(m.round) or f'Round {m.round}'
            return f'Group {m.group_number} · {name}' if m.group_number else name

        feeders: Dict[Tuple[int, int], Tuple[int, str]] = {}
        for m in matches:
            for target, slot, outcome in (
                (m.winner_to_id, m.winner_to_slot, 'winner'),  # type: ignore[attr-defined]
                (m.loser_to_id, m.loser_to_slot, 'loser'),  # type: ignore[attr-defined]
            ):
                if target and slot:
                    feeders[(target, slot)] = (m.id, outcome)

        pools: Dict[Optional[int], List[SetView]] = {}
        for m in matches:
            slots = []
            for n in (1, 2):
                entrant = entrant_of(getattr(m, f'entry{n}_id'))
                feeder = feeders.get((m.id, n))
                placeholder = None
                if entrant is None and feeder is not None:
                    source = by_id[feeder[0]]
                    placeholder = f'{feeder[1].capitalize()} of {label(source)} #{source.position}'
                slots.append(SlotView(
                    entrant_id=entrant.id if entrant else None,
                    name=entrant.display_name if entrant else None,
                    user_id=entrant.user_id if entrant and show_users else None,
                    score=getattr(m, f'entry{n}_score'),
                    placeholder=placeholder or (None if entrant else 'TBD'),
                    from_set_id=feeder[0] if feeder else None,
                    from_outcome=feeder[1] if feeder else None,
                ))
            games = sorted(m.games, key=lambda g: g.game_number)  # type: ignore[attr-defined]
            numbers = service.numbers_by_time(games)
            best_of = service.resolve_best_of(bracket, m)
            winner = entrant_of(m.winner_id)  # type: ignore[attr-defined]
            pools.setdefault(m.group_number, []).append(SetView(
                id=m.id, identifier=f'{m.round}.{m.position}', round=m.round, round_label=label(m),
                state=_value(m.state) or 'pending', slots=slots, best_of=best_of,
                winner_entrant_id=winner.id if winner else None, forfeit=m.forfeit,
                winner_to=FeedView(m.winner_to_id, m.winner_to_slot) if m.winner_to_id else None,  # type: ignore[attr-defined]
                loser_to=FeedView(m.loser_to_id, m.loser_to_slot) if m.loser_to_id else None,  # type: ignore[attr-defined]
                bookings=[
                    _booking(g.match, f'Game {numbers.get(g.id, g.game_number)} of {best_of}', g.state)
                    for g in games if g.match_id is not None  # type: ignore[attr-defined]
                ],
            ))

        standings = await self._native_standings(service, bracket, entries, entrants, matches)
        if not pools:
            pools[None] = []
        pool_views = []
        for group in sorted(pools, key=lambda g: g or 0):
            sets = pools[group]
            done = sum(1 for s in sets if s.state == BracketMatchState.COMPLETE.value)
            pool_views.append(PoolView(
                name=f'Group {group}' if group else bracket.name,
                format=_value(bracket.format),
                complete=bool(sets) and done == len(sets),
                sets_total=len(sets), sets_complete=done,
                standings=standings.get(group, []) if len(pools) > 1 else [
                    row for rows in standings.values() for row in rows
                ],
                sets=sets,
            ))
        return PhaseView(
            id=bracket.id, name=bracket.name, order=bracket.stage_order,
            format=_value(bracket.format), state=_value(bracket.state), pools=pool_views,
        )

    @staticmethod
    async def _native_standings(
        service: BracketService, bracket: Bracket, entries: Dict[int, Any],
        entrants: Dict[int, Any], matches: Sequence[BracketMatch],
    ) -> Dict[Optional[int], List[StandingView]]:
        if bracket.format not in _ELIM_FORMATS:
            out: Dict[Optional[int], List[StandingView]] = {}
            for group in await service.standings(bracket.id):
                out[group.group_number] = [
                    StandingView(
                        entrant_id=row.entrant_id, name=row.display_name, placement=row.rank,
                        wins=row.wins, losses=row.losses, draws=row.draws, points=row.points,
                    )
                    for row in group.rows
                ]
            return out
        wins: Counter = Counter()
        losses: Counter = Counter()
        for m in matches:
            if m.state != BracketMatchState.COMPLETE or not m.winner_id:  # type: ignore[attr-defined]
                continue
            for entry_id in (m.entry1_id, m.entry2_id):  # type: ignore[attr-defined]
                if entry_id:
                    (wins if entry_id == m.winner_id else losses)[entry_id] += 1  # type: ignore[attr-defined]
        rows = []
        for entry in entries.values():
            entrant = entrants.get(entry.entrant_id)
            if entrant is None:
                continue
            rows.append(StandingView(
                entrant_id=entrant.id, name=entrant.display_name, placement=entry.final_rank,
                wins=wins[entry.id], losses=losses[entry.id],
            ))
        rows.sort(key=lambda r: (r.placement is None, r.placement or 0, -r.wins, r.losses, r.name.lower()))
        return {None: rows}

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------
    @staticmethod
    def _link_forward(sets: Iterable[SetView]) -> None:
        """Fill ``winner_to``/``loser_to`` from the sides' ``from_set_id``."""
        rows = list(sets)
        by_id = {s.id: s for s in rows}
        for row in rows:
            for n, slot in enumerate(row.slots, start=1):
                source = by_id.get(slot.from_set_id) if slot.from_set_id else None
                if source is None:
                    continue
                feed = FeedView(row.id, n)
                if slot.from_outcome == 'loser':
                    source.loser_to = feed
                else:
                    source.winner_to = feed

    @staticmethod
    def _tally(sets: Sequence[SetView]) -> List[StandingView]:
        """Win/loss per entrant from completed sets, for a site whose mirror has no table."""
        record: Dict[int, StandingView] = {}
        for row in sets:
            for slot in row.slots:
                if slot.entrant_id is not None and slot.entrant_id not in record:
                    record[slot.entrant_id] = StandingView(entrant_id=slot.entrant_id, name=slot.name or '')
            if row.state != 'complete' or row.winner_entrant_id is None:
                continue
            for slot in row.slots:
                if slot.entrant_id is None:
                    continue
                if slot.entrant_id == row.winner_entrant_id:
                    record[slot.entrant_id].wins += 1
                else:
                    record[slot.entrant_id].losses += 1
        return sorted(record.values(), key=lambda r: (-r.wins, r.losses, r.name.lower()))


