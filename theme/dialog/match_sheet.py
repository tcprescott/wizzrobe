"""Everything staff can do to one match, in one sheet.

The run-sheet board (``theme.tables.match_run_sheet``) shows a line per match and
opens this when a line is tapped: a side sheet on a desktop, a full-screen sheet
on a phone. It reads the board's own row for the match, so a change made here or
by someone else elsewhere repaints it through the same live update that repaints
the line (:meth:`MatchSheet.sync`).

Every control calls the board's existing callbacks — ``MatchLifecycleHandlers``
for the lifecycle, stage, seed and stations, the view's handlers for crew — so
the sheet adds no second path to a service, and each control shows only when the
viewer's ``MatchBoardAccess`` covers it.
"""

from types import SimpleNamespace
from typing import Any, Optional

from nicegui import app, ui

from application.content import parse_user_blocks
from application.errors import NotFoundError
from application.services import ItemTrackerService, MatchCommentService, get_user_from_session
from theme.busy import busy
from theme.clipboard import copy_to_clipboard
from theme.dialog._helpers import dialog_actions, dialog_header
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.help.render import render_user_blocks
from theme.markdown_editor import MarkdownEditor
from theme.matchup import matchup_label
from theme.menu import menu_action, more_menu
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_run_sheet import decorate_run_sheet_row
from theme.tables.match_slots import CANDIDATE_STAGE
from theme.url_state import url_param, url_state

_NO_STAGE = ''

_STATE_SINCE = {
    'Checked In': 'Checked in at',
    'Started': 'Started at',
    'Finished': 'Finished at',
    'Confirmed': 'Confirmed at',
}

_ROLES = (
    # (row key, singular used by the view's handlers, heading)
    ('commentators', 'commentator', 'Commentary'),
    ('trackers', 'tracker', 'Tracking'),
    ('broadcasters', 'broadcaster', 'Broadcast'),
)


def seed_rollable(row: dict) -> bool:
    """Python twin of ``match_slots.SEED_ROLLABLE``: may a seed be rolled now."""
    return bool(
        row.get('tournament_seed_generator') and not row.get('is_racetime')
        and row.get('players')
        and row.get('state') not in ('Finished', 'Confirmed')
    )


def _names(names: list) -> str:
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


