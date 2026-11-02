"""The app's Admin → Tournaments screen: the board, one tournament's players and
bracket, and the bracket-site tools behind each row's ⋮.

Screen-shaped reads for the new app, built on the services the NiceGUI board
calls, so both front ends make the same decisions about who may do what.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from application.errors import NotFoundError, require_found
from application.repositories import TournamentRepository
from application.services import bracket_sources
from application.services.auth_service import AuthService
from application.services.challonge_service import ChallongeService
from application.services.player_home_service import PlayerHomeService
from application.services.round_schedule_service import RoundScheduleService
from application.services.startgg_service import StartggService
from application.services.tournament_service import TournamentService
from application.services.user_service import UserService
from application.utils.tournament_signup import SignupWindow, signup_window_state
from models import ForfeitReason, Match, Tournament, User

_NOT_LINKED = "{name} isn't linked to a bracket site."


@dataclass
class StageActionView:
    key: str
    label: str
    confirm: str


@dataclass
class BracketView:
    site: str
    label: str
    stage: Optional[str]
    synced_at: Optional[datetime]
    unmatched: int
    url: Optional[str]
    stage_action: Optional[StageActionView]


@dataclass
class BoardRow:
    id: int
    name: str
    tournament_format: Optional[str]
    seed_generator: Optional[str]
    is_active: bool
    is_test: bool
    players_per_match: int
    player_count: int
    average_match_duration: Optional[int]
    max_match_duration: Optional[int]
    signups_open_at: Optional[datetime]
    signups_close_at: Optional[datetime]
    signup_state: SignupWindow
    bracket: Optional[BracketView]
    bracket_url: Optional[str]
    rules_url: Optional[str]
    has_round_schedule: bool
    can_manage_bracket: bool
    can_link: bool
    can_delete: bool


@dataclass
class BracketSite:
    key: str
    label: str


@dataclass
class Board:
    rows: List[BoardRow]
    can_create: bool
    linkable_sites: List[BracketSite]
    zone: Optional[str] = None


@dataclass
class PersonView:
    id: int
    name: str
    username: Optional[str] = None


@dataclass
class PlayerView:
    user: PersonView
    enrolled_at: Optional[datetime]


@dataclass
class DropMatchView:
    id: int
    title: str
    scheduled_at: Optional[datetime]


@dataclass
class DropPlanView:
    head_to_head: List[DropMatchView]
    group: List[DropMatchView]
    on_bracket_site: bool


@dataclass
class EntrantView:
    """A bracket-site entrant and the member it maps to.

    ``status`` is ``manual`` (matched by hand), ``linked`` (from the entrant's
    own linked account), ``suggested`` (a likely member waiting to be
    confirmed) or ``unmatched``.
    """

    id: int
    name: str
    status: str
    user: Optional[PersonView]
    suggested_user: Optional[PersonView] = None
    conflicting_user: Optional[PersonView] = None


@dataclass
class EntrantList:
    site: str
    label: str
    entrants: List[EntrantView] = field(default_factory=list)


@dataclass
class PhaseView:
    phase_id: str
    name: str
    state: Optional[str]


@dataclass
class StartggSettings:
    event_slug: Optional[str]
    event_name: Optional[str]
    bookable_phase_id: Optional[str]
    report_results: bool
    phases: List[PhaseView]


def _person(user: Optional[User]) -> Optional[PersonView]:
    if user is None:
        return None
    return PersonView(id=user.id, name=user.preferred_name, username=user.username)


def _match_title(match: Match) -> str:
    return getattr(match, 'title', None) or f'Match {match.id}'


class AdminTournamentsService:
    """Stateless; each method checks the caller's rights on the tournament it touches."""

    def __init__(self) -> None:
        self.repository = TournamentRepository()
        self.tournaments = TournamentService()

    async def _editable(self, tournament_id: int, actor: User) -> Tournament:
        tournament = require_found(await self.repository.get_by_id(tournament_id), 'Tournament')
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            "You can't manage this tournament.",
        )
        return tournament

    async def _ensure_staff(self, actor: User) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), 'Staff only.')

    async def board(self, actor: User) -> Board:
        """Every tournament the caller runs in this event, as the board lists them."""
        is_staff = await AuthService.is_staff(actor)
        own_ids = None if is_staff else sorted((await AuthService.tournament_scope(actor))[0])
        await AuthService.ensure(is_staff or bool(own_ids), "You don't run any tournaments here.")
        tournaments = await self.repository.list_with_player_counts(own_ids)
        live = await bracket_sources.live_sources()
        unmatched = await bracket_sources.unmatched_entrant_counts(tournaments)
        return Board(
            rows=[self._row(t, live, unmatched, is_staff) for t in tournaments],
            can_create=is_staff,
            linkable_sites=[BracketSite(s.key, s.label) for s in live] if is_staff else [],
            zone=await PlayerHomeService.viewer_zone(actor),
        )

    async def row(self, tournament_id: int, actor: User) -> BoardRow:
        await self._editable(tournament_id, actor)
        rows = await self.repository.list_with_player_counts([tournament_id])
        tournament = require_found(rows[0] if rows else None, 'Tournament')
        live = await bracket_sources.live_sources()
        unmatched = await bracket_sources.unmatched_entrant_counts([tournament])
        return self._row(tournament, live, unmatched, await AuthService.is_staff(actor))

    @staticmethod
    def _row(t: Tournament, live, unmatched: Dict[int, int], is_staff: bool) -> BoardRow:
        source = next((s for s in live if s.linked(t)), None)
        bracket = None
        if source is not None:
            action = None
            stage = None
            synced_at = t.startgg_last_synced_at
            url = StartggService.event_url(t) or t.bracket_url
            if source.key == 'challonge':
                stage = ChallongeService.stage_label_for(t)
                synced_at = t.challonge_last_synced_at
                url = t.challonge_tournament_url or t.bracket_url
                first = next(iter(ChallongeService.stage_actions_for(t)), None)
                if first is not None:
                    action = StageActionView(first.key, first.label, first.confirm)
            bracket = BracketView(
                site=source.key, label=source.label, stage=stage, synced_at=synced_at,
                unmatched=unmatched.get(t.id, 0), url=url, stage_action=action,
            )
        return BoardRow(
            id=t.id,
            name=t.name,
            tournament_format=t.tournament_format,
            seed_generator=t.seed_generator,
            is_active=t.is_active,
            is_test=t.is_test,
            players_per_match=t.players_per_match,
            player_count=getattr(t, 'player_count', 0) or 0,
            average_match_duration=t.average_match_duration,
            max_match_duration=t.max_match_duration,
            signups_open_at=t.signups_open_at,
            signups_close_at=t.signups_close_at,
            signup_state=signup_window_state(t.signups_open_at, t.signups_close_at),
            bracket=bracket,
            bracket_url=t.bracket_url,
            rules_url=t.rules_url,
            has_round_schedule=bool(t.challonge_tournament_id or t.startgg_event_slug),
            can_manage_bracket=source is not None,
            can_link=is_staff and (source is not None or bool(live)),
            can_delete=is_staff and not t.is_active,
        )

    async def deletion_preview(self, tournament_id: int, actor: User) -> Dict[str, int]:
        await self._ensure_staff(actor)
        tournament = require_found(await self.repository.get_by_id(tournament_id), 'Tournament')
        return await self.tournaments.deletion_preview(tournament)

    async def players(self, tournament_id: int, actor: User) -> List[PlayerView]:
        tournament = await self._editable(tournament_id, actor)
        rows = await self.tournaments.get_enrolled_players(tournament)
        players = [PlayerView(user=_person(r.user), enrolled_at=r.created_at) for r in rows]  # type: ignore[arg-type]
        return sorted(players, key=lambda p: p.user.name.lower())

    async def people(self, tournament_id: int, actor: User) -> List[PersonView]:
        """The community's members, for the enrol and match-entrant pickers."""
        await self._editable(tournament_id, actor)
        summaries = await UserService().get_community_people_summaries()
        return [PersonView(id=p.id, name=p.preferred_name, username=p.username) for p in summaries]

    async def _target(self, user_id: int) -> User:
        return require_found(await UserService().get_user_by_id(user_id), 'User')

    async def drop_plan(self, tournament_id: int, user_id: int, actor: User) -> DropPlanView:
        tournament = await self._editable(tournament_id, actor)
        plan = await self.tournaments.plan_drop(tournament, await self._target(user_id), actor)
        view = lambda m: DropMatchView(m.id, _match_title(m), m.scheduled_at)
        return DropPlanView(
            head_to_head=[view(m) for m in plan.head_to_head],
            group=[view(m) for m in plan.group],
            on_bracket_site=plan.on_bracket_site,
        )

    async def drop(
        self, tournament_id: int, user_id: int, actor: User, *, reason: ForfeitReason, forfeit_matches: bool,
    ):
        tournament = await self._editable(tournament_id, actor)
        return await self.tournaments.drop_player(
            tournament, await self._target(user_id), actor, reason=reason, forfeit_matches=forfeit_matches,
        )

    async def _source(self, tournament: Tournament):
        source = await bracket_sources.source_for(tournament)
        if source is None:
            raise ValueError(_NOT_LINKED.format(name=tournament.name))
        return source

    async def sync(self, tournament_id: int, actor: User) -> bracket_sources.SyncResult:
        tournament = await self._editable(tournament_id, actor)
        return await bracket_sources.sync_tournament(tournament, actor)

    async def sync_all(self, actor: User) -> List[bracket_sources.SyncResult]:
        is_staff = await AuthService.is_staff(actor)
        own_ids = None if is_staff else sorted((await AuthService.tournament_scope(actor))[0])
        await AuthService.ensure(is_staff or bool(own_ids), "You don't run any tournaments here.")
        tournaments = await self.repository.list_with_player_counts(own_ids)
        return await bracket_sources.sync_all(list(tournaments), actor)

    async def link(self, tournament_id: int, site: str, id_or_url: str, actor: User) -> str:
        await self._ensure_staff(actor)
        tournament = await self._editable(tournament_id, actor)
        if site not in {s.key for s in await bracket_sources.live_sources()}:
            raise ValueError("That bracket site isn't switched on for this community.")
        detected = bracket_sources.site_for_link(id_or_url)
        if detected is not None and detected != site:
            raise ValueError(f'That link is for {bracket_sources.site_labels()[detected]}, not '
                             f'{bracket_sources.site_labels()[site]}.')
        return await bracket_sources.link(site, tournament, id_or_url, actor)

    async def unlink(self, tournament_id: int, actor: User) -> str:
        await self._ensure_staff(actor)
        tournament = await self._editable(tournament_id, actor)
        return await bracket_sources.unlink(tournament, actor)

    async def advance_stage(self, tournament_id: int, action_key: str, actor: User) -> BoardRow:
        await self._editable(tournament_id, actor)
        await ChallongeService().advance_stage(tournament_id, action_key, actor)
        return await self.row(tournament_id, actor)

    async def entrants(self, tournament_id: int, actor: User) -> EntrantList:
        tournament = await self._editable(tournament_id, actor)
        source = await self._source(tournament)
        out = EntrantList(site=source.key, label=source.label)
        if source.key == 'challonge':
            for row in await ChallongeService().list_bracket_participants(tournament.id, actor):
                status = {'verified': 'linked', 'unlinked': 'unmatched'}.get(row['status'], row['status'])
                out.entrants.append(EntrantView(
                    id=row['id'], name=row['name'] or '', status=status, user=_person(row['user']),
                    conflicting_user=_person(row['verified_user']),
                ))
            return out
        for e in await StartggService().list_entrants(tournament.id, actor):
            if e.user is not None:
                status = 'manual' if e.user_assigned_manually else 'linked'
            elif e.suggested_user is not None and not e.suggestion_dismissed:
                status = 'suggested'
            else:
                status = 'unmatched'
            out.entrants.append(EntrantView(
                id=e.id, name=e.name, status=status, user=_person(e.user),
                suggested_user=_person(e.suggested_user) if status == 'suggested' else None,
            ))
        out.entrants.sort(key=lambda e: e.name.lower())
        return out

    async def assign_entrant(self, tournament_id: int, entrant_id: int, user_id: int, actor: User) -> None:
        source = await self._source(await self._editable(tournament_id, actor))
        if source.key == 'challonge':
            await ChallongeService().assign_participant_user(entrant_id, user_id, actor)
        else:
            await StartggService().assign_entrant(entrant_id, user_id, actor)

    async def clear_entrant(self, tournament_id: int, entrant_id: int, actor: User) -> None:
        source = await self._source(await self._editable(tournament_id, actor))
        if source.key == 'challonge':
            await ChallongeService().clear_participant_user(entrant_id, actor)
        else:
            await StartggService().clear_entrant(entrant_id, actor)

    async def _startgg(self, tournament_id: int, actor: User) -> Tournament:
        tournament = await self._editable(tournament_id, actor)
        if (await self._source(tournament)).key != 'startgg':
            raise NotFoundError('start.gg bracket')
        return tournament

    async def dismiss_suggestion(self, tournament_id: int, entrant_id: int, actor: User) -> None:
        await self._startgg(tournament_id, actor)
        await StartggService().dismiss_suggestion(entrant_id, actor)

    async def confirm_all_suggestions(self, tournament_id: int, actor: User) -> int:
        await self._startgg(tournament_id, actor)
        return await StartggService().confirm_all_suggestions(tournament_id, actor)

    async def startgg_settings(self, tournament_id: int, actor: User) -> StartggSettings:
        tournament = await self._startgg(tournament_id, actor)
        phases = await StartggService().list_phases(tournament_id, actor)
        return StartggSettings(
            event_slug=tournament.startgg_event_slug,
            event_name=tournament.startgg_event_name,
            bookable_phase_id=tournament.startgg_bookable_phase_id,
            report_results=tournament.startgg_report_results,
            phases=[PhaseView(p.phase_id, p.name, p.state) for p in phases],
        )

    async def update_startgg_settings(
        self, tournament_id: int, actor: User, *,
        bookable_phase_id: Optional[str] = None, set_phase: bool = False, report_results: Optional[bool] = None,
    ) -> StartggSettings:
        await self._startgg(tournament_id, actor)
        service = StartggService()
        if set_phase:
            await service.set_bookable_phase(tournament_id, bookable_phase_id, actor)
        if report_results is not None:
            await service.set_report_results(tournament_id, report_results, actor)
        return await self.startgg_settings(tournament_id, actor)

    async def save_round_schedule(self, tournament_id: int, entries: Dict[str, dict], actor: User) -> None:
        await self._editable(tournament_id, actor)
        await RoundScheduleService().save_schedule(tournament_id, entries, actor)
