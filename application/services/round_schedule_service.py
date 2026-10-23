"""Round schedule - Business Logic Layer.

When each stage of a tournament's bracket may be booked: a Challonge group
stage, a start.gg pool phase, each elimination round. Staff set an opens time, a
closes time and optional daily hours per stage (``Tournament.config
['round_schedule']``, keyed as described in :mod:`round_windows`); native
brackets keep theirs on the stage itself (``Bracket.config['rounds']``).

The window is a hard limit for players, enforced at every booking path and by
Suggest a time. Staff and the tournament's admins are told and may book outside
it anyway: :class:`RoundWindowError` carries ``can_override`` so the UI can ask
"Save anyway?" and resubmit with ``allow_outside_round=True``.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from application.errors import require_found
from application.events import EventType
from application.repositories.bracket_repository import BracketRepository
from application.repositories.challonge_repository import ChallongeRepository
from application.repositories.match_repository import MatchRepository
from application.repositories.startgg_repository import StartggRepository
from application.repositories.tournament_repository import TournamentRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.round_windows import (
    GROUP_KEY,
    RoundWindow,
    challonge_group_stage_running,
    elimination_keys,
    placeholder_keys,
    stage_label,
    stage_sort_key,
    window_to_config,
)
from application.services.timezone_service import TimezoneService
from application.services.tournament_config import validate_tournament_config
from application.timezone_context import get_zone
from application.utils.timezone import parse_local_datetime
from models import (
    BracketMatch,
    ChallongeMatch,
    Match,
    StartggPhase,
    StartggSet,
    Tournament,
    User,
)

_ELIMINATION_TYPES = {'SINGLE_ELIMINATION', 'DOUBLE_ELIMINATION'}
_DEFAULT_MATCH_MINUTES = 90
_BEST_OF_CHOICES = (1, 3, 5)


class RoundWindowError(ValueError):
    """A booking time falls outside its stage's window.

    ``can_override`` is true when the actor is staff or one of the tournament's
    admins, who may book outside the window after a warning.
    """

    def __init__(self, message: str, *, can_override: bool) -> None:
        super().__init__(message)
        self.can_override = can_override


@dataclass
class ScheduleStage:
    """One row of a tournament's round schedule."""

    key: str
    label: str
    detail: str
    total: int = 0
    booked: int = 0
    window: Optional[RoundWindow] = None
    # Offered ahead of the bracket: no matches carry this key yet.
    placeholder: bool = False
    best_of: int = 1


@dataclass
class StageWindow:
    """The window a single matchup is booked inside, with its stage's name."""

    label: str
    window: RoundWindow


def _is_double_challonge(tournament: Tournament, rounds: List[int]) -> bool:
    kind = (getattr(tournament, 'challonge_tournament_type', None) or '').lower()
    return 'double' in kind or any(r < 0 for r in rounds)


def _schedule_config(tournament: Tournament) -> Dict[str, dict]:
    return ((tournament.config or {}).get('round_schedule') or {})


