"""The "Needs you" inbox above a run-sheet match board.

Four kinds of work wait on staff rather than on the clock: a player's request to
move or cancel a match, a booked match someone already reported on its bracket
site (Challonge, start.gg), a finished match nobody has confirmed, and a match
past its start that nobody has checked in. They used to be three differently built
strips of counts above the board, none of which did anything but filter. Here
each is a short list of items with the action on the item.

Requests come from ``MatchRescheduleService``; results and late matches are read
off the board's own rows, so they follow the board's filters the way the old
count strip did.
"""

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from nicegui import context, ui

from application.event_context import get_current_event_id
from application.services import MatchRescheduleService, MatchService, bracket_sources
from application.tenant_context import require_tenant_id
from application.utils.timezone import format_local_time, to_local, today_local
from models import Match, RescheduleRequestKind
from theme.dialog.clash_warning import confirm_time
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.help import ResolvedHelp, help_button, resolve_help
from theme.matchup import matchup_label
from theme.notify import notify_error
from theme.tables.admin_crud import capture_render_context, current_actor, scoped_background
from theme.tables.match_access import MatchBoardAccess

#: Items a group shows before "Show N more".
_GROUP_LIMIT = 3


def _when(dt) -> str:
    """``14:58`` today, ``Sat 1 Aug 14:58`` on any other day."""
    local = to_local(dt)
    if local is None:
        return 'no time set'
    if local.date() == today_local():
        return format_local_time(dt)
    return f'{local:%a} {local.day} {local:%b} {format_local_time(dt)}'


def _clock(ts: float) -> str:
    return format_local_time(datetime.fromtimestamp(ts, tz=timezone.utc))


def _names(match) -> str:
    names = [p.user.preferred_name for p in match.players]
    return ' vs '.join(names) if len(names) == 2 else ', '.join(names) or (match.title or 'a match')


def _winner_line(row: dict) -> str:
    players = row.get('players') or []
    winner = next((p for p in players if p.get('finish_rank') == 1), None)
    if winner is None:
        return ' vs '.join(p.get('name') or '' for p in players)
    others = [p for p in players if p is not winner]
    if len(others) == 1:
        return f"{winner.get('name')} beat {others[0].get('name')}"
    return f"{winner.get('name')} won"


