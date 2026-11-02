"""What the app's admin Match Schedule shows, as one read for the board and one per match.

The NiceGUI board assembles its rows inside ``MatchTableView`` from the display
rows plus per-viewer stamps (comment counts, ADA marks, late notes) and the
run-sheet grouping. The app gets the same thing already arranged: groups in
board order, each row with what this viewer may do to it, so the app never
re-derives a permission or a group from raw match fields.

Times are UTC instants; ``zone`` says which clock to print them on, as for the
player reads (``PlayerHomeService.viewer_zone``).
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from application.errors import NotFoundError
from application.match_board_access import MatchBoardAccess
from application.run_sheet import (
    FOLDED_BY_DEFAULT,
    admin_group,
    arrange_run_sheet,
    readiness,
)
from application.services.accommodation_service import AccommodationService
from application.services.auth_service import AuthService
from application.services.match.match_comment_service import MatchCommentService
from application.services.match.match_display_service import MatchDisplayService
from application.services.match.match_rewind import rewind_targets
from application.services.match.match_service import MatchService
from application.services.player_home_service import PlayerHomeService
from application.services.system_config_service import SystemConfigService
from application.services.timezone_service import TimezoneService
from application.timezone_context import is_valid_timezone, tz_scope
from application.utils.timezone import local_day_bounds
from models import Role, User

CONFIRMED_GROUP = 'Confirmed'
MAX_DAY_TABS = 10

_STATE_SINCE = {
    'Checked In': 'seated_at',
    'Started': 'started_at',
    'Finished': 'finished_at',
    'Confirmed': 'confirmed_at',
}


def _instant(ts: Optional[float]) -> Optional[datetime]:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None


class AdminScheduleService:
    """Screen-shaped reads for the admin Match Schedule. Stateless."""

    def __init__(self) -> None:
        self.display = MatchDisplayService()

    @staticmethod
    async def board_access(actor: User) -> tuple[MatchBoardAccess, Optional[frozenset[int]]]:
        """``(what the viewer may do, the tournaments the board is narrowed to)``.

        Mirrors ``pages.admin._admin_tabs``: staff and Broadcast Tech see every
        match; a tournament admin or crew coordinator sees the tournaments they
        operate. Raises ``PermissionError`` for anyone the page wouldn't offer.
        """
        roles = await AuthService.get_roles(actor)
        is_staff = await AuthService.is_staff(actor)
        admin_ids, cc_ids = await AuthService.tournament_scope(actor)
        is_sm = Role.STREAM_MANAGER in roles
        access = MatchBoardAccess.for_roles(
            is_staff=is_staff,
            is_tournament_admin=bool(admin_ids),
            is_stream_manager=is_sm,
            is_crew_coordinator=bool(cc_ids),
            admin_tournament_ids=frozenset(admin_ids),
            crew_tournament_ids=frozenset(cc_ids),
        )
        if not (is_staff or is_sm or admin_ids or cc_ids):
            raise PermissionError("You don't have access to the Match Schedule.")
        scope = None if (is_staff or is_sm) else frozenset(admin_ids | cc_ids)
        return access, scope

    @staticmethod
    async def zone(actor: User, device_zone: Optional[str]) -> str:
        """The zone the board's days are cut on: the community's pinned clock or
        the viewer's saved one, else the device's, else the community default."""
        chosen = await PlayerHomeService.viewer_zone(actor)
        if chosen:
            return chosen
        if is_valid_timezone(device_zone):
            return device_zone  # type: ignore[return-value]
        return await TimezoneService.tenant_timezone_name()

    @staticmethod
    def row_caps(access: MatchBoardAccess, row: dict) -> Dict[str, bool]:
        return {
            'edit': access.can_edit_row(row),
            'run': access.can_run_row(row),
            'confirm': access.can_confirm_row(row),
            'stage': access.can_assign_stream_row(row),
            'crew': access.can_approve_crew_row(row),
        }

    async def _stamp(self, access: MatchBoardAccess, actor: User, rows: List[dict]) -> None:
        """The per-viewer fields the staff board carries: comment counts and ADA marks."""
        if access.run:
            counts = await MatchCommentService().counts_by_match([r['id'] for r in rows])
            for row in rows:
                row['comment_counts'] = counts.get(row['id'])
        user_ids = {p.get('user_id') for row in rows for p in row.get('players') or []}
        notes = await AccommodationService().arranged_notes_for(actor, user_ids)
        for row in rows:
            for player in row.get('players') or []:
                if player.get('user_id') in notes:
                    player['ada'] = True
                    player['ada_note'] = notes[player['user_id']]

    async def days(self) -> List[date]:
        start, end = await SystemConfigService.get_event_window()
        if (end - start).days >= MAX_DAY_TABS or end < start:
            return []
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]

    async def board(
        self,
        actor: User,
        *,
        day: Optional[date] = None,
        tournament_id: Optional[int] = None,
        stage_id: Optional[int] = None,
        search: str = '',
        device_zone: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Every match on ``day`` (or every day), grouped as the board draws them."""
        access, scope = await self.board_access(actor)
        zone = await self.zone(actor, device_zone)
        tournament_ids: Optional[List[int]] = None
        if scope is not None:
            tournament_ids = sorted(scope if tournament_id is None else scope & {tournament_id})
        elif tournament_id is not None:
            tournament_ids = [tournament_id]
        window = None
        if day is not None:
            with tz_scope(zone):
                window = local_day_bounds(day, day)
        rows = await self.display.get_matches_for_display(
            tournament_ids=tournament_ids,
            stage_ids=[stage_id] if stage_id is not None else None,
            scheduled_window=window,
        ) if tournament_ids != [] else []
        needle = search.strip().lower()
        if needle:
            rows = [r for r in rows if needle in _haystack(r)]
        await self._stamp(access, actor, rows)
        with tz_scope(zone):
            arranged = arrange_run_sheet(rows, admin_group)
        groups: List[Dict[str, Any]] = []
        for row in arranged:
            if row['_group_start']:
                groups.append({
                    'label': row['_group'],
                    'folded_by_default': row['_group'] in FOLDED_BY_DEFAULT,
                    'rows': [],
                })
            groups[-1]['rows'].append(self._row(access, row))
        tournaments = await self.display.get_tournaments_for_filter()
        stages = await self.display.get_stages_for_filter()
        if scope is not None:
            tournaments = {k: v for k, v in tournaments.items() if k in scope}
        return {
            'zone': zone,
            'days': await self.days(),
            'tournaments': [{'id': k, 'name': v} for k, v in tournaments.items()],
            'stages': [{'id': k, 'name': v} for k, v in stages.items()],
            'scoped_tournaments': None if scope is None else len(scope),
            'can_create': access.edit,
            'groups': groups,
        }

    def _row(self, access: MatchBoardAccess, row: dict) -> Dict[str, Any]:
        bracket = row.get('bracket') or {}
        until_ts = None
        if row.get('finished_ts'):
            until_ts = row['finished_ts']
        elif row.get('started_ts') and row.get('match_length'):
            until_ts = row['started_ts'] + row['match_length'] * 60
        site = row.get('played_on_site') or None
        held = row.get('racetime_held') or None
        return {
            'id': row['id'],
            'tournament_id': row.get('tournament_id'),
            'tournament': row.get('tournament_short') or row.get('tournament') or '',
            'tournament_name': row.get('tournament') or '',
            'bracket': bracket.get('name') or None,
            'game': bracket.get('game'),
            'best_of': bracket.get('best_of'),
            'set_label': row.get('set_label') or None,
            'scheduled_at': _instant(row.get('scheduled_ts')),
            'started_at': _instant(row.get('started_ts')),
            'until_at': _instant(until_ts),
            'until_estimated': bool(until_ts) and not row.get('finished_ts'),
            'overrun': bool(row.get('_overrun')),
            'state': row.get('state'),
            'status': row['_status'],
            'tone': row['_tone'],
            'group': row['_group'],
            'players': [
                {
                    'id': p.get('id'), 'user_id': p.get('user_id'), 'name': p.get('name') or '',
                    'station': p.get('station') or None, 'ada': bool(p.get('ada')),
                }
                for p in row.get('players') or []
            ],
            'placeholders': list(row.get('placeholders') or []),
            'stage_id': row.get('stage_id'),
            'stage': row.get('stage') or None,
            'stream_candidate': bool(row.get('is_stream_candidate')),
            'seat_line': row.get('_seat_line') or None,
            'crew': [
                {
                    'key': c['key'], 'label': c['label'], 'count': c['count'],
                    'short': bool(c['short']), 'unapproved': bool(c['unapproved']),
                    'approvable': bool(c['approvable']),
                    'people': [
                        {'id': p.get('id'), 'name': p['name'], 'approved': p['approved'], 'with': p.get('with') or None}
                        for p in c['people']
                    ],
                }
                for c in row['_crew']
            ],
            'late': [{'label': t['label'], 'note': t['note'] or None} for t in row['_late']],
            'comments': {b['key']: b['n'] for b in row['_comments']},
            'winners': row['_winners'],
            'pending_result': row.get('_pending_result') or None,
            'next_step': row['_next'],
            'seed_url': row.get('generated_seed') or None,
            'seed_code': row.get('seed_code') or None,
            'seed_rolling': bool(row.get('seed_rolling_since')),
            'seed_error': row.get('seed_roll_error') or None,
            'is_racetime': bool(row.get('is_racetime')),
            'played_on_site': site,
            'racetime_held': held,
            'needs_review': bool(row.get('needs_review')),
            'can': self.row_caps(access, row),
        }

    async def match(self, actor: User, match_id: int, *, device_zone: Optional[str] = None) -> Dict[str, Any]:
        """One match as the board's detail pane shows it: the line, its checklist,
        where it can be rewound to, and when it reached its current step."""
        access, scope = await self.board_access(actor)
        zone = await self.zone(actor, device_zone)
        row = await self.display.get_match_for_display(match_id)
        if row is None or (scope is not None and row.get('tournament_id') not in scope):
            raise NotFoundError('Match not found')
        await self._stamp(access, actor, [row])
        with tz_scope(zone):
            arrange_run_sheet([row], admin_group)
            items = readiness(row, crew=access.can_approve_crew_row(row))
        match = await MatchService().get_match_by_id(match_id)
        since_field = _STATE_SINCE.get(row.get('state') or '')
        since = getattr(match, since_field, None) if match and since_field else None
        rewind = rewind_targets(match) if match is not None else []
        out = self._row(access, row)
        out.update({
            'zone': zone,
            'description': row.get('description_text') or None,
            'state_since': since,
            'checklist': [
                {'key': i['key'], 'label': i['label'], 'ready': i['ready'], 'detail': i['detail']}
                for i in items
            ],
            'rewind_to': [{'step': s, 'explanation': e} for s, e in rewind],
            'seed_dm_blocked': list(row.get('seed_dm_blocked') or []),
            'stage_url': row.get('stage_url') or None,
            'ada_notes': {
                str(p.get('user_id')): p.get('ada_note')
                for p in row.get('players') or [] if p.get('ada') and p.get('ada_note')
            },
        })
        return out


def _haystack(row: dict) -> str:
    names = [p.get('name') or '' for p in row.get('players') or []] + list(row.get('placeholders') or [])
    return ' '.join([
        row.get('tournament') or '', row.get('tournament_short') or '', row.get('stage') or '',
        row.get('title') or '', *names,
    ]).lower()