class RoundScheduleService:
    def __init__(self) -> None:
        self.tournament_repository = TournamentRepository()
        self.challonge_repository = ChallongeRepository()
        self.startgg_repository = StartggRepository()
        self.bracket_repository = BracketRepository()
        self.match_repository = MatchRepository()
        self.audit_service = AuditService()

    # ------------------------------------------------------------------
    # Staff editor
    # ------------------------------------------------------------------
    async def _editable(self, tournament_id: int, actor: Optional[User]) -> Tournament:
        tournament = require_found(
            await self.tournament_repository.get_by_id(tournament_id), 'Tournament',
        )
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            "You can't change this tournament's round schedule.",
        )
        return tournament

    @staticmethod
    def group_stage_running(remote_tournament: Optional[dict]) -> bool:
        return challonge_group_stage_running(remote_tournament)

    @staticmethod
    def source_label(tournament: Tournament) -> Optional[str]:
        if tournament.startgg_event_slug:
            return 'start.gg'
        if tournament.challonge_tournament_id:
            return 'Challonge'
        return None

    async def list_stages(self, tournament_id: int, actor: Optional[User]) -> List[ScheduleStage]:
        """The tournament's stages in play order, each with its saved window."""
        return await self._stages(await self._editable(tournament_id, actor))

    async def _stages(self, tournament: Tournament) -> List[ScheduleStage]:
        saved = _schedule_config(tournament)
        if tournament.startgg_event_slug:
            stages = await self._startgg_stages(tournament)
        elif tournament.challonge_tournament_id:
            stages = await self._challonge_stages(tournament)
        else:
            raise ValueError('Only Challonge and start.gg tournaments have a round schedule here.')
        for stage in stages:
            stage.window = RoundWindow.from_config(saved.get(stage.key))
            stage.best_of = (saved.get(stage.key) or {}).get('best_of') or 1
        return stages

    async def _challonge_stages(self, tournament: Tournament) -> List[ScheduleStage]:
        matches = await self.challonge_repository.list_matches(tournament)
        rows: Dict[str, ScheduleStage] = {}
        group = [m for m in matches if m.group_stage]
        if group or tournament.challonge_group_stage:
            rows[GROUP_KEY] = ScheduleStage(GROUP_KEY, stage_label(GROUP_KEY), '')
        elim = [m for m in matches if not m.group_stage and m.round is not None]
        rounds = [m.round for m in elim]
        double = _is_double_challonge(tournament, rounds)
        keys = elimination_keys(rounds, double=double)
        for m in group:
            _count(rows[GROUP_KEY], m)
        for m in elim:
            key = keys.get(m.round)
            if key is None:
                continue
            row = rows.setdefault(key, ScheduleStage(key, stage_label(key, round_number=m.round), ''))
            _count(row, m)
        if not elim:
            for key in placeholder_keys(double=double):
                rows.setdefault(key, ScheduleStage(key, stage_label(key), '', placeholder=True))
        for key in _schedule_config(tournament):
            if key not in rows and (key == GROUP_KEY or key[:1] in 'ewlg'):
                rows[key] = ScheduleStage(key, stage_label(key), '', placeholder=True)
        ordered = sorted(rows.values(), key=lambda r: stage_sort_key(r.key))
        for row in ordered:
            row.detail = _detail(row)
        return ordered

    async def _startgg_stages(self, tournament: Tournament) -> List[ScheduleStage]:
        phases = await self.startgg_repository.list_phases(tournament)
        sets = await self.startgg_repository.list_sets(tournament)
        out: List[ScheduleStage] = []
        for phase in phases:
            phase_sets = [s for s in sets if s.pool.phase_id == phase.id]
            if not _is_elimination(phase):
                row = ScheduleStage(phase.phase_id, phase.name, '')
                for s in phase_sets:
                    _count(row, s)
                row.detail = _detail(row)
                out.append(row)
                continue
            keys = _startgg_keys(phase, phase_sets)
            rows: Dict[str, ScheduleStage] = {}
            for s in phase_sets:
                key = keys.get(s.round)
                if key is None:
                    continue
                label = s.round_text or stage_label(key.split(':', 1)[1], round_number=s.round)
                row = rows.setdefault(key, ScheduleStage(key, f'{phase.name}: {label}' if len(phases) > 1 else label, ''))
                _count(row, s)
            for row in sorted(rows.values(), key=lambda r: stage_sort_key(r.key.split(':', 1)[1])):
                row.detail = _detail(row)
                out.append(row)
        return out

    async def save_schedule(
        self, tournament_id: int, entries: Dict[str, dict], actor: User,
    ) -> Tournament:
        """Replace the tournament's whole round schedule.

        ``entries`` maps a stage key to ``{opens, closes, daily_from, daily_until,
        best_of}`` where opens/closes are aware datetimes (or None), the daily
        pair is ``HH:MM`` strings (or None) and ``best_of`` an odd game count. A
        stage left wholly blank is dropped.
        """
        tournament = await self._editable(tournament_id, actor)
        schedule: Dict[str, dict] = {}
        for key, entry in entries.items():
            stored = window_to_config(
                entry.get('opens'), entry.get('closes'),
                entry.get('daily_from') or None, entry.get('daily_until') or None,
            )
            if (entry.get('daily_from') or None) is not None and not entry.get('daily_until'):
                raise ValueError('Daily hours need both a start and an end.')
            if (entry.get('daily_until') or None) is not None and not entry.get('daily_from'):
                raise ValueError('Daily hours need both a start and an end.')
            best_of = int(entry.get('best_of') or 1)
            if best_of > 1:
                stored = {**(stored or {}), 'best_of': best_of}
            if stored:
                schedule[key] = stored
        config = dict(tournament.config or {})
        if schedule:
            config['round_schedule'] = schedule
        else:
            config.pop('round_schedule', None)
        await self.tournament_repository.update(
            tournament, config=validate_tournament_config(config) or None,
        )
        await self.audit_service.write_and_publish(
            actor, AuditActions.TOURNAMENT_ROUND_SCHEDULE_UPDATED,
            {'tournament_id': tournament.id, 'stages': sorted(schedule)},
            EventType.TOURNAMENT_ROUND_SCHEDULE_UPDATED,
        )
        return tournament

    async def set_best_of(
        self, tournament_id: int, stage_key: str, best_of: int, actor: User,
    ) -> ScheduleStage:
        """Set how many games every matchup in one stage plays, leaving its window alone.

        Matches already booked for the stage keep the length they were booked with.
        """
        if best_of not in _BEST_OF_CHOICES:
            raise ValueError('Best of has to be 1, 3 or 5.')
        tournament = await self._editable(tournament_id, actor)
        stage = next((s for s in await self._stages(tournament) if s.key == stage_key), None)
        if stage is None:
            raise ValueError(f"This tournament has no stage '{stage_key}'.")
        schedule = {k: dict(v) for k, v in _schedule_config(tournament).items()}
        entry = schedule.pop(stage_key, {})
        entry.pop('best_of', None)
        if best_of > 1:
            entry['best_of'] = best_of
        if entry:
            schedule[stage_key] = entry
        config = dict(tournament.config or {})
        if schedule:
            config['round_schedule'] = schedule
        else:
            config.pop('round_schedule', None)
        await self.tournament_repository.update(
            tournament, config=validate_tournament_config(config) or None,
        )
        await self.audit_service.write_and_publish(
            actor, AuditActions.TOURNAMENT_ROUND_SCHEDULE_UPDATED,
            {'tournament_id': tournament.id, 'stages': sorted(schedule),
             'stage': stage_key, 'best_of': best_of},
            EventType.TOURNAMENT_ROUND_SCHEDULE_UPDATED,
        )
        stage.best_of = best_of
        return stage

    # ------------------------------------------------------------------
    # Lookups: which window a matchup is booked inside
    # ------------------------------------------------------------------
    async def _challonge_key(self, cm: ChallongeMatch) -> Optional[str]:
        if cm.group_stage:
            return GROUP_KEY
        if cm.round is None:
            return None
        matches = await self.challonge_repository.list_matches(cm.tournament)
        rounds = [m.round for m in matches if not m.group_stage and m.round is not None]
        return elimination_keys(
            rounds, double=_is_double_challonge(cm.tournament, rounds),
        ).get(cm.round)

    async def _startgg_key(self, row: StartggSet) -> Optional[str]:
        phase = row.pool.phase
        if not _is_elimination(phase):
            return phase.phase_id
        sets = [s for s in await self.startgg_repository.list_sets(row.tournament) if s.pool.phase_id == phase.id]
        return _startgg_keys(phase, sets).get(row.round)

    async def best_of_for(
        self, *, challonge_match_id: Optional[int] = None, startgg_set_id: Optional[int] = None,
    ) -> int:
        """How many games a Challonge or start.gg matchup's stage plays; 1 when unset."""
        tournament: Optional[Tournament] = None
        key: Optional[str] = None
        if challonge_match_id is not None:
            cm = await self.challonge_repository.get_match(challonge_match_id)
            if cm is not None and _schedule_config(cm.tournament):
                tournament, key = cm.tournament, await self._challonge_key(cm)
        elif startgg_set_id is not None:
            row = await self.startgg_repository.get_set(startgg_set_id)
            if row is not None and _schedule_config(row.tournament):
                tournament, key = row.tournament, await self._startgg_key(row)
        if tournament is None or key is None:
            return 1
        return (_schedule_config(tournament).get(key) or {}).get('best_of') or 1

    async def window_for_challonge_match(self, cm: ChallongeMatch) -> Optional[StageWindow]:
        saved = _schedule_config(cm.tournament)
        if not saved:
            return None
        return _stage_window(saved, await self._challonge_key(cm), cm.round)

    async def window_for_startgg_set(self, row: StartggSet) -> Optional[StageWindow]:
        saved = _schedule_config(row.tournament)
        if not saved:
            return None
        phase = row.pool.phase
        if not _is_elimination(phase):
            window = RoundWindow.from_config(saved.get(phase.phase_id))
            return StageWindow(phase.name, window) if window else None
        key = await self._startgg_key(row)
        found = _stage_window(saved, key, row.round)
        if found is not None and row.round_text:
            found.label = row.round_text
        return found

    async def window_for_bracket_match(self, bm: BracketMatch) -> Optional[StageWindow]:
        rounds = (bm.bracket.config or {}).get('rounds') or {}
        window = RoundWindow.from_config(rounds.get(str(bm.round)))
        return StageWindow('This round', window) if window else None

    async def window_for_match(self, match: Match) -> Optional[StageWindow]:
        """The window of whatever bracket matchup ``match`` was booked for."""
        first_id = getattr(match, 'set_first_id', None)
        if first_id is not None:
            first = await self.match_repository.get_by_id(first_id, prefetch_relations=False)
            if first is not None:
                match = first
        cm = await self.challonge_repository.get_challonge_match_for_match(match)
        if cm is not None:
            return await self.window_for_challonge_match(cm)
        sets = await self.startgg_repository.sets_for_match(match)
        if sets:
            return await self.window_for_startgg_set(sets[0])
        bm = await self.bracket_repository.get_bracket_match_for_match(match.id)
        if bm is not None:
            return await self.window_for_bracket_match(bm)
        return None

    async def window_for(
        self, *, bracket_match_id: Optional[int] = None,
        challonge_match_id: Optional[int] = None, startgg_set_id: Optional[int] = None,
        match_id: Optional[int] = None,
    ) -> Optional[StageWindow]:
        """The window for whichever matchup reference the caller holds."""
        if bracket_match_id is not None:
            bm = await self.bracket_repository.get_match_with_bracket(bracket_match_id)
            return await self.window_for_bracket_match(bm) if bm is not None else None
        if challonge_match_id is not None:
            cm = await self.challonge_repository.get_match(challonge_match_id)
            return await self.window_for_challonge_match(cm) if cm is not None else None
        if startgg_set_id is not None:
            row = await self.startgg_repository.get_set(startgg_set_id)
            return await self.window_for_startgg_set(row) if row is not None else None
        if match_id is not None:
            match = await self.match_repository.get_by_id(match_id)
            return await self.window_for_match(match) if match is not None else None
        return None

    async def describe_for_challonge_match(self, cm: ChallongeMatch) -> Optional[str]:
        found = await self.window_for_challonge_match(cm)
        return found.window.describe(await _tenant_zone()) if found else None

    async def describe_for_startgg_set(self, row: StartggSet) -> Optional[str]:
        found = await self.window_for_startgg_set(row)
        return found.window.describe(await _tenant_zone()) if found else None

    async def describe_for_bracket_match(self, bm: BracketMatch) -> Optional[str]:
        found = await self.window_for_bracket_match(bm)
        return found.window.describe(await _tenant_zone()) if found else None

    # ------------------------------------------------------------------
    # Enforcement
    # ------------------------------------------------------------------
    async def check_time(
        self,
        found: Optional[StageWindow],
        tournament: Tournament,
        scheduled_at: datetime,
        actor: Optional[User],
        *,
        allow_outside_round: bool = False,
    ) -> None:
        """Refuse a time outside the stage's window.

        Players are always refused. Staff and the tournament's admins are refused
        with ``can_override`` set, and pass when they resubmit with
        ``allow_outside_round``.
        """
        if found is None:
            return
        tz = await _tenant_zone()
        duration = timedelta(minutes=tournament.average_match_duration or _DEFAULT_MATCH_MINUTES)
        if found.window.slot_fits(scheduled_at, duration, tz):
            return
        privileged = await AuthService.can_edit_tournament(actor, tournament)
        if privileged and allow_outside_round:
            return
        rule = found.window.describe(tz)
        rule = rule[:1].lower() + rule[1:]
        message = (
            f"{found.label} has to fit inside its booking window: {rule}. "
            f"That time doesn't, counting {int(duration.total_seconds() // 60)} minutes for the match."
        )
        raise RoundWindowError(message, can_override=privileged)

    async def check_reference(
        self, scheduled_at: datetime, actor: Optional[User], *,
        bracket_match_id: Optional[int] = None,
        challonge_match_id: Optional[int] = None,
        startgg_set_id: Optional[int] = None,
    ) -> None:
        """:meth:`check_time` for whichever matchup reference a booking dialog holds.

        Lets a dialog refuse an out-of-window time before its "Check this time"
        sheet offers a player "Schedule anyway" for a save that would be refused.
        """
        found: Optional[StageWindow] = None
        tournament: Optional[Tournament] = None
        if bracket_match_id is not None:
            bm = await self.bracket_repository.get_match_with_bracket(bracket_match_id)
            if bm is not None:
                found = await self.window_for_bracket_match(bm)
                tournament = await self.tournament_repository.get_by_id(bm.bracket.tournament_id)
        elif challonge_match_id is not None:
            cm = await self.challonge_repository.get_match(challonge_match_id)
            if cm is not None:
                found, tournament = await self.window_for_challonge_match(cm), cm.tournament
        elif startgg_set_id is not None:
            row = await self.startgg_repository.get_set(startgg_set_id)
            if row is not None:
                found, tournament = await self.window_for_startgg_set(row), row.tournament
        if found is not None and tournament is not None:
            await self.check_time(found, tournament, scheduled_at, actor)

    async def check_booking(
        self, found: Optional[StageWindow], tournament: Tournament,
        scheduled_date: str, scheduled_time: str, actor: Optional[User],
        *, allow_outside_round: bool = False,
    ) -> None:
        """:meth:`check_time` for a date and time typed into a booking form."""
        if found is None:
            return
        try:
            scheduled_at = parse_local_datetime(scheduled_date, scheduled_time)
        except ValueError:
            return  # The booking itself reports the bad input.
        await self.check_time(
            found, tournament, scheduled_at, actor, allow_outside_round=allow_outside_round,
        )

    async def check_challonge_booking(
        self, cm: ChallongeMatch, scheduled_date: str, scheduled_time: str,
        actor: Optional[User], *, allow_outside_round: bool = False,
    ) -> None:
        await self.check_booking(
            await self.window_for_challonge_match(cm), cm.tournament,
            scheduled_date, scheduled_time, actor, allow_outside_round=allow_outside_round,
        )

    async def check_match_time(
        self, match: Match, tournament: Tournament, scheduled_at: datetime,
        actor: Optional[User], *, allow_outside_round: bool = False,
    ) -> None:
        await self.check_time(
            await self.window_for_match(match), tournament, scheduled_at, actor,
            allow_outside_round=allow_outside_round,
        )