class MatchInbox:
    """Build with ``render`` inside the page; refresh with ``refresh``."""

    def __init__(
        self,
        *,
        access: MatchBoardAccess,
        handlers,
        open_match: Callable[[int], Awaitable[None]],
        open_request: Callable[[int], Awaitable[None]],
        tournament_ids: Optional[list[int]] = None,
    ) -> None:
        self.access = access
        self.handlers = handlers
        self.open_match = open_match
        self.open_request = open_request
        self.tournament_ids = tournament_ids
        self.view: Any = None
        self._expanded: set[str] = set()
        self._generation = 0
        self._help: list[ResolvedHelp] = []
        self._render_context = capture_render_context()
        self._client: Any = None

    def refresh(self) -> None:
        scoped_background(self._render_context, self._reload())

    async def _reload(self) -> None:
        # Query first, then clear and rebuild in one step. A refreshable that
        # awaits after clearing leaves the inbox empty for the length of the
        # query, and the CSS hides an empty inbox, so the board beside it
        # jumped to full width and back on every refresh (several per confirm).
        self._generation += 1
        generation = self._generation
        requests = await self._requests()
        if generation != self._generation:
            return  # a newer refresh started meanwhile and will draw instead
        self._build.refresh(requests)

    async def _requests(self) -> list:
        if not self.access.edit:
            return []
        actor = await current_actor()
        if actor is None:
            return []
        return await MatchRescheduleService().list_pending(
            actor, tournament_ids=self.tournament_ids, event_id=get_current_event_id(),
        )

    async def render(self) -> None:
        # Item handlers run in their button's slot, and a refresh (theirs or a
        # live update) can delete it mid-await; their UI calls go through the
        # page's client instead (SGLMAN-2B).
        self._client = context.client
        self._help = await resolve_help(('staff-queues',))
        self._build(await self._requests())

    @ui.refreshable_method
    def _build(self, requests: list) -> None:
        rows: list = self.view._visible_rows() if self.view else []
        finished = [r for r in rows if r.get('state') == 'Finished' and self.access.can_confirm_row(r)]
        played = [r for r in rows if r.get('played_on_site') and self.access.can_confirm_row(r)]
        late = [
            r for r in rows
            if self.access.can_run_row(r) and r.get('state') == 'Scheduled'
            and (r.get('is_overdue') or r.get('_late'))
            and r.get('players') and not r.get('is_racetime') and not r.get('played_on_site')
        ]
        total = len(requests) + len(played) + len(finished) + len(late)
        if not (self.access.edit or self.access.confirm or self.access.run):
            return
        with ui.column().classes('match-inbox full-width'):
            with ui.row().classes('items-center gap-2 full-width wiz-help-row'):
                heading = ui.label('Needs you').classes('section-title')
                heading.tag = 'h2'
                if total:
                    with ui.element('span').classes('wiz-chip wiz-chip--pending'):
                        ui.label(str(total))
                help_button(self._help)
            if not total:
                ui.label('Nothing is waiting on you.').classes('text-muted')
                return
            self._group('requests', 'Reschedule requests', requests, self._request_item)
            self._group('site', 'Played on the bracket site', played, self._site_item)
            # Disputed first: it needs a decision, an unflagged result only a click.
            finished.sort(key=lambda r: (not r.get('needs_review'), r.get('has_result'), r.get('scheduled_ts') or 0))
            self._group('results', 'Results', finished, self._result_item)
            self._group('late', 'Running late', late, self._late_item)

    def _group(self, key: str, title: str, items: list, build) -> None:
        if not items:
            return
        label = ui.label(f'{title} · {len(items)}').classes('match-inbox__group')
        label.tag = 'h3'
        shown = items if key in self._expanded else items[:_GROUP_LIMIT]
        for item in shown:
            with ui.element('div').classes('match-inbox__item'):
                build(item)
        hidden = len(items) - len(shown)
        if hidden > 0:
            def expand(k=key):
                self._expanded.add(k)
                self.refresh()
            ui.button(f'Show {hidden} more', on_click=expand) \
                .props('flat dense no-caps color=primary').classes('self-start')

    def _request_item(self, request) -> None:
        match = request.match
        who = request.requested_by.preferred_name
        cancel = request.kind is RescheduleRequestKind.CANCEL
        what = 'cancel' if cancel else 'move'
        ui.label(f'{who} wants to {what} {_names(match)}').classes('match-inbox__title')
        if cancel:
            detail = f'{match.tournament.name} · {_when(match.scheduled_at)}'
        elif request.proposed_at:
            detail = (f'{match.tournament.name} · {_when(match.scheduled_at)}'
                      f' to {_when(request.proposed_at)}')
        else:
            detail = f'{match.tournament.name} · no time suggested'
        ui.label(detail).classes('match-inbox__meta')
        if request.opponent_agreed_at:
            ui.label('The other player agreed').classes('match-inbox__meta st-ok')
        with ui.row().classes('gap-2 items-center'):
            if not cancel and request.proposed_at:
                async def approve(r=request):
                    with self._client:
                        actor = await current_actor()
                        if actor is None:
                            ui.notify('Log in again to decide this.', color='warning')
                            return
                        if not await confirm_time(
                            tournament_id=r.match.tournament_id,
                            player_ids=[p.user_id for p in r.match.players],
                            scheduled_at=r.proposed_at, actor=actor,
                            exclude_match_id=r.match_id, confirm_label='Move it anyway',
                        ):
                            return
                        try:
                            await MatchRescheduleService().approve(r.id, actor)
                        except (ValueError, PermissionError) as e:
                            notify_error(e)
                            return
                        ui.notify('Match moved and everyone told.', color='positive')
                        self.refresh()
                        if self.view is not None:
                            await self.view.refresh()
                ui.button('Decline', on_click=lambda r=request: self.open_request(r.id)) \
                    .props('outline color=primary')
                ui.button('Approve', on_click=approve).props('color=primary')
            else:
                # Cancelling deletes the match, and a request with no time needs
                # one picked: both go through the full decision dialog.
                ui.button('Review', on_click=lambda r=request: self.open_request(r.id)) \
                    .props('color=primary')

    def _result_item(self, row: dict) -> None:
        match_id = row['id']
        with ui.row().classes('items-center gap-2 no-wrap full-width'):
            ui.label(_winner_line(row)).classes('match-inbox__title col')
            if row.get('needs_review'):
                with ui.element('span').classes('wiz-chip wiz-chip--cancelled'):
                    ui.label('Flagged')
            elif not row.get('has_result'):
                with ui.element('span').classes('wiz-chip wiz-chip--cancelled'):
                    ui.label('No winner')
        meta = row.get('tournament') or ''
        if row.get('finished_ts'):
            meta += f" · finished {_clock(row['finished_ts'])}"
        ui.label(meta).classes('match-inbox__meta')
        h = self.handlers
        with ui.row().classes('gap-2 items-center'):
            ui.button('Open', on_click=lambda: self.open_match(match_id)).props('flat no-caps color=primary')
            if not row.get('has_result'):
                ui.button('Record winner', on_click=lambda: h.on_edit_result(match_id)).props('color=primary')
            elif row.get('needs_review'):
                ui.button('Review', on_click=lambda: h.on_confirm(match_id)).props('color=primary')
            else:
                async def confirm():
                    match = await Match.get(id=match_id, tenant_id=require_tenant_id()) \
                        .prefetch_related('tournament', 'players', 'players__user')
                    await h.confirm_confirming(match)
                ui.button('Change', on_click=lambda: h.on_edit_result(match_id)).props('outline color=primary')
                ui.button('Confirm', on_click=confirm).props('color=primary')

    def _site_item(self, row: dict) -> None:
        match_id = row['id']
        site = row['played_on_site']
        players = row.get('players') or []
        winner = next((p for p in players if site.get('winner_user_id') and p.get('user_id') == site['winner_user_id']), None)
        winner_name = winner.get('name') if winner else (site.get('winner') or 'Someone')
        others = [p for p in players if p is not winner]
        if site.get('no_winner'):
            title = f"Closed on {site['site']} with no winner"
        elif winner is not None and len(others) == 1:
            title = f"{winner_name} beat {others[0].get('name')} on {site['site']}"
        else:
            title = f"{winner_name} won on {site['site']}"
        ui.label(title).classes('match-inbox__title')
        meta = row.get('tournament') or ''
        if row.get('scheduled_ts'):
            meta += f" · booked {_clock(row['scheduled_ts'])}"
        if row.get('stage'):
            meta += f" on {row['stage']}"
        ui.label(meta).classes('match-inbox__meta')

        async def record():
            with self._client:
                actor = await current_actor()
                if actor is None:
                    ui.notify('Log in again to record this.', color='warning')
                    return
                try:
                    await bracket_sources.record_site_result(match_id, actor)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify(f"Recorded the {site['site']} result.", color='positive')
                self.refresh()
                if self.view is not None:
                    await self.view.refresh()

        async def call_off():
            with self._client:
                actor = await current_actor()
                if actor is None:
                    ui.notify('Log in again to call this off.', color='warning')
                    return
                try:
                    await MatchService().cancel_match(match_id, actor=actor)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Match called off. Players and crew were told.', color='positive')
                self.refresh()
                if self.view is not None:
                    await self.view.refresh()

        def confirm_call_off():
            ConfirmationDialog(
                message=(
                    f"Call off this match? {site['site']} already "
                    f"{'closed it with no winner' if site.get('no_winner') else 'has its result'}, so "
                    'Wizzrobe keeps no result for it. Its players and crew will be DMed.'
                ),
                on_confirm=call_off, confirm_text='Call off match', cancel_text='Back',
                title='Call off match?',
            ).open()

        with ui.row().classes('gap-2 items-center'):
            if site.get('no_winner'):
                ui.button('Call off', on_click=confirm_call_off).props('color=primary')
            else:
                ui.button('Call off', on_click=confirm_call_off).props('outline color=primary')
                ui.button('Record result', on_click=record).props('color=primary')

    def _late_item(self, row: dict) -> None:
        match_id = row['id']
        if row.get('_who_parts'):
            matchup_label(row['_who_parts'], 'match-inbox__title', tag='div')
        else:
            ui.label(row.get('_who') or row.get('title') or 'Match').classes('match-inbox__title')
        when = _clock(row['scheduled_ts']) if row.get('scheduled_ts') else ''
        ui.label(f"{row.get('tournament') or ''} · {'was' if row.get('is_overdue') else 'at'} {when}") \
            .classes('match-inbox__meta')
        for late in row.get('_late') or []:
            with ui.column().classes('match-inbox__late gap-0'):
                with ui.row().classes('items-center no-wrap gap-1 st-pending'):
                    ui.icon('schedule', size='16px').props('aria-hidden=true')
                    ui.label(late['label'])
                if late.get('note'):
                    ui.label(f"“{late['note']}”").classes('match-inbox__meta')
        with ui.row().classes('gap-2 items-center'):
            ui.button('Open', on_click=lambda: self.open_match(match_id)).props('flat no-caps color=primary')
            ui.button('Check in', on_click=lambda: self.handlers.on_seat(match_id)).props('color=primary')