def readiness(row: dict, *, crew: bool = True) -> list[dict]:
    """The match sheet's checklist, one item per thing a match needs.

    ``ready`` is True or False for what a match can't start well without, and
    None for what's only information (no stage just means off stream). Seed and
    crew appear only where the tournament uses them, and crew only when the
    viewer looks after crew (``crew``).
    """
    players = row.get('players') or []
    acks = row.get('acknowledgments') or []
    items = []

    if not players:
        items.append({'key': 'players', 'label': 'Players', 'ready': False, 'detail': 'Not decided yet'})
    else:
        unconfirmed = [a.get('name') or '' for a in acks if not a.get('acknowledged')]
        if not unconfirmed:
            detail = 'Everyone has confirmed'
        elif len(unconfirmed) == len(players) and len(players) == 2:
            detail = f'Neither {unconfirmed[0]} nor {unconfirmed[1]} has confirmed'
        else:
            detail = f"{_names(unconfirmed)} {'hasn' if len(unconfirmed) == 1 else 'haven'}'t confirmed"
        items.append({'key': 'players', 'label': 'Players confirmed', 'ready': not unconfirmed, 'detail': detail})

        stations = [(p.get('name') or '', p.get('station')) for p in players]
        missing = [name for name, station in stations if not station]
        # A stage match is played on the stage: stations are only information
        # there, listed when someone recorded them.
        if row.get('stage'):
            if len(missing) < len(stations):
                detail = ' · '.join(f'{name} {station}' for name, station in stations if station)
                items.append({'key': 'stations', 'label': 'Stations', 'ready': None, 'detail': detail})
        else:
            if len(missing) == len(stations):
                detail = 'Not assigned'
            else:
                detail = ' · '.join(f'{name} {station}' for name, station in stations if station)
                if missing:
                    detail += f" · {_names(missing)} {'has' if len(missing) == 1 else 'have'} none"
            items.append({'key': 'stations', 'label': 'Stations', 'ready': not missing, 'detail': detail})

    stage = row.get('_stage') or ''
    items.append({
        'key': 'stage', 'label': 'Stage', 'ready': True if row.get('stage') else None,
        'detail': stage or 'No stage',
    })

    seed = row.get('generated_seed')
    if seed or row.get('tournament_seed_generator'):
        if seed:
            ready, detail = True, ''
        elif row.get('seed_rolling_since'):
            ready, detail = False, row.get('seed_rolling_label') or 'Rolling…'
        elif row.get('seed_roll_error'):
            ready, detail = False, row['seed_roll_error']
        elif row.get('is_racetime'):
            ready, detail = None, 'The race room rolls it'
        else:
            ready, detail = False, 'Not rolled'
        items.append({'key': 'seed', 'label': 'Seed', 'ready': ready, 'detail': detail})

    wanted = row.get('crew_wanted') or {}
    required = row.get('crew_required') or {}
    need = approved = waiting = 0
    for key, _role, _label in _ROLES:
        if not wanted.get(key):
            continue
        people = row.get(key) or []
        got = sum(1 for p in people if p.get('approved'))
        need += max((required.get(key) or 0) - got, 0)
        approved += got
        waiting += sum(1 for p in people if not p.get('approved'))
    if crew and any(required.get(k) for k, _r, _l in _ROLES if wanted.get(k)):
        if need:
            detail = f'{need} more needed'
            if waiting:
                detail += f', {waiting} waiting for approval below'
        else:
            detail = 'Covered'
        items.append({'key': 'crew', 'label': 'Crew', 'ready': not need, 'detail': detail})
    return items


