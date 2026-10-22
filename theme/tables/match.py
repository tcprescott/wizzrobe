import asyncio
from datetime import datetime, timezone

from nicegui import app, background_tasks, context, ui

from application.services import (
    AccommodationService,
    MatchCommentService,
    MatchDisplayService,
    MatchHardPresetService,
    MatchRescheduleService,
    MatchService,
    MatchStreamVolunteerService,
    MatchWatcherService,
    PlayerLateService,
    UserService,
    get_user_from_session,
)
from application.tenant_context import get_current_tenant_id
from theme.dialog._helpers import form_dialog
from theme.empty_state import no_data_slot
from theme.realtime import register_view
from theme.tables.admin_crud import capture_render_context, scoped_background
from theme.tables.export import csv_export_button
from theme.tables.hard_preset_rows import (
    apply_hard_preset_state,
)
from theme.tables.match_access import MatchBoardAccess
from theme.tables.match_filters import (
    ALL_DAYS,
    MatchFiltersMixin,
    day_scope_window,
)
from theme.tables.match_grid import render_grid_slot
from theme.tables.match_handlers import MatchTableHandlersMixin
from theme.tables.match_hold import MatchBoardHoldMixin
from theme.tables.match_player_run_sheet import (
    arrange_player_run_sheet,
    keep_own_late_only,
    register_player_run_sheet_slots,
)
from theme.tables.match_run_sheet import arrange_run_sheet, register_run_sheet_slots
from theme.tables.match_slots import (
    CANDIDATE_STAGE,
    MY_ACTIONS_COLUMN,
    register_body_slots,
)
from theme.tables.plain_rows import set_plain_rows
from theme.tables.preferences import (
    customize_table,
    preferences_button,
    row_count_label,
    search_input,
    sticky_header,
)
from theme.tables.row_state import carry_viewer_row_state
from theme.timer_teardown import PageTimer

# Pagination, sorting, and filtering can be implemented server-side if needed for large datasets.

#: A tournament-id list no row can match. ``tournament_ids=None`` means "every
#: tournament", so an *empty* narrowing has to be spelled some other way or a
#: scoped board would fall open to the whole community.
_MATCHES_NOTHING = [0]