async def _tenant_zone():
    return get_zone(await TimezoneService.tenant_timezone_name())


def _is_elimination(phase: StartggPhase) -> bool:
    return (phase.bracket_type or '').upper() in _ELIMINATION_TYPES


def _startgg_keys(phase: StartggPhase, sets: List[StartggSet]) -> Dict[int, str]:
    double = (phase.bracket_type or '').upper() == 'DOUBLE_ELIMINATION'
    gf = {s.round for s in sets if (s.round_text or '').lower().startswith('grand final')}
    keys = elimination_keys([s.round for s in sets], double=double, grand_final_rounds=gf)
    return {r: f'{phase.phase_id}:{k}' for r, k in keys.items()}


def _stage_window(saved: Dict[str, dict], key: Optional[str], round_number: Optional[int]) -> Optional[StageWindow]:
    if key is None:
        return None
    window = RoundWindow.from_config(saved.get(key))
    if window is None:
        return None
    bare = key.split(':', 1)[-1]
    return StageWindow(stage_label(bare, round_number=round_number), window)


def _count(row: ScheduleStage, item) -> None:
    row.total += 1
    if getattr(item, 'match_id', None) is not None:
        row.booked += 1


def _detail(row: ScheduleStage) -> str:
    if row.placeholder:
        return 'Not on the bracket yet'
    noun = 'match' if row.total == 1 else 'matches'
    return f'{row.total} {noun}'


def booked_summary(row: ScheduleStage) -> Tuple[str, str]:
    """The Booked chip's text and tone for a row."""
    if row.placeholder or row.total == 0:
        return 'No matches yet', ''
    if row.booked >= row.total:
        return 'All booked', 'ok'
    return f'{row.booked} of {row.total} booked', 'ok' if row.booked else ''
