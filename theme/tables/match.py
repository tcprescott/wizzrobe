import asyncio
from datetime import datetime, timezone

from nicegui import app, background_tasks, context, ui

from application.services import (
    AccommodationService,
    MatchDisplayService,
    MatchHardPresetService,
    MatchRescheduleService,
    MatchService,
    MatchStreamVolunteerService,
    MatchWatcherService,
    PlayerLateService,
    SeedReadyService,
    UserService,
)
from application.tenant_context import get_current_tenant_id
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
from theme.tables.match_fold import MatchBoardFoldMixin
from theme.tables.match_grid import render_grid_slot
from theme.tables.match_handlers import MatchTableHandlersMixin
from theme.tables.match_hold import MatchBoardHoldMixin
from theme.tables.match_player_run_sheet import (
    arrange_player_run_sheet,
    register_player_run_sheet_slots,
)
from theme.tables.match_run_sheet import arrange_run_sheet, register_run_sheet_slots, station_lookup_caption
from theme.tables.match_slots import (
    CANDIDATE_STAGE,
    MY_ACTIONS_COLUMN,
    register_body_slots,
)
from theme.tables.match_stamps import MatchRowStampsMixin
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

# Not []: tournament_ids=None means every tournament, so an empty narrowing would open the whole community.
_MATCHES_NOTHING = [0]


