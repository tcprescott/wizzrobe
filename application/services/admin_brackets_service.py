"""The app's Admin → Brackets screen: every native bracket stage, and one stage's
next step with what that step needs.

Screen-shaped reads for the new app over :class:`BracketService`, so the website's
stage table and the app agree on which step a stage is up to.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from application.bracket_labels import (
    FORMAT_OPTIONS,
    config_summary,
    format_label,
    match_label,
    round_display_names,
    stage_label,
    state_label,
)
from application.errors import NotFoundError
from application.feature_flags import requires_feature
from application.repositories import TournamentRepository
from application.services.async_qualifier import AsyncQualifierService
from application.services.auth_service import AuthService
from application.services.bracket_service import BracketService
from models import (
    Bracket,
    BracketEntrantStatus,
    BracketEntry,
    BracketEntryStatus,
    BracketFormat,
    BracketMatch,
    BracketMatchState,
    BracketState,
    FeatureFlag,
    User,
)

ELIM_FORMATS = (BracketFormat.SINGLE_ELIM, BracketFormat.DOUBLE_ELIM)
_STAFF_ONLY = "Only Staff can manage brackets"


@dataclass
class NextStep:
    """What the stage is up to: ``key`` picks the button, ``title``/``detail`` say why."""

    key: Optional[str]
    title: str
    detail: str
    done: int
    total: int


@dataclass
class StageRow:
    id: int
    tournament_id: int
    name: str
    stage_order: int
    stage_label: str
    format: str
    format_label: str
    state: str
    state_label: str
    config: List[str]
    next_step: NextStep
    can_complete: bool
    can_advance: bool


@dataclass
class TournamentStages:
    id: int
    name: str
    is_active: bool
    stages: List[StageRow]


@dataclass
class BracketsBoard:
    tournaments: List[TournamentStages]
    formats: List[Tuple[str, str]]


@dataclass
class SeedEntry:
    id: int
    entrant_id: int
    name: str
    seed: Optional[int]
    group_number: Optional[int]
    status: str
    final_rank: Optional[int]


@dataclass
class RosterEntrant:
    id: int
    name: str
    user_id: Optional[int]
    status: str
    enrolled: bool


@dataclass
class Side:
    entry_id: Optional[int]
    name: str
    score: Optional[int]


@dataclass
class StageMatch:
    id: int
    label: str
    round: int
    state: str
    best_of: Optional[int]
    side1: Side
    side2: Side
    winner_entry_id: Optional[int]
    forfeit: bool


@dataclass
class StageDetail:
    row: StageRow
    tournament_name: str
    entries: List[SeedEntry]
    roster: List[RosterEntrant]
    matches: List[StageMatch]


@dataclass
class QualifierSeeding:
    seeds: Dict[int, int]
    unmatched: List[str]


def _next_step(
    bracket: Bracket, entries: List[BracketEntry], matches: List[BracketMatch], can_advance: bool,
    champion: Optional[str], roster_waiting: int,
) -> NextStep:
    played = sum(1 for m in matches if m.state == BracketMatchState.COMPLETE)
    total = len(matches)
    active_entries = [e for e in entries if e.status != BracketEntryStatus.DROPPED]
    if bracket.state == BracketState.DRAFT:
        enrolled = len(active_entries)
        waiting = f" · {roster_waiting} on the roster aren't in yet" if roster_waiting else ''
        if enrolled < 2:
            return NextStep('enroll', 'Add entrants', f'{enrolled} enrolled; a stage needs at least 2{waiting}', enrolled, max(enrolled + roster_waiting, 2))
        return NextStep('start', 'Enroll and seed, then start', f'{enrolled} enrolled{waiting}', enrolled, enrolled + roster_waiting)
    if bracket.state == BracketState.ACTIVE:
        open_now = sum(1 for m in matches if m.state == BracketMatchState.OPEN)
        if open_now:
            return NextStep('report', 'Report results', f'{played} of {total} matches reported · {open_now} open', played, total)
        if bracket.format in ELIM_FORMATS:
            return NextStep(None, 'Waiting on matches', f'{played} of {total} matches reported', played, total)
        return NextStep('complete', 'Complete the stage', f'All {total} matches reported; completing ranks the field', played, total)
    if bracket.state == BracketState.COMPLETE:
        detail = f'Won by {champion}' if champion else f'{total} matches played'
        if can_advance:
            return NextStep('advance', 'Advance to the next stage', detail, total, total)
        return NextStep(None, 'Complete', detail, total, total)
    return NextStep(None, 'Cancelled', 'Abandoned: results kept, nobody ranked', played, total)


class AdminBracketsService:
    def __init__(self) -> None:
        self.brackets = BracketService()

    async def _ensure_staff(self, actor: User) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), _STAFF_ONLY)

    async def _rows(self, tournament_id: int, brackets: List[Bracket]) -> Dict[int, Tuple[StageRow, List[BracketEntry], List[BracketMatch]]]:
        loaded = {b.stage_order: (b, await self.brackets.list_entries(b.id)) for b in brackets}
        entrants = await self.brackets.list_entrants(tournament_id)
        names = {en.id: en.display_name for en in entrants}
        live_entrants = {en.id for en in entrants if en.status != BracketEntrantStatus.DROPPED}
        out = {}
        for bracket in brackets:
            entries = loaded[bracket.stage_order][1]
            matches = await self.brackets.list_matches(bracket.id)
            successor = loaded.get(bracket.stage_order + 1)
            can_advance = (
                bracket.state == BracketState.COMPLETE
                and successor is not None
                and successor[0].state == BracketState.DRAFT
                and not successor[1]
                and bool((successor[0].config or {}).get('advancement'))
            )
            champion = None
            if bracket.state == BracketState.COMPLETE:
                winner = next((e for e in entries if e.final_rank == 1), None)
                champion = names.get(winner.entrant_id) if winner else None
            enrolled_ids = {e.entrant_id for e in entries}
            roster_waiting = len(live_entrants - enrolled_ids) if bracket.state == BracketState.DRAFT else 0
            row = StageRow(
                id=bracket.id,
                tournament_id=tournament_id,
                name=bracket.name,
                stage_order=bracket.stage_order,
                stage_label=stage_label(bracket.stage_order),
                format=bracket.format.value,
                format_label=format_label(bracket.format),
                state=bracket.state.value,
                state_label=state_label(bracket.state),
                config=config_summary(bracket),
                next_step=_next_step(bracket, entries, matches, can_advance, champion, roster_waiting),
                can_complete=bracket.state == BracketState.ACTIVE and bracket.format not in ELIM_FORMATS,
                can_advance=can_advance,
            )
            out[bracket.id] = (row, entries, matches)
        return out

    @requires_feature(FeatureFlag.BRACKETS)
    async def board(self, actor: User) -> BracketsBoard:
        await self._ensure_staff(actor)
        tournaments = [t for t in await TournamentRepository.get_all() if not t.challonge_tournament_id]
        tournaments.sort(key=lambda t: (not t.is_active, t.name.lower()))
        result = []
        for t in tournaments:
            brackets = await self.brackets.list_brackets(t.id)
            rows = await self._rows(t.id, brackets)
            result.append(TournamentStages(t.id, t.name, t.is_active, [rows[b.id][0] for b in brackets]))
        return BracketsBoard(result, list(FORMAT_OPTIONS.items()))

    @requires_feature(FeatureFlag.BRACKETS)
    async def stage(self, actor: User, bracket_id: int) -> StageDetail:
        await self._ensure_staff(actor)
        bracket = await self.brackets.get_bracket(bracket_id)
        if bracket is None:
            raise NotFoundError('That bracket stage no longer exists.')
        tournament = await TournamentRepository.get_by_id(bracket.tournament_id)
        brackets = await self.brackets.list_brackets(bracket.tournament_id)
        row, entries, matches = (await self._rows(bracket.tournament_id, brackets))[bracket.id]
        entrants = await self.brackets.list_entrants(bracket.tournament_id)
        by_entrant = {en.id: en for en in entrants}
        entry_name = {e.id: (by_entrant[e.entrant_id].display_name if e.entrant_id in by_entrant else f'Entry {e.id}') for e in entries}
        enrolled = {e.entrant_id for e in entries}
        seeds = sorted(
            (
                SeedEntry(e.id, e.entrant_id, entry_name[e.id], e.seed, e.group_number, e.status.value, e.final_rank)
                for e in entries
            ),
            key=lambda s: (s.seed is None, s.seed or 0, s.name.lower()),
        )
        roster = sorted(
            (RosterEntrant(en.id, en.display_name, en.user_id, en.status.value, en.id in enrolled) for en in entrants),
            key=lambda r: r.name.lower(),
        )
        names = round_display_names(matches, bracket.format)
        shown = [m for m in matches if m.state != BracketMatchState.PENDING] if bracket.state != BracketState.DRAFT else []
        stage_matches = [
            StageMatch(
                id=m.id,
                label=match_label(m, names),
                round=m.round,
                state=m.state.value,
                best_of=m.best_of,
                side1=Side(m.entry1_id, entry_name.get(m.entry1_id, 'Bye') if m.entry1_id else 'TBD', m.entry1_score),
                side2=Side(m.entry2_id, entry_name.get(m.entry2_id, 'Bye') if m.entry2_id else 'TBD', m.entry2_score),
                winner_entry_id=m.winner_id,
                forfeit=m.forfeit,
            )
            for m in sorted(shown, key=lambda m: (m.state != BracketMatchState.OPEN, abs(m.round), m.round < 0, m.position))
        ]
        return StageDetail(row, tournament.name if tournament else '', seeds, roster, stage_matches)

    @requires_feature(FeatureFlag.BRACKETS)
    async def seeds_from_qualifier(self, actor: User, bracket_id: int, qualifier_id: int) -> QualifierSeeding:
        """Seeds for the stage's entries from a qualifier leaderboard, best score first,
        matched by linked account; entries the board doesn't cover are listed back."""
        await self._ensure_staff(actor)
        bracket = await self.brackets.get_bracket(bracket_id)
        if bracket is None:
            raise NotFoundError('That bracket stage no longer exists.')
        board = await AsyncQualifierService().get_leaderboard(actor, qualifier_id)
        rank_of_user = {row.user_id: i for i, row in enumerate(board)}
        entrants = {en.id: en for en in await self.brackets.list_entrants(bracket.tournament_id)}
        ranked: List[Tuple[int, BracketEntry]] = []
        unmatched: List[str] = []
        for entry in await self.brackets.list_entries(bracket.id):
            entrant = entrants.get(entry.entrant_id)
            user_id = entrant.user_id if entrant is not None else None
            if user_id is not None and user_id in rank_of_user:
                ranked.append((rank_of_user[user_id], entry))
            else:
                unmatched.append(entrant.display_name if entrant else f'Entry {entry.id}')
        if not ranked:
            raise ValueError('No enrolled entrant is linked to a player on that leaderboard.')
        ranked.sort(key=lambda pair: pair[0])
        return QualifierSeeding({entry.id: i for i, (_rank, entry) in enumerate(ranked, start=1)}, unmatched)