class MatchTableView(MatchFiltersMixin, MatchTableHandlersMixin, MatchBoardHoldMixin):
    """
    Encapsulates the match table UI and logic for admin/player dashboards.

    Owns the data refresh, single-row updates, and event wiring. The filter
    strip and its stored state live in ``MatchFiltersMixin`` (``match_filters``);
    the Vue slot templates live in ``match_slots`` (columns) and ``match_grid``
    (mobile grid); the event-handler coroutines live in ``MatchTableHandlersMixin``
    (``match_handlers``). Uses MatchService for all data operations.
    """

    #: Class-level defaults for the two layout attributes, so a view assembled
    #: without ``__init__`` (the tests' ``__new__`` stubs) reads as a table.
    layout = 'table'
    on_open_match = None
    run_sheet_group = None
    proctor_run_sheet = False
    crew_discord_id = None

    def __init__(self, columns, get_query, admin_controls=False, access=None, extra_slots=None, submit_match_callback=None,
                 on_edit=None, on_generate_seed=None, on_seat=None, on_start=None, on_finish=None, on_confirm=None,
                 on_edit_result=None, on_set_stage=None, on_assign_stations=None,
                 player_discord_id=None, crew_discord_id=None, grid_breakpoint='lt.md',
                 row_sort=None, row_filter=None, exclude_racetime=False, on_rows_changed=None, actions_first=False,
                 storage_key='match', default_state_filter=None, default_day_scope=ALL_DAYS, match_ids=None,
                 scope_tournament_ids=None, table_key=None, searchable=False,
                 show_accommodations=False, layout='table', on_open_match=None,
                 run_sheet_group=None, proctor_run_sheet=False, show_comment_counts=False):
        self.columns = columns
        # 'table' draws every column's control in its cell (and a full card per
        # match on a phone); 'run_sheet' draws one grouped line per match and
        # hands a tapped line to ``on_open_match(match_id)``, which shows the
        # controls (see match_run_sheet); 'player_run_sheet' is the same with
        # the viewer's own crew, acknowledge and watch buttons left on the line
        # (see match_player_run_sheet).
        self.layout = layout
        self.on_open_match = on_open_match
        # The run sheet's grouping (``match_run_sheet.admin_group`` when None)
        # and whether its lines are the proctor's: stations on the names and
        # the next lifecycle step as a button.
        self.run_sheet_group = run_sheet_group
        self.proctor_run_sheet = proctor_run_sheet
        # When set, this board's desktop columns follow the viewer's saved
        # layout. Each surface built on this class passes its **own** key: a
        # proctor's column choices must not follow them onto the admin board.
        self.table_key = table_key
        # A text box over the visible columns, for the boards where finding one
        # row is the actual task. Not persisted: it is working state.
        self.searchable = searchable
        # Whether this board marks players with an arranged ADA accommodation.
        # Opt-in per surface (admin Schedule, Proctor Station); the service still
        # decides per viewer, so a tournament admin on the same board sees none.
        self.show_accommodations = show_accommodations
        # Whether lines carry their player and staff comment counts. Staff
        # boards only: the staff number includes private notes.
        self.show_comment_counts = show_comment_counts
        self._plan = None
        # Serialises single-row updates. One service call can push match_live
        # twice (approving a move: once from update_match, once after the
        # decision is recorded), and two concurrent re-fetches of one row let
        # the earlier, staler read land last.
        self._row_update_lock = asyncio.Lock()
        self.get_query = get_query
        self.grid_breakpoint = grid_breakpoint
        self.admin_controls = admin_controls
        # What this viewer may do, one field per service gate (match_access).
        # Defaults to a spectator: the player-facing boards pass nothing, and
        # every capability-gated control is also behind ``admin_controls``.
        self.access = access or MatchBoardAccess()
        self.player_discord_id = player_discord_id
        # The board of matches this viewer signed up to crew (My Schedule's crew
        # section), where ``player_discord_id`` is the board of matches they play.
        self.crew_discord_id = crew_discord_id
        # Every board gets its own filter namespace and its own default state
        # set — see _skey. Pass a distinct storage_key from each construction
        # site; the fallback exists only so a bare view still works.
        self.storage_key = storage_key
        self.default_state_filter = default_state_filter
        self.default_day_scope = default_day_scope
        # Board-shaping hooks. ``row_sort`` reorders the fetched rows (the
        # proctor board sorts by what needs doing next rather than by clock),
        # ``exclude_racetime`` drops rows no on-site proctor can act on,
        # ``on_rows_changed`` lets a caller mirror the row set (a summary strip),
        # and ``actions_first`` hoists the mobile card's action row.
        self.row_sort = row_sort
        # A predicate every row must satisfy to stay on this board, applied to a
        # full refresh *and* to a single row arriving from a live update. Unlike
        # the State and Day filters it is not the operator's to change: the
        # tournament-room view exists to show rolled seeds for unplayed matches,
        # so a match that starts leaves the screen rather than lingering.
        self.row_filter = row_filter
        # A deep link focuses the board on specific matches. It narrows the query
        # and suspends the State filter — a link to a Confirmed match must not
        # land on an empty board because the board's default set hides it — but
        # it never writes to the stored filters, so leaving the focus restores
        # whatever the operator had chosen.
        self.match_ids = list(match_ids) if match_ids else None
        # A hard bound on which tournaments this board may show at all — set for
        # a viewer whose authority is per tournament. Distinct from the
        # Tournament *filter*, which is the operator's own choice **within** the
        # scope; ``refresh`` intersects the two, so choosing a tournament cannot
        # widen the board past what the viewer operates.
        self.scope_tournament_ids = list(scope_tournament_ids) if scope_tournament_ids is not None else None
        self.exclude_racetime = exclude_racetime
        self.on_rows_changed = on_rows_changed
        self.actions_first = actions_first
        self.extra_slots = extra_slots
        self.submit_match_callback = submit_match_callback
        # Optional callbacks for admin actions
        self.on_edit = on_edit
        self.on_generate_seed = on_generate_seed
        self.on_seat = on_seat
        self.on_start = on_start
        self.on_finish = on_finish
        self.on_confirm = on_confirm
        # Correcting an already-recorded result: the admin's half of "the proctor
        # records their best guess". Reaches the same service as on_finish's
        # dialog, but must never re-finish the match.
        self.on_edit_result = on_edit_result
        # Where this match is streamed, written from the row's Stage select —
        # a real stage, the ``Candidate`` pseudo-stage, or nothing.
        self.on_set_stage = on_set_stage
        self.on_assign_stations = on_assign_stations
        self.table = None
        self.tournament_filter = None
        self.tournaments_list = []  # Will be populated in _setup_ui
        self.stage_filter = None
        self.stages_list = []  # Will be populated in _setup_ui
        self.state_filter = None
        self.day_filter = None
        # Mobile collapsible-filter state (CSS gates the toggle/card to <1024px)
        self.filters_card = None
        self.filter_badge = None
        self.filter_reset_desk = None
        self._reset_undo = None
        self._filters_open = False
        # Capture the tenant at build time (context is live here). Background
        # tasks — filter/page refreshes, filter-option loads — run outside the
        # slot/request context where neither the contextvar nor the per-client
        # stash is reachable, so scoped repository reads would raise
        # ``require_tenant_id()``; ``_bg`` rebinds this captured tenant.
        self._tenant_id = get_current_tenant_id()
        self._render_context = capture_render_context()
        # True until the stored filters have been restored, so restoring them
        # does not each trigger their own table load. See _refresh_unless_initializing.
        self._initializing = True
        self.service = MatchService()
        self.display_service = MatchDisplayService()
        self.user_service = UserService()
        self.watcher_service = MatchWatcherService()
        self.stream_volunteer_service = MatchStreamVolunteerService()
        self.player_late_service = PlayerLateService()
        self.hard_preset_service = MatchHardPresetService()
        self.reschedule_service = MatchRescheduleService()
        self.accommodation_service = AccommodationService()
        self._setup_ui()

    def _arrange(self, rows: list) -> list:
        """Group and order ``rows`` for a run-sheet layout; a table keeps its order."""
        if self.layout == 'player_run_sheet':
            return arrange_player_run_sheet(rows, getattr(self, '_discord_id', None))
        if self.layout != 'run_sheet':
            return rows
        if self.run_sheet_group is None:
            return arrange_run_sheet(rows)
        return arrange_run_sheet(rows, self.run_sheet_group)

    def _bg(self, coro) -> None:
        """Schedule ``coro`` with this view's tenant *and* display zone rebound."""
        scoped_background(self._render_context, coro)


    def _register_table_slots(self, discord_id) -> None:
        """The 'table' layout: a slot per column, and the full card on a phone."""
        # The want_* flags mirror the callback availability so the
        # seed/state/stage slots register exactly as before (see match_slots).
        register_body_slots(
            self.table,
            admin_controls=self.admin_controls,
            access=self.access,
            discord_id=discord_id,
            extra_slots=self.extra_slots,
            has_edit=self.on_edit is not None,
            want_seed_slot=self.admin_controls and self.on_generate_seed is not None,
            want_seed_readonly=self.admin_controls and self.on_generate_seed is None,
            # Every operator's board gets the state cell, including one whose
            # viewer can run nothing: it carries the chips and timestamps, and
            # the capability flags inside it decide which buttons appear.
            want_state_slot=self.admin_controls,
            want_stage_admin=self.admin_controls and self.on_set_stage is not None,
            want_stage_readonly=self.on_set_stage is None,
        )

        # Register the mobile grid slot (see match_grid).
        render_grid_slot(
            self.table, self.columns,
            admin_controls=self.admin_controls, access=self.access, discord_id=discord_id,
            has_edit=self.on_edit is not None,
            actions_first=self.actions_first,
        )

    def _setup_ui(self):
        if self.submit_match_callback:
            with ui.row().classes('full-width row-spacing'):
                ui.button(
                    'Create Match' if self.admin_controls else 'Request Match',
                    icon='add',
                    on_click=self.submit_match_callback
                ).props('color=primary')

        search_slot, gear_slot = self._build_filter_bar()

        # Restore the filters, then load the table — one task, one load.
        self._bg(self._initial_load())

        with ui.column().classes('full-width') as table_container:
            self.table_container = table_container
            self.table = ui.table(
                columns=self.columns,
                rows=[],
                row_key='id',
            # Client-side paging over the rows already loaded. There is no
            # 'update:pagination' handler: Quasar pages over table.rows, so
            # re-querying the database on a page turn fetched data the browser
            # already held. Refresh stays an explicit button.
                pagination={'rowsPerPage': 25, 'page': 1},
            ).classes('match-table match-table-container').props(f':grid="Quasar.Screen.{self.grid_breakpoint}"')
            self.table.add_slot('no-data', no_data_slot('No matches to show yet.'))
        self._build_tidy_pill()

        # Resolve current user's discord_id once for slot templates and event wiring.
        discord_id = app.storage.user.get('discord_id', None)
        # Kept for _arrange: row updates run in background tasks, where
        # app.storage.user is out of reach.
        self._discord_id = discord_id

        if self.layout in ('run_sheet', 'player_run_sheet'):
            if self.layout == 'run_sheet':
                register_run_sheet_slots(
                    self.table, proctor=self.proctor_run_sheet,
                    can_run=self.proctor_run_sheet and self.admin_controls and self.access.run,
                )
            else:
                register_player_run_sheet_slots(
                    self.table,
                    menu=any(c.get('name') == MY_ACTIONS_COLUMN for c in self.columns),
                )
            if self.on_open_match is not None:
                self.table.on('open_match', lambda event: self._bg(self.on_open_match(event.args)))
        else:
            self._register_table_slots(discord_id)

        if self.table_key:
            # **After** the item slot is registered, never before: the card is
            # generated from these columns at build time, so applying a saved
            # layout here cannot reach it. Pinned by
            # tests/theme/test_table_preferences.py.
            self._plan = customize_table(self.table, self.columns, key=self.table_key)
            with gear_slot:
                csv_export_button(
                    'matches',
                    lambda: self._plan.columns if self._plan else self.columns,
                    lambda: self.table.rows,
                )
                preferences_button(self.table)
            if self.searchable:
                with search_slot:
                    search_input(self.table, placeholder='Search matches…')
            with self.table_container:
                row_count_label(self.table, 'matches')
            sticky_header(self.table)

        # --- Event wiring (handler bodies live in MatchTableHandlersMixin) ---
        self.table.on('acknowledge_match', lambda event: background_tasks.create(
            self._handle_acknowledge_match(event.args, context.client)))
        if self.show_accommodations:
            self.table.on('show_accommodation', lambda event: self._show_accommodation(event.args))

        for role in ['commentator', 'tracker']:
            self.table.on(f"view_{role}", lambda event, r=role: self._handle_edit_role(r, event))
            self.table.on(f"toggle_{role}", lambda event, r=role: self._handle_toggle_approval(r, event))

        if self.on_assign_stations is not None:
            self.table.on('assign_stations', lambda event: self._bg(self._handle_assign_stations(event)))

        self.table.on('signup_commentator', lambda event: self._handle_signup_or_undo_role('signup', 'commentator', event.args))
        self.table.on('signup_tracker', lambda event: self._handle_signup_or_undo_role('signup', 'tracker', event.args))
        self.table.on('undo_commentator', lambda event: self._handle_signup_or_undo_role('undo', 'commentator', event.args))
        self.table.on('undo_tracker', lambda event: self._handle_signup_or_undo_role('undo', 'tracker', event.args))

        self.table.on('acknowledge_commentator', lambda event: background_tasks.create(
            self._handle_acknowledge_crew('commentator', event, context.client)))
        self.table.on('acknowledge_tracker', lambda event: background_tasks.create(
            self._handle_acknowledge_crew('tracker', event, context.client)))

        if discord_id:
            self.table.on('toggle_watch', self._handle_toggle_watch)
            # Registered directly, exactly like toggle_watch beside it: both are
            # socket events handled inside the client's own context, where
            # ``ui.notify`` reaches the browser. ``_bg`` would rebind the tenant
            # but drop the client, so the confirmation would go nowhere.
            self.table.on('toggle_stream_volunteer', self._handle_toggle_stream_volunteer)
            # Same reasoning as the two above: the dialog it opens renders into
            # this client's slot context.
            self.table.on('request_reschedule', self._handle_request_reschedule)
            # Same reasoning again: it opens a dialog in this client's slot
            # context, and its confirmation is a toast in this browser.
            self.table.on('open_hard_preset', self._handle_open_hard_preset)
            self.table.on('running_late', self._handle_running_late)
            self.table.on('take_back_late', self._handle_take_back_late)

        # Bracket link: navigation only, so it needs no tenant rebind — and it
        # goes through ui.navigate.to precisely to pick up the tenant root_path.
        self.table.on('open_bracket', self._handle_open_bracket)

        # Admin-specific event wiring (slots registered above under the same
        # conditions). These handlers' callbacks reach scoped repository reads
        # (``require_tenant_id()``) before restoring any client context, so they
        # run through ``_bg`` to rebind the captured tenant — a socket event
        # carries neither the request contextvar nor a reachable client stash.
        if self.admin_controls:
            if self.on_generate_seed is not None:
                self.table.on('roll', lambda event: self._bg(self._handle_roll(event)))
            if (self.on_seat is not None or self.on_start is not None
                    or self.on_finish is not None or self.on_confirm is not None
                    or self.on_edit_result is not None):
                if self.on_seat is not None:
                    self.table.on('seat', lambda event: self._bg(self._handle_seat(event)))
                if self.on_start is not None:
                    self.table.on('start', lambda event: self._bg(self._handle_start(event)))
                if self.on_finish is not None:
                    self.table.on('finish', lambda event: self._bg(self._handle_finish(event)))
                if self.on_confirm is not None:
                    self.table.on('confirm', lambda event: self._bg(self._handle_confirm(event)))
                if self.on_edit_result is not None:
                    self.table.on('edit_result', lambda event: self._bg(self._handle_edit_result(event)))
            if self.on_set_stage is not None:
                self.table.on('set_stage', lambda event: self._bg(self._handle_set_stage(event)))

        if self.on_edit is not None:
            self.table.on('edit_match', lambda event: self._bg(self._handle_edit(event)))

        # Live updates: react to match changes made by other users.
        register_view(self._on_remote_change)

        # Keeps the "Rolling… 1:24" clock moving on any row with a seed roll in
        # flight. Five seconds, not one: the label is the difference between "the
        # page is working" and "the page is stuck", which does not need
        # per-second resolution, and this ticks once per open board.
        PageTimer(5.0, self._tick_rolling_labels)

    def _tick_rolling_labels(self) -> None:
        """Re-render the elapsed label on every row that is currently rolling.

        Pure formatting against a timestamp the row already carries — no query,
        and nothing at all to do when nothing is rolling, which is almost always.
        """
        from application.services.match.match_display_service import rolling_elapsed_label
        from application.utils.timezone import to_utc_aware

        touched = False
        for row in self.table.rows:
            started = row.get('seed_rolling_since')
            if not started:
                continue
            row['seed_rolling_label'] = rolling_elapsed_label(
                to_utc_aware(datetime.fromisoformat(started))
            )
            touched = True
        if touched:
            self.table.update()

    def _has_row(self, match_id) -> bool:
        return any(row.get('id') == match_id for row in self.table.rows)

    async def _on_remote_change(self, match_id, change_type):
        """Apply a match change broadcast from another user's action."""
        from application.events import match_live
        if change_type == match_live.CREATED or (
            self.row_filter is not None and not self._has_row(match_id)
        ):
            # A new row would push the board down under the viewer, so it waits
            # for the next tidy. On a filtered board an *existing* match can
            # become this board's business too (the room view's rows appear the
            # moment a seed is rolled, which publishes 'changed', not CREATED).
            if self._visible_rows():
                self._hold(match_id)
            else:
                await self.refresh()
        else:
            # 'changed' updates in place; 'deleted' removes the row.
            await self.update_row_by_id(match_id, flash=True)

    async def refresh(self, *_args):
        """Refresh table data using service layer."""
        tournament_ids = None
        if self.tournament_filter and self.tournament_filter.value:
            tournament_ids = self.tournament_filter.value
        if self.scope_tournament_ids is not None:
            # The scope wins: an empty intersection is an empty board, which is
            # the honest answer for "the tournament you picked is not yours".
            tournament_ids = (
                [t for t in tournament_ids if t in self.scope_tournament_ids]
                if tournament_ids else list(self.scope_tournament_ids)
            )
            if not tournament_ids:
                tournament_ids = _MATCHES_NOTHING

        stage_ids = None
        if self.stage_filter and self.stage_filter.value:
            stage_ids = self.stage_filter.value

        # When the active state filter shows only pre-finish states, exclude
        # finished/confirmed matches at the DB layer instead of hydrating the
        # entire (monotonically growing) match history and dropping them in
        # Python below. Confirming requires a finish, so both hidden states
        # share ``finished_at IS NOT NULL`` — exactly what ``only_upcoming``
        # filters — making this a behavior-preserving fast path for the default
        # Scheduled/Checked In/Started view (the highest-traffic schedule tab).
        state_filter = self.state_filter.value if self.state_filter else []
        if self.match_ids:
            state_filter = []
        only_upcoming = bool(state_filter) and not ({'Finished', 'Confirmed'} & set(state_filter))

        # The day scope goes to the database for the same reason: "Today" on a
        # 1,300-match event is ~300 rows, and hydrating the other thousand only to
        # drop them here cost most of the board's load. Suspended under a deep
        # link for the reason the state filter is: a link to one match must not
        # land on an empty board.
        window = None if self.match_ids else day_scope_window(
            self.day_filter.value if self.day_filter else self.default_day_scope)
        scheduled_window = None
        if window:
            scheduled_window = (
                datetime.fromtimestamp(window[0], timezone.utc),
                datetime.fromtimestamp(window[1], timezone.utc),
            )

        rows = await self.display_service.get_matches_for_display(
            tournament_ids=tournament_ids,
            stage_ids=stage_ids,
            only_upcoming=only_upcoming,
            user_discord_id=self.player_discord_id,
            crew_discord_id=self.crew_discord_id,
            exclude_racetime=self.exclude_racetime,
            match_ids=self.match_ids,
            scheduled_window=scheduled_window,
        )

        # Client-side filter by state (narrows within the fetched set)
        if state_filter:
            rows = [row for row in rows if row.get('state') in state_filter]

        watched_ids = await self._fetch_watched_ids()
        volunteered_ids = await self._fetch_stream_volunteered_ids()
        requestable_ids, asked_ids = await self._fetch_reschedule_state()
        hard_preset_states = await self._fetch_hard_preset_states(rows)
        await self._apply_accommodations(rows)
        await self._apply_comment_counts(rows)
        self._apply_late_visibility(rows)
        stage_options = self._stage_options()
        for row in rows:
            row['_watching'] = row.get('id') in watched_ids
            row['_stream_volunteer'] = row.get('id') in volunteered_ids
            apply_hard_preset_state(row, hard_preset_states.get(row.get('id')))
            row['_can_reschedule'] = row.get('id') in requestable_ids
            row['_reschedule_pending'] = row.get('id') in asked_ids
            if stage_options is not None:
                row['stage_options'] = stage_options

        if self.row_filter is not None:
            rows = [row for row in rows if self.row_filter(row)]

        if self.row_sort is not None:
            rows = self.row_sort(rows)
        rows = self._arrange(rows)

        set_plain_rows(self.table, rows)
        self._release_hold()
        self._notify_rows_changed()

    def _stage_options(self):
        """The Stage select's choices, or ``None`` for a board that cannot set one.

        Carried on every row rather than baked into the slot template: the stages
        load in a background task *after* the templates are registered, so a
        template built at registration time would offer an empty list forever.
        One shared list object per refresh, not a copy per row.

        ``No Stage`` leads, because "none" is one of the answers rather than the
        absence of one — it is what the cell shows when nothing is assigned, and
        it has to be selectable to be reversible. ``Candidate`` follows: the
        answer before a stage is decided, writing ``is_stream_candidate`` rather
        than a stage (see ``match_slots.CANDIDATE_STAGE``).
        """
        if self.on_set_stage is None:
            return None
        return [
            {'label': 'No Stage', 'value': None},
            {'label': 'Candidate', 'value': CANDIDATE_STAGE},
        ] + [
            {'label': name, 'value': stage_id}
            for stage_id, name in (self.stages_list or {}).items()
        ]

    def _notify_rows_changed(self) -> None:
        """Tell the caller the visible row set changed (drives a summary strip)."""
        if self.on_rows_changed is not None:
            self.on_rows_changed(self._visible_rows())

    async def _fetch_watched_ids(self) -> set:
        user = await get_user_from_session(app.storage.user)
        if not user:
            return set()
        return set(await self.watcher_service.list_watched_match_ids(user))

    async def _fetch_stream_volunteered_ids(self) -> set:
        """Matches this viewer has offered for stream — the toggle's own state.

        Separate from the row's ``stream_volunteers`` names, which are everyone's
        and are what staff read.
        """
        user = await get_user_from_session(app.storage.user)
        if not user:
            return set()
        return set(await self.stream_volunteer_service.list_volunteered_match_ids(user))

    async def _apply_comment_counts(self, rows) -> None:
        """Stamp ``comment_counts`` on each row, in one query, on a staff board."""
        if not (getattr(self, 'show_comment_counts', False) and self.access.run):
            return
        counts = await MatchCommentService().counts_by_match([row.get('id') for row in rows])
        for row in rows:
            row['comment_counts'] = counts.get(row.get('id'))

    def _apply_late_visibility(self, rows) -> None:
        """Keep who's running late on staff boards; elsewhere only the viewer's own.

        The heads-up is between a player and staff, so the opponent's row (and
        everyone's on Event > Schedule) must not carry it to the browser.
        """
        if getattr(self, 'show_comment_counts', False) and self.access.run:
            return
        keep_own_late_only(rows, getattr(self, '_discord_id', None))

    async def _apply_accommodations(self, rows) -> None:
        """Mark each player with an arranged ADA accommodation, in one query.

        Sets ``ada`` and ``ada_note`` on the player dicts the players cell
        renders. Nothing is set on a board that has not opted in, or for a
        viewer the service does not admit (it returns ``{}``). For a proctor
        the service withholds the note (``None``), and so does this: the
        popup is told so server-side rather than trusting what the row says.
        """
        if not self.show_accommodations:
            return
        user_ids = {p.get('user_id') for row in rows for p in row.get('players') or []}
        actor = await get_user_from_session(app.storage.user)
        notes = await self.accommodation_service.arranged_notes_for(actor, user_ids)
        if not notes:
            return
        self._ada_notes_readable = all(n is not None for n in notes.values())
        for row in rows:
            for player in row.get('players') or []:
                if player.get('user_id') in notes:
                    player['ada'] = True
                    player['ada_note'] = notes[player['user_id']]

    def _show_accommodation(self, args) -> None:
        """The popup behind a player's accessibility icon.

        Staff get the staff notes. A proctor gets that it's arranged and who to
        ask: the notes are written under a "only staff can read these" promise.
        """
        name = (args or {}).get('name') or 'This player'
        note = (args or {}).get('note')
        readable = getattr(self, '_ada_notes_readable', False) and note is not None
        with form_dialog(f'ADA accommodation: {name}', card_classes='dialog-card dialog-card--sm') as dialog:
            if readable:
                ui.label('Staff notes').classes('subsection-title q-mt-sm')
                # Plain text, never markup: staff wrote it.
                ui.label(note or 'Staff marked this arranged but left no notes.') \
                    .classes('text-body2').style('white-space: pre-wrap')
            else:
                ui.label(
                    f'Staff have arranged an accommodation for {name}. Check with '
                    'staff for what it involves before the match starts.'
                ).classes('text-body2 q-mt-sm')
        dialog.open()

    def _shows_column(self, name: str) -> bool:
        """Whether the board renders ``name``'s control, on its own or inside
        the player's combined "Your actions" cell (``MY_ACTIONS_COLUMN``)."""
        names = {c.get('name') for c in self.columns}
        return name in names or MY_ACTIONS_COLUMN in names

    async def _fetch_hard_preset_states(self, rows) -> dict:
        """This viewer's own hard-preset state for every visible row, in bulk.

        Skipped entirely on a board with no ``hard_preset`` column — the admin
        schedule and the proctor station render matches nobody is choosing
        settings on, so the queries would buy nothing.
        """
        if not self._shows_column('hard_preset'):
            return {}
        user = await get_user_from_session(app.storage.user)
        if not user:
            return {}
        match_ids = [row.get('id') for row in rows if row.get('id') is not None]
        return await self.hard_preset_service.board_states(user, match_ids)

    async def _fetch_reschedule_state(self) -> tuple:
        """``(requestable ids, already-asked ids)`` for this viewer, in bulk.

        Two sets rather than one flag because they drive different marks: the
        first shows the Ask button, the second shows the "Asked" chip on a row
        where the button is gone precisely *because* they already asked.

        Skipped entirely on a board with no ``reschedule`` column — the admin
        schedule, the proctor station and the home schedule all render matches
        and none of them offers the ask, so the two queries would buy nothing.
        """
        if not self._shows_column('reschedule'):
            return set(), set()
        user = await get_user_from_session(app.storage.user)
        if not user:
            return set(), set()
        requestable = await self.reschedule_service.list_requestable_match_ids(user)
        asked = set(await self.reschedule_service.pending_match_ids_for_user(user))
        return requestable, asked

    async def update_row_by_id(self, match_id, flash=False):
        """
        Update a single row in the table by its match ID, only if the row is currently visible.
        Uses service layer to fetch match data. When ``flash`` is set, the refreshed row
        briefly highlights so viewers notice a change made elsewhere.
        """
        async with self._row_update_lock:
            await self._update_row(match_id, flash)

    def _row_index(self, match_id):
        return next((i for i, row in enumerate(self.table.rows) if row.get('id') == match_id), None)

    async def _update_row(self, match_id, flash):
        if self._row_index(match_id) is None:
            return  # Row not visible, do nothing

        match_data = await self.display_service.get_match_for_display(match_id)

        # A reload can replace ``table.rows`` during any await: re-find by id.
        idx = self._row_index(match_id)
        if idx is None:
            return
        if not match_data or (self.row_filter is not None and not self.row_filter(match_data)) \
                or not self._still_crews(match_data):
            # Gone, or no longer this board's business (a room-view seed whose
            # match just started): it fades where it is until the next tidy.
            self._hold_removed(idx)
            self.table.update()
            self._notify_rows_changed()
            return

        carry_viewer_row_state(match_data, self.table.rows[idx])
        await self._restamp_shared_state(match_data)
        await self._apply_accommodations([match_data])
        await self._apply_comment_counts([match_data])
        self._apply_late_visibility([match_data])
        idx = self._row_index(match_id)
        if idx is None:
            return
        stage_options = self._stage_options()
        if stage_options is not None:
            match_data['stage_options'] = stage_options
        if flash:
            match_data['_flash'] = True
        if self.layout == 'table':
            self.table.rows[idx] = match_data
        else:
            # A state change belongs in another group, but the row stays put.
            self._place_in_hold(idx, match_data)
        self.table.update()
        self._notify_rows_changed()
        if flash:
            self._schedule_flash_clear(match_id)

    def _still_crews(self, row) -> bool:
        """On a crew board, whether the viewer still holds a slot on ``row``.

        Withdrawing updates the one row in place, and a row they left would
        otherwise stay on their own list offering Sign up.
        """
        if not self.crew_discord_id:
            return True
        return any(
            str(person.get('discord_id')) == str(self.crew_discord_id)
            for key in ('commentators', 'trackers')
            for person in row.get(key) or []
            if isinstance(person, dict)
        )

    async def _restamp_shared_state(self, row) -> None:
        """Recompute the per-viewer fields other people's actions change.

        The reschedule pair and the harder-preset fields (``RESTAMPED_ROW_FIELDS``)
        for this one row, through the same bulk lookups ``refresh`` uses: staff
        approving a move is exactly the ``match_live`` push that lands here.
        """
        if self._shows_column('reschedule'):
            requestable_ids, asked_ids = await self._fetch_reschedule_state()
            row['_can_reschedule'] = row.get('id') in requestable_ids
            row['_reschedule_pending'] = row.get('id') in asked_ids
        if self._shows_column('hard_preset'):
            states = await self._fetch_hard_preset_states([row])
            apply_hard_preset_state(row, states.get(row.get('id')))

    def _schedule_flash_clear(self, match_id):
        """Clear the transient highlight on a row a moment after it was set."""
        def clear():
            i = self._row_index(match_id)
            if i is not None and self.table.rows[i].get('_flash'):
                self.table.rows[i]['_flash'] = False
                self.table.update()
        PageTimer(1.6, clear, once=True)

    async def delete_row_by_id(self, match_id):
        """
        Delete a single row in the table by its match ID, only if the row is currently visible.
        Does not delete from the database, only removes from the table UI.
        """
        idx = self._row_index(match_id)
        if idx is not None:
            self._hold_removed(idx)
            self.table.update()