class MatchSheet:
    """The match sheet. Two-phase like the lifecycle handlers: set ``view`` after
    the board exists, since the board needs ``open`` as its callback."""

    def __init__(self, handlers, *, access: MatchBoardAccess, show_crew: bool = True,
                 decorate=decorate_run_sheet_row, url_key: str = 'sheet'):
        self.handlers = handlers
        self.access = access
        # Off on the proctor's board: crew is not theirs to fix, so a crew gap
        # must not hold their checklist below "ready".
        self.show_crew = show_crew
        # The board's own row decorator, so the sheet's status chip says what
        # the line it opened from says.
        self.decorate = decorate
        self.view: Any = None
        self.row: Optional[dict] = None
        # Comments are read per viewer, so they are fetched when the sheet
        # opens rather than carried on the board's rows, which every viewer of
        # a shared board would otherwise receive.
        # A copy, because comments can load where the session is out of reach.
        self._session = dict(app.storage.user)
        self._viewer: Any = None
        self._comments: list = []
        # Which comment box is open: 'private' or 'players' for staff, while
        # a player's box is always open. The draft survives the repaints a
        # live update causes while someone is typing.
        self._compose: Optional[str] = None
        self._draft = ''
        # A deep link asks for a match before the board has loaded it.
        self._pending_open: Optional[int] = None
        # The ALTTP item tracker for the open match, when this viewer may track it.
        self._tracker_link: Optional[str] = None
        # mobile_sheet's own breakpoint, but a side sheet rather than a centred
        # card above it: the board stays readable beside the open match.
        with ui.dialog().props(''':position="Quasar.Screen.lt.sm ? 'bottom' : 'right'"''') as self.dialog, \
                ui.card().classes('dialog-card match-sheet'):
            dialog_header('Match details', self.dialog)
            self._content = ui.refreshable(self._render)
            self._content()
        self.dialog.on('hide', self._closed)
        # The open sheet is in the address (``?sheet=<match id>``), so Back
        # closes it, Forward reopens it, and a copied link opens the same match.
        # ``_pushed`` remembers the sheet was opened here, where closing it is
        # Back; one opened by a link has no entry of its own to go back over.
        self._pushed = False
        asked = url_state().requested(url_key)
        asked = asked if asked and asked.isdecimal() else ''
        self._param = url_param(
            url_key, self.dialog, default='', value=asked,
            label=lambda v: 'Match details' if v else None, on_restore=self._restore,
        )
        if asked:
            self.open_when_loaded(int(asked))

    def _closed(self) -> None:
        self.row = None
        self._compose = None
        self._draft = ''
        if self._param.value:
            if self._pushed:
                self._param.value = ''
                with self.dialog:
                    ui.run_javascript('history.back()')
            else:
                self._param.set('', history='replace')
        self._pushed = False

    async def _restore(self, value: str) -> None:
        if value.isdecimal():
            if self._find(int(value)) is None:
                self.open_when_loaded(int(value))
            else:
                await self.open(int(value), record=False)
        elif self.dialog.value:
            self.dialog.close()

    async def open(self, match_id, *, record: bool = True) -> None:
        if isinstance(match_id, str) and match_id.startswith('future:'):
            # A bracket matchup nobody has signed up for yet: not a match, no sheet.
            return
        row = self._find(match_id)
        if row is None:
            return
        self.row = row
        await self._load_comments()
        self._tracker_link = await ItemTrackerService().sheet_link(
            row['id'], self._viewer,
        )
        with self.dialog:
            self._content.refresh()
            self.dialog.open()
        if record and str(row.get('id')) != self._param.value:
            self._pushed = not self._param.state.restoring
            self._param.set(row.get('id'))

    def open_when_loaded(self, match_id: int) -> None:
        """Open ``match_id`` once the board's first load brings its row."""
        self._pending_open = match_id

    def sync(self, rows) -> None:
        """Repaint from the board's latest copy of the open match's row."""
        if self._pending_open is not None:
            pending, self._pending_open = self._pending_open, None
            if any(r.get('id') == pending for r in rows):
                self.view._bg(self.open(pending, record=False))
                self._param.set(pending, history='replace')
            else:
                # A DM outlives its match: a deleted or filtered-out one must
                # say so rather than leave a link that silently does nothing.
                with self.dialog:
                    ui.notify("That match isn't on this board any more.", color='warning')
                if self._param.value == str(pending):
                    self._param.set('', history='replace')
        if self.row is None:
            return
        fresh = next((r for r in rows if r.get('id') == self.row.get('id')), None)
        if fresh is not None:
            self.row = fresh
        self._content.refresh()
        self.view._bg(self._reload_comments())

    def _run(self, handler, *args) -> None:
        """Run a lifecycle callback the way the board runs its own row events.

        Every control here lives inside ``_content``, and the write a callback
        makes pushes ``match_live``, which repaints ``_content`` before the
        callback has finished. Awaited from the control's own event, the
        callback is left inside a slot whose element has just been deleted, so
        the client stash the tenant and display zone fall back to is out of
        reach and its closing row refresh raises "No tenant in context". The
        board's ``_bg`` rebinds them from what it captured at page build.
        """
        self.view._bg(handler(*args))

    def _run_busy(self, e, text: str, handler, *args) -> None:
        """``_run`` for a slow callback, with the pressed button busy meanwhile."""
        async def go() -> None:
            async with busy(e.sender, text):
                await handler(*args)
        self.view._bg(go())

    def _find(self, match_id) -> Optional[dict]:
        rows = self.view.table.rows if self.view else []
        return next((r for r in rows if r.get('id') == match_id), None)

    # -- rendering -------------------------------------------------------

    def _render(self) -> None:
        row = self.row
        if row is None:
            return
        self.decorate(row)
        with ui.column().classes('match-sheet__body full-width'):
            self._headline(row)
            self._status(row)
            self._setup(row)
            if self.show_crew:
                self._crew(row)
            self._comments_section(row)
        self._footer(row)

    def _headline(self, row: dict) -> None:
        when = ' '.join(part for part in (row.get('_booked_day', row.get('_day')),
                                         row.get('_booked_time', row.get('_time'))) if part)
        context = ' · '.join(part for part in (when, row.get('tournament')) if part)
        ui.label(context).classes('match-sheet__note')
        if row.get('_who_parts'):
            matchup_label(row['_who_parts'], 'match-sheet__title', tag='h3')
        else:
            title = row.get('_who') or (row.get('bracket') or {}).get('name') or row.get('title') or 'Match'
            heading = ui.label(title).classes('match-sheet__title')
            heading.tag = 'h3'
        if row.get('description'):
            with ui.column().classes('match-sheet__description gap-0 full-width'):
                render_user_blocks(parse_user_blocks(row['description']))
        bracket = row.get('bracket')
        if bracket and bracket.get('id'):
            label = bracket.get('name') or 'Bracket'
            if bracket.get('game'):
                label += f" · Game {bracket['game']}"
            ui.button(label, icon='account_tree',
                      on_click=lambda: self.view._handle_open_bracket(SimpleNamespace(args=row))) \
                .props('flat dense no-caps color=primary').classes('self-start')
        if self._tracker_link:
            link = self._tracker_link
            ui.button('Item tracker', icon='grid_view', on_click=lambda: ui.navigate.to(link)) \
                .props('flat dense no-caps color=primary').classes('self-start')

    def _status(self, row: dict) -> None:
        with ui.row().classes('items-center gap-2 full-width match-sheet__status'):
            with ui.element('span').classes(f"wiz-chip wiz-chip--{row['_tone']}"):
                ui.label(row['_status'])
            if row.get('forfeit'):
                ui.label(row['forfeit']).classes('match-sheet__note')
            since = _STATE_SINCE.get(row.get('state') or '')
            if since and row.get('state_time'):
                ui.label(f"{since} {row['state_time']}").classes('match-sheet__note')
            ui.space()
            self._primary_actions(row)

    def _primary_actions(self, row: dict) -> None:
        """The one thing to do next, and the result correction beside a result."""
        match_id = row['id']
        state = row.get('state')
        h = self.handlers
        if self.access.can_run_row(row) and row.get('players') and not row.get('is_racetime'):
            if state == 'Scheduled':
                ui.button('Check in', icon='event_seat',
                          on_click=lambda: self._run(h.on_seat, match_id)).props('color=primary')
            elif state == 'Checked In':
                ui.button('Start match', icon='play_arrow',
                          on_click=lambda: self._run(h.on_start, match_id)).props('color=primary')
            elif state == 'Started':
                ui.button('Finish', icon='sports_score',
                          on_click=lambda: self._run(h.on_finish, match_id)).props('color=primary')
        if self.access.can_confirm_row(row) and state == 'Finished':
            if not row.get('has_result'):
                ui.button('Record winner', icon='emoji_events',
                          on_click=lambda: self._run(h.on_edit_result, match_id)).props('color=primary')
            else:
                ui.button('Change result', on_click=lambda: self._run(h.on_edit_result, match_id)) \
                    .props('flat color=primary')
                ui.button('Review result' if row.get('needs_review') else 'Confirm result',
                          icon='verified', on_click=lambda: self._run(h.on_confirm, match_id)) \
                    .props('color=primary')
        if self.access.can_confirm_row(row) and state == 'Confirmed':
            ui.button('Change result', on_click=lambda: self._run(h.on_edit_result, match_id)) \
                .props('flat color=primary')
        if row.get('is_racetime') and state not in ('Finished', 'Confirmed'):
            ui.label('Runs on racetime.gg').classes('match-sheet__note')

    def _setup(self, row: dict) -> None:
        """The setup as a checklist: what's ready, what's missing, and the fix
        on the same line. Before the match starts it counts what's ready."""
        items = readiness(row, crew=self.show_crew)
        counted = [i for i in items if i['ready'] is not None]
        before = row.get('state') in ('Scheduled', 'Checked In')
        title = (f"Ready to play? {sum(1 for i in counted if i['ready'])} of {len(counted)}"
                 if before and counted else 'Setup')
        heading = ui.label(title).classes('match-sheet__section')
        heading.tag = 'h4'
        with ui.column().classes('match-sheet__checklist full-width gap-0'):
            for item in items:
                with ui.row().classes('match-sheet__check no-wrap full-width'):
                    if item['ready'] is True:
                        ui.icon('check_circle').classes('match-sheet__check-icon st-ok') \
                            .props('aria-label=Ready role=img')
                    elif item['ready'] is False:
                        ui.icon('error_outline').classes('match-sheet__check-icon st-pending') \
                            .props('aria-label="Not ready" role=img')
                    else:
                        ui.icon('remove_circle_outline').classes('match-sheet__check-icon text-muted') \
                            .props('aria-hidden=true')
                    with ui.column().classes('match-sheet__check-body col gap-1'):
                        ui.label(item['label']).classes('match-sheet__check-label')
                        getattr(self, f"_check_{item['key']}")(row, item)

    def _check_players(self, row: dict, item: dict) -> None:
        ui.label(item['detail']).classes('match-sheet__note')
        for player in row.get('players') or []:
            if player.get('ada'):
                name = player.get('name') or ''
                ui.button(f'{name}: ADA accommodation', icon='accessible',
                          on_click=lambda p=player: self.view._show_accommodation(
                              {'name': p.get('name'), 'note': p.get('ada_note')})) \
                    .props('flat dense no-caps color=primary').classes('self-start')

    def _check_stations(self, row: dict, item: dict) -> None:
        with ui.row().classes('items-center no-wrap full-width gap-2'):
            ui.label(item['detail']).classes('match-sheet__note col')
            if self.access.can_run_row(row) and row.get('state') in ('Scheduled', 'Checked In', 'Started'):
                ui.button('Assign' if item['ready'] is False else 'Change',
                          on_click=lambda: self._run(self.handlers.on_assign_stations, row['id'])) \
                    .props('outline dense no-caps color=primary').classes('match-sheet__check-act')

    def _check_stage(self, row: dict, item: dict) -> None:
        match_id = row['id']
        with ui.row().classes('items-center no-wrap full-width gap-2'):
            if self.access.can_assign_stream_row(row) and self.view is not None:
                options = {_NO_STAGE: 'No stage'}
                for opt in self.view._stage_options() or []:
                    if opt['value'] is not None:
                        options[opt['value']] = opt['label']
                current = row.get('stage_id') or (CANDIDATE_STAGE if row.get('is_stream_candidate') else _NO_STAGE)

                def set_stage(e, mid=match_id):
                    value = e.value if e.value != _NO_STAGE else None
                    self._run(self.handlers.on_set_stage, mid, value)

                ui.select(options, value=current, on_change=set_stage) \
                    .props('dense outlined options-dense aria-label="Stage"').classes('match-sheet__select col')
            else:
                ui.label(item['detail']).classes('match-sheet__note col')
            if row.get('stage_url'):
                ui.link('Watch', row['stage_url'], new_tab=True).classes('match-sheet__check-act')

    def _check_seed(self, row: dict, item: dict) -> None:
        seed = row.get('generated_seed')
        with ui.row().classes('items-center no-wrap full-width gap-2'):
            if seed and str(seed).startswith(('http://', 'https://')):
                ui.link(seed, seed, new_tab=True).classes('match-sheet__seed col')
            elif seed:
                ui.label(seed).classes('match-sheet__seed col')
            else:
                ui.label(item['detail']).classes(
                    'col ' + ('st-cancelled' if row.get('seed_roll_error') else 'match-sheet__note'))
            if seed:
                with ui.column().classes('gap-2 items-stretch no-wrap'):
                    ui.button('Copy', on_click=lambda: copy_to_clipboard(seed, 'Seed')) \
                        .props('outline dense no-caps color=primary').classes('match-sheet__check-act')
                    if self.access.can_edit_row(row) and hasattr(self.handlers, 'on_clear_seed'):
                        ui.button('Clear…', on_click=lambda: self._run(self.handlers.on_clear_seed, row['id'])) \
                            .props('outline dense no-caps color=negative aria-label="Clear seed"') \
                            .classes('match-sheet__check-act')
            elif not row.get('seed_rolling_since') and self.access.can_run_row(row) and seed_rollable(row):
                ui.button('Generate', on_click=lambda e: self._run_busy(
                    e, 'Rolling…', self.handlers.on_generate_seed, row['id'])) \
                    .props('outline dense no-caps color=primary').classes('match-sheet__check-act')
        if seed and row.get('seed_code'):
            ui.label(row['seed_code']).classes('seed-code')
        blocked = row.get('seed_dm_blocked') or []
        if seed and blocked:
            ui.label("Couldn't DM " + ', '.join(str(b) for b in blocked) + '. Hand them the seed.') \
                .classes('match-sheet__note')

    def _check_crew(self, row: dict, item: dict) -> None:
        ui.label(item['detail']).classes('match-sheet__note')

    def _crew(self, row: dict) -> None:
        wanted = row.get('crew_wanted') or {}
        roles = [r for r in _ROLES if wanted.get(r[0]) or row.get(r[0])]
        if not roles:
            return
        heading = ui.label('Crew').classes('match-sheet__section')
        heading.tag = 'h4'
        required = row.get('crew_required') or {}
        for key, role, label in roles:
            people = list(enumerate(row.get(key) or []))
            need = required.get(key) or 0
            approved = [(i, p) for i, p in people if p.get('approved')]
            waiting = [(i, p) for i, p in people if not p.get('approved')]
            with ui.column().classes('match-sheet__role full-width gap-2'):
                with ui.row().classes('match-sheet__role-head items-baseline no-wrap full-width'):
                    title = ui.label(label).classes('match-sheet__role-title col')
                    title.tag = 'h5'
                    if need and len(approved) < need and row.get('crew_signup_open'):
                        ui.label(f'{need - len(approved)} more needed').classes('match-sheet__short')
                if not people:
                    ui.label('No signups yet').classes('match-sheet__note italic-note')
                    continue
                with ui.row().classes('match-sheet__approved items-center'):
                    ui.label('Approved:' if approved else 'Approved: nobody yet').classes('match-sheet__note')
                    for idx, person in approved:
                        self._approved_pill(row, role, idx, person)
                if waiting:
                    self._waiting(row, role, waiting)

    def _profile(self, row: dict, role: str, idx: int):
        event = SimpleNamespace(args={'row': row, 'idx': idx})
        return lambda: self.view._handle_edit_role(role, event)

    def _toggle(self, row: dict, role: str, idx: int):
        event = SimpleNamespace(args={'row': row, 'idx': idx})
        return lambda: self.view._handle_toggle_approval(role, event)

    def _approved_pill(self, row: dict, role: str, idx: int, person: dict) -> None:
        name = person.get('name') or ''
        with ui.element('span').classes('match-sheet__pill'):
            ui.label(name).classes('match-sheet__pill-name')
            if person.get('acknowledged'):
                ui.label('confirmed').classes('match-sheet__pill-state st-ok')
            else:
                ui.label("hasn't confirmed").classes('match-sheet__pill-state')
            if self.access.can_approve_crew_row(row):
                with ui.button(icon='more_vert').props(f'flat round dense size=sm color=primary aria-label="More for {name}"'):
                    with ui.menu():
                        ui.menu_item('Open profile', on_click=self._profile(row, role, idx))
                        ui.menu_item('Un-approve', on_click=self._toggle(row, role, idx))

    def _waiting(self, row: dict, role: str, waiting: list) -> None:
        # The people waiting on staff get the weight; approved crew shrinks to
        # pills above. Three runs of link-coloured text per person read as one blur.
        with ui.column().classes('match-sheet__waiting full-width gap-0'):
            heading = 'Waiting for you' if self.access.can_approve_crew_row(row) else 'Signed up'
            ui.label(f'{heading} · {len(waiting)}').classes('match-sheet__waiting-head')
            for idx, person in waiting:
                with ui.row().classes('match-sheet__waiting-row items-center no-wrap full-width'):
                    name = person.get('name') or ''
                    if self.access.can_approve_crew_row(row):
                        ui.button(name, on_click=self._profile(row, role, idx), color=None) \
                            .props('flat dense no-caps align=left').classes('match-sheet__waiting-name col') \
                            .tooltip(f"Open {name}'s profile")
                        ui.button('Approve', on_click=self._toggle(row, role, idx)).props('no-caps color=primary')
                    else:
                        ui.label(name).classes('match-sheet__waiting-name col')

    # -- comments ------------------------------------------------------

    async def _load_comments(self) -> None:
        if self.row is None:
            self._comments = []
            return
        if self._viewer is None and self._session.get('authenticated'):
            self._viewer = await get_user_from_session(self._session)
        try:
            self._comments = await MatchCommentService().list_for_viewer(self.row['id'], self._viewer)
        except NotFoundError:
            self._comments = []

    async def _reload_comments(self) -> None:
        await self._load_comments()
        if self.row is not None:
            with self.dialog:
                self._content.refresh()

    def _plays(self, row: dict) -> bool:
        viewer_id = getattr(self._viewer, 'id', None)
        return viewer_id is not None and any(
            p.get('user_id') == viewer_id for p in row.get('players') or []
        )

    def _comments_section(self, row: dict) -> None:
        """The match's comments, then a way to add one.

        Staff (anyone who can run the match) read every comment and choose
        between a private note and a message the players read. A player reads
        their own comments and staff's messages, and writes to staff.
        """
        staff = self.access.can_run_row(row)
        player = self._plays(row)
        if not (staff or player):
            return
        count = f' · {len(self._comments)}' if self._comments else ''
        heading = ui.label(f'Comments{count}').classes('match-sheet__section')
        heading.tag = 'h4'
        if staff and not self._comments:
            ui.label('No comments yet.').classes('match-sheet__note')
        for comment in self._comments:
            self._comment(comment, staff)
        if staff:
            self._staff_composer(row)
        else:
            self._player_composer()

    def _comment(self, comment: dict, staff: bool) -> None:
        with ui.column().classes('match-sheet__comment full-width gap-1'):
            with ui.row().classes('match-sheet__comment-head items-center no-wrap full-width'):
                with ui.row().classes('match-sheet__comment-byline items-center'):
                    ui.label(comment['author']).classes('match-sheet__comment-author')
                    if comment['own']:
                        ui.label('(you)').classes('wiz-you')
                    self._comment_tag(comment['kind'], staff, comment['own'])
                    ui.label(comment['when']).classes('match-sheet__note match-sheet__comment-when')
                if comment['can_delete']:
                    with more_menu(f"the comment from {comment['author']}"):
                        menu_action('delete', 'Delete comment',
                                    lambda cid=comment['id']: self._confirm_delete(cid), negative=True)
            with ui.element('div').classes('wiz-help-prose match-sheet__comment-body'):
                render_user_blocks(parse_user_blocks(comment['body']))

    @staticmethod
    def _comment_tag(kind: str, staff: bool, own: bool) -> None:
        if staff:
            tag = {
                'player': (None, 'Player', ''),
                'staff_private': ('lock', 'Private', 'match-sheet__tag--private'),
                'staff_to_players': ('visibility', 'Players see this', 'match-sheet__tag--shared'),
            }.get(kind)
        else:
            tag = ('shield', 'Staff', 'match-sheet__tag--shared') if kind == 'staff_to_players' else None
        if tag is None:
            return
        icon, label, tone = tag
        with ui.element('span').classes(f'match-sheet__tag {tone}'):
            if icon:
                ui.icon(icon).props('aria-hidden=true')
            ui.label(label)

    def _keep_draft(self, e) -> None:
        self._draft = e.value or ''

    def _staff_composer(self, row: dict) -> None:
        names = [p.get('name') or '' for p in row.get('players') or []]
        if self._compose is None:
            with ui.row().classes('match-sheet__comment-start no-wrap full-width'):
                ui.button('Private note', icon='lock', on_click=lambda: self._start('private')) \
                    .props('outline no-caps color=primary').classes('col')
                if names:
                    ui.button('Message players', icon='visibility', on_click=lambda: self._start('players')) \
                        .props('outline no-caps color=primary').classes('col')
            return
        to_players = self._compose == 'players'
        tone = 'match-sheet__composer--shared' if to_players else ''
        with ui.column().classes(f'match-sheet__composer {tone} full-width gap-0'):
            with ui.row().classes('match-sheet__composer-head items-center no-wrap full-width'):
                ui.icon('visibility' if to_players else 'lock').props('aria-hidden=true')
                ui.label(f"Message to {' and '.join(names)}" if to_players
                         else 'Private note. Only staff and proctors see it.')
            with ui.column().classes('match-sheet__composer-body full-width gap-2'):
                MarkdownEditor(
                    'Message' if to_players else 'Note', value=self._draft, compact=True,
                    on_change=self._keep_draft,
                    placeholder="What the players need to know" if to_players
                    else 'Something for the rest of staff',
                )
                with ui.row().classes('justify-end full-width gap-2'):
                    ui.button('Cancel', on_click=self._cancel).props('flat no-caps color=primary')
                    ui.button('Send to players' if to_players else 'Save note',
                              on_click=lambda: self._run(self._post, self._compose)) \
                        .props('no-caps color=primary')

    def _player_composer(self) -> None:
        with ui.column().classes('match-sheet__composer-body full-width gap-2'):
            MarkdownEditor(
                'Tell staff something about this match', value=self._draft, compact=True,
                on_change=self._keep_draft,
                placeholder="Running late, need a left-handed setup, that kind of thing",
            )
            with ui.row().classes('items-center no-wrap full-width gap-2'):
                ui.label("Only staff read what you send. Your opponent doesn't.") \
                    .classes('match-sheet__note col')
                ui.button('Send', on_click=lambda: self._run(self._post, 'player')) \
                    .props('no-caps color=primary')

    def _start(self, mode: str) -> None:
        self._compose = mode
        self._content.refresh()

    def _cancel(self) -> None:
        self._compose = None
        self._draft = ''
        self._content.refresh()

    async def _post(self, mode: Optional[str]) -> None:
        if self.row is None or self._viewer is None or mode is None:
            return
        service = MatchCommentService()
        try:
            if mode == 'player':
                await service.add_player_comment(self.row['id'], self._viewer, self._draft)
            else:
                await service.add_staff_comment(
                    self.row['id'], self._viewer, self._draft, to_players=mode == 'players',
                )
        except ValueError as e:
            with self.dialog:
                ui.notify(str(e), color='warning')
            return
        self._draft = ''
        self._compose = None
        await self._reload_comments()
        with self.dialog:
            ui.notify({'player': 'Sent to staff', 'players': 'Sent to the players'}.get(mode, 'Note saved'),
                      color='positive')

    def _confirm_delete(self, comment_id: int) -> None:
        ConfirmationDialog(
            "Delete this comment? This can't be undone.",
            on_confirm=lambda: self._run(self._delete, comment_id),
            confirm_text='Delete', title='Delete comment',
        ).open()

    async def _delete(self, comment_id: int) -> None:
        try:
            await MatchCommentService().delete_comment(comment_id, self._viewer)
        except ValueError as e:
            with self.dialog:
                ui.notify(str(e), color='warning')
            return
        await self._reload_comments()

    def _footer(self, row: dict) -> None:
        if not self.access.can_edit_row(row):
            return
        with dialog_actions().classes('justify-end'):
            ui.button('Edit match', icon='edit', on_click=lambda: self._run(self.handlers.on_edit, row['id'])) \
                .props('flat color=primary')