class MatchTableView(MatchFiltersMixin, MatchTableHandlersMixin, MatchBoardHoldMixin, MatchBoardFoldMixin,
                     MatchRowStampsMixin):
    """
    Encapsulates the match table UI and logic for admin/player dashboards.

    Owns the data refresh, single-row updates, and event wiring. The filter
    strip and its stored state live in ``MatchFiltersMixin`` (``match_filters``);
    the Vue slot templates live in ``match_slots`` (columns) and ``match_grid``
    (mobile grid); the event-handler coroutines live in ``MatchTableHandlersMixin``
    (``match_handlers``); the per-viewer row fields (watching, reschedule,
    accommodations, comment counts) live in ``MatchRowStampsMixin``
    (``match_stamps``). Uses MatchService for all data operations.
    """

    # Class-level so a view built without __init__ (tests' __new__ stubs) reads as a table.
    layout = 'table'
    on_open_match = None
    run_sheet_group = None
    proctor_run_sheet = False
    crew_discord_id = None
    friend_ids: frozenset = frozenset()

    def __init__(self, columns, get_query, admin_controls=False, access=None, extra_slots=None, submit_match_callback=None,
                 on_edit=None, on_generate_seed=None, on_seat=None, on_start=None, on_finish=None, on_report_winner=None, on_confirm=None,
                 on_edit_result=None, on_set_stage=None, on_assign_stations=None,
                 player_discord_id=None, crew_discord_id=None, grid_breakpoint='lt.md',
                 row_sort=None, row_filter=None, exclude_racetime=False, on_rows_changed=None, actions_first=False,
                 storage_key='match', default_state_filter=None, default_day_scope=ALL_DAYS, match_ids=None,
                 scope_tournament_ids=None, table_key=None, searchable=False,
                 show_accommodations=False, layout='table', on_open_match=None,
                 run_sheet_group=None, proctor_run_sheet=False, show_comment_counts=False,
                 extra_rows=None, extra_filters=None):
        self.columns = columns
        self.extra_filters = extra_filters
        self.extra_rows = extra_rows
        self.open_by_default: tuple = ()
        self.layout = layout
        self.on_open_match = on_open_match
        self.run_sheet_group = run_sheet_group
        self.proctor_run_sheet = proctor_run_sheet
        self.table_key = table_key
        self.searchable = searchable
        self.show_accommodations = show_accommodations
        self.show_comment_counts = show_comment_counts
        self._plan = None
        # Two concurrent re-fetches of one row let the staler read land last.
        self._row_update_lock = asyncio.Lock()
        self.get_query = get_query
        self.grid_breakpoint = grid_breakpoint
        self.admin_controls = admin_controls
        self.access = access or MatchBoardAccess()
        self.player_discord_id = player_discord_id
        self.crew_discord_id = crew_discord_id
        self.storage_key = storage_key
        self.default_state_filter = default_state_filter
        self.default_day_scope = default_day_scope
        self.row_sort = row_sort
        self.row_filter = row_filter
        # A deep link suspends the State filter but must never write to the stored filters.
        self.match_ids = list(match_ids) if match_ids else None
        # Hard bound: refresh intersects it with the Tournament filter so choosing one can't widen the board.
        self.scope_tournament_ids = list(scope_tournament_ids) if scope_tournament_ids is not None else None
        self.exclude_racetime = exclude_racetime
        self.on_rows_changed = on_rows_changed
        self.actions_first = actions_first
        self.extra_slots = extra_slots
        self.submit_match_callback = submit_match_callback
        self.on_edit = on_edit
        self.on_generate_seed = on_generate_seed
        self.on_seat = on_seat
        self.on_start = on_start
        self.on_finish = on_finish
        self.on_report_winner = on_report_winner
        self.on_confirm = on_confirm
        # Must never re-finish the match.
        self.on_edit_result = on_edit_result
        self.on_set_stage = on_set_stage
        self.on_assign_stations = on_assign_stations
        self.table = None
        self.tournament_filter = None
        self.tournaments_list = []
        self.stage_filter = None
        self.stages_list = []
        self.state_filter = None
        self.day_filter = None
        self.filters_card = None
        self.filter_badge = None
        self.filter_reset_desk = None
        self._reset_undo = None
        self._filters_open = False
        # Background refreshes run outside the request context; _bg rebinds this tenant or scoped reads raise.
        self._tenant_id = get_current_tenant_id()
        self._render_context = capture_render_context()
        self._initializing = True
        self.service = MatchService()
        self.display_service = MatchDisplayService()
        self.user_service = UserService()
        self.watcher_service = MatchWatcherService()
        self.stream_volunteer_service = MatchStreamVolunteerService()
        self.player_late_service = PlayerLateService()
        self.seed_ready_service = SeedReadyService()
        self.hard_preset_service = MatchHardPresetService()
        self.reschedule_service = MatchRescheduleService()
        self.accommodation_service = AccommodationService()
        self._setup_ui()

    @property
    def _quick_approve(self) -> bool:
        """Whether waiting crew get an Approve button on the admin run sheet."""
        return (self.layout == 'run_sheet' and not self.proctor_run_sheet
                and self.admin_controls and self.access.approve_crew)

    def _arrange(self, rows: list) -> list:
        """Group and order ``rows`` for a run-sheet layout; a table keeps its order."""
        if self.layout == 'player_run_sheet':
            return self._stamp_folded(arrange_player_run_sheet(
                rows, getattr(self, '_discord_id', None), self.friend_ids))
        if self.layout != 'run_sheet':
            return rows
        if self.run_sheet_group is None:
            arranged = arrange_run_sheet(rows)
        else:
            arranged = arrange_run_sheet(rows, self.run_sheet_group)
        return self._stamp_folded(arranged)

    def _bg(self, coro) -> None:
        """Schedule ``coro`` with this view's tenant *and* display zone rebound."""
        scoped_background(self._render_context, coro)


    def _register_table_slots(self, discord_id) -> None:
        """The 'table' layout: a slot per column, and the full card on a phone."""
        register_body_slots(
            self.table,
            admin_controls=self.admin_controls,
            access=self.access,
            discord_id=discord_id,
            extra_slots=self.extra_slots,
            has_edit=self.on_edit is not None,
            want_seed_slot=self.admin_controls and self.on_generate_seed is not None,
            want_seed_readonly=self.admin_controls and self.on_generate_seed is None,
            want_state_slot=self.admin_controls,
            want_stage_admin=self.admin_controls and self.on_set_stage is not None,
            want_stage_readonly=self.on_set_stage is None,
        )

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

        phone_search_slot = ui.column().classes('lt-md full-width gap-1 run-sheet__phone-search') \
            if self.proctor_run_sheet and self.searchable else None
        search_slot, gear_slot = self._build_filter_bar()

        self._bg(self._initial_load())

        with ui.column().classes('full-width') as table_container:
            self.table_container = table_container
            self.table = ui.table(
                columns=self.columns,
                rows=[],
                row_key='id',
            # No 'update:pagination' handler: Quasar pages over the rows already loaded.
                pagination={'rowsPerPage': 25, 'page': 1},
            ).classes('match-table match-table-container').props(f':grid="Quasar.Screen.{self.grid_breakpoint}"')
            self.table.add_slot('no-data', no_data_slot('No matches to show yet.',
                                                        from_table=self.proctor_run_sheet))
        self._build_tidy_pill()

        discord_id = app.storage.user.get('discord_id', None)
        # Kept for _arrange: background row updates can't reach app.storage.user.
        self._discord_id = discord_id

        if self.layout in ('run_sheet', 'player_run_sheet'):
            if self.layout == 'run_sheet':
                register_run_sheet_slots(
                    self.table, proctor=self.proctor_run_sheet,
                    can_run=self.proctor_run_sheet and self.admin_controls and self.access.run,
                    approve=self.access.crew_js() if self._quick_approve else 'false',
                    run=(self.access.run_js() if not self.proctor_run_sheet and self.admin_controls
                         and self.on_report_winner is not None else 'false'),
                )
                if self._quick_approve:
                    self.table.on('approve_crew', lambda event: self._handle_quick_approve(event.args))
            else:
                register_player_run_sheet_slots(
                    self.table,
                    menu=any(c.get('name') == MY_ACTIONS_COLUMN for c in self.columns),
                )
            if self.on_open_match is not None:
                self.table.on('open_match', lambda event: self._bg(self.on_open_match(event.args)))
            self._wire_folding()
        else:
            self._register_table_slots(discord_id)

        if self.table_key:
            # After the item slot is registered, never before: the card is generated from these columns at build time.
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
                    box = search_input(
                        self.table,
                        placeholder='Search names or a station…' if self.proctor_run_sheet else 'Search matches…',
                        search_terms=self.layout in ('run_sheet', 'player_run_sheet'))
                    if self.proctor_run_sheet:
                        station_lookup_caption(self.table, box)
                if phone_search_slot is not None:
                    with phone_search_slot:
                        phone_box = search_input(self.table, placeholder='Search names or a station…',
                                                 width='full-width', search_terms=True)
                        phone_box.bind_value(box)
                        station_lookup_caption(self.table, phone_box)
            with self.table_container:
                row_count_label(self.table, 'matches')
            sticky_header(self.table)

        self.table.on('acknowledge_match', lambda event: background_tasks.create(
            self._handle_acknowledge_match(event.args, context.client)))
        if self.show_accommodations:
            self.table.on('show_accommodation', lambda event: self._show_accommodation(event.args))

        for role in ['commentator', 'tracker', 'broadcaster']:
            self.table.on(f"view_{role}", lambda event, r=role: self._handle_edit_role(r, event))
            self.table.on(f"toggle_{role}", lambda event, r=role: self._handle_toggle_approval(r, event))

        if self.on_assign_stations is not None:
            self.table.on('assign_stations', lambda event: self._bg(self._handle_assign_stations(event)))

        self.table.on('signup_commentator', lambda event: self._handle_signup_or_undo_role('signup', 'commentator', event.args))
        self.table.on('signup_tracker', lambda event: self._handle_signup_or_undo_role('signup', 'tracker', event.args))
        self.table.on('undo_commentator', lambda event: self._handle_signup_or_undo_role('undo', 'commentator', event.args))
        self.table.on('undo_tracker', lambda event: self._handle_signup_or_undo_role('undo', 'tracker', event.args))
        self.table.on('signup_broadcaster', lambda event: self._handle_signup_or_undo_role('signup', 'broadcaster', event.args))
        self.table.on('undo_broadcaster', lambda event: self._handle_signup_or_undo_role('undo', 'broadcaster', event.args))
        self.table.on('invite_partner', lambda event: self._handle_invite_partner(event.args))
        self.table.on('cancel_partner_invite', lambda event: self._handle_cancel_partner_invite(event.args))

        self.table.on('acknowledge_commentator', lambda event: background_tasks.create(
            self._handle_acknowledge_crew('commentator', event, context.client)))
        self.table.on('acknowledge_tracker', lambda event: background_tasks.create(
            self._handle_acknowledge_crew('tracker', event, context.client)))
        self.table.on('acknowledge_broadcaster', lambda event: background_tasks.create(
            self._handle_acknowledge_crew('broadcaster', event, context.client)))

        if discord_id:
            self.table.on('toggle_watch', self._handle_toggle_watch)
            # Registered directly, not via _bg: _bg drops the client, so ui.notify would go nowhere.
            self.table.on('toggle_stream_volunteer', self._handle_toggle_stream_volunteer)
            # Direct, not _bg: the dialog renders into this client's slot context.
            self.table.on('request_reschedule', self._handle_request_reschedule)
            # Direct, not _bg: same reason.
            self.table.on('open_hard_preset', self._handle_open_hard_preset)
            self.table.on('running_late', self._handle_running_late)
            self.table.on('take_back_late', self._handle_take_back_late)
            self.table.on('seed_ready', self._handle_seed_ready)
            self.table.on('seed_not_ready', self._handle_seed_not_ready)

        # ui.navigate.to so the tenant root_path is applied.
        self.table.on('open_bracket', self._handle_open_bracket)

        # Through _bg: these reach scoped reads before any client context is restored.
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
                if self.on_report_winner is not None:
                    self.table.on('report_winner', lambda event: self._bg(self._handle_report_winner(event)))
                if self.on_confirm is not None:
                    self.table.on('confirm', lambda event: self._bg(self._handle_confirm(event)))
                if self.on_edit_result is not None:
                    self.table.on('edit_result', lambda event: self._bg(self._handle_edit_result(event)))
            if self.on_set_stage is not None:
                self.table.on('set_stage', lambda event: self._bg(self._handle_set_stage(event)))

        if self.on_edit is not None:
            self.table.on('edit_match', lambda event: self._bg(self._handle_edit(event)))

        register_view(self._on_remote_change)

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
            if self._visible_rows():
                self._hold(match_id)
            else:
                await self.refresh()
        else:
            await self.update_row_by_id(match_id, flash=True)

    async def refresh(self, *_args):
        """Refresh table data using service layer."""
        tournament_ids = None
        if self.tournament_filter and self.tournament_filter.value:
            tournament_ids = self.tournament_filter.value
        if self.scope_tournament_ids is not None:
            tournament_ids = (
                [t for t in tournament_ids if t in self.scope_tournament_ids]
                if tournament_ids else list(self.scope_tournament_ids)
            )
            if not tournament_ids:
                tournament_ids = _MATCHES_NOTHING

        stage_ids = None
        if self.stage_filter and self.stage_filter.value:
            stage_ids = self.stage_filter.value

        # Only safe while both hidden states imply finished_at IS NOT NULL (confirming requires a finish).
        state_filter = self.state_filter.value if self.state_filter else []
        if self.match_ids:
            state_filter = []
        only_upcoming = bool(state_filter) and not ({'Finished', 'Confirmed'} & set(state_filter))

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

        if self.extra_rows is not None and window is None and not self.match_ids and not stage_ids:
            rows = await self.extra_rows(
                rows, tournament_ids,
            )

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
            return

        match_data = await self.display_service.get_match_for_display(match_id)

        # A reload can replace ``table.rows`` during any await: re-find by id.
        idx = self._row_index(match_id)
        if idx is None:
            return
        if not match_data or (self.row_filter is not None and not self.row_filter(match_data)) \
                or not self._still_crews(match_data):
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
            for key in ('commentators', 'trackers', 'broadcasters')
            for person in row.get(key) or []
            if isinstance(person, dict)
        )

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

