"""The match board's filter strip: Day, Tournament, Stage, State.

Split out of ``theme/tables/match.py`` the way the grid, the row handlers and
the lifecycle callbacks already were — that module kept running at the
file-length guideline with four concerns still in it. :class:`MatchFiltersMixin`
is composed into ``MatchTableView`` and reaches ``self.display_service``,
``self.storage_key`` and ``self.refresh`` through it.

Every selection is stored per view *and* per tenant (``_skey`` +
``tenant_session_*``): four boards share one session, and a filter change on the
admin Schedule tab used to silently retarget the home schedule board and the
proctor station too.
"""

from datetime import timedelta
from typing import Any, Callable, Optional

from nicegui import ui

from application.services.match.match_display_service import NO_STAGE
from application.utils.tenant_session import tenant_session_get, tenant_session_set
from application.utils.timezone import local_day_bounds, today_local
from theme.timer_teardown import PageTimer

# Match lifecycle states, and the subset shown by default. Kept as module constants
# so the storage default, the select options, and the "is this filter changed?"
# check in _active_filter_count cannot drift out of sync.
ALL_MATCH_STATES = ['Scheduled', 'Checked In', 'Started', 'Finished', 'Confirmed']
DEFAULT_STATE_FILTER = ['Scheduled', 'Checked In', 'Started']

# Day scopes for the Day filter. The board had no day or now anchor at all, so
# an operator standing at a venue scrolled ~8 screenfuls of phone to find a
# match by eye — the cards carry every detail and nothing narrowed them to the
# day being run. ``ALL_DAYS`` is the default, so nothing is hidden until asked,
# except on the admin board, which opens on Today: at 1,300 matches All dates
# sent every row (2MB) on each open.
ALL_DAYS = 'All dates'

#: Seconds the reset chip offers Undo before it goes away.
RESET_UNDO_SECONDS = 8
DAY_SCOPES = [ALL_DAYS, 'Today', 'Tomorrow', 'Next 7 days']

#: ``scope -> (days from today for the first day, for the last day)``, inclusive.
_DAY_OFFSETS = {'Today': (0, 0), 'Tomorrow': (1, 1), 'Next 7 days': (0, 6)}


def day_scope_window(scope: str):
    """UTC ``(start, end)`` epoch seconds for a day scope, or ``None`` for all.

    Resolved on the *display* clock — "today" at 21:00 in New York is not today
    in London — and through ``local_day_bounds`` so the window is half-open and
    does not lose its final second.
    """
    offsets = _DAY_OFFSETS.get(scope)
    if offsets is None:
        return None
    first, last = offsets
    today = today_local()
    start, end = local_day_bounds(today + timedelta(days=first), today + timedelta(days=last))
    return start.timestamp(), end.timestamp()


class MatchFiltersMixin:
    """The filter strip, its stored state, and the options it offers."""

    default_day_scope: str = ALL_DAYS
    # Supplied by MatchTableView, which this is composed into.
    storage_key: str
    default_state_filter: Optional[list]
    _bg: Callable[[Any], None]
    refresh: Callable[[], Any]

    def _skey(self, name: str) -> str:
        """Session key for one of this view's filters.

        Namespaced per view: four boards share one session, and before this a
        filter change on the admin Schedule tab silently retargeted the home
        schedule board and the proctor station too.
        """
        return f'{self.storage_key}:{name}'

    def _stored_or_default_states(self) -> list:
        """This board's opening State selection.

        A stored choice wins; otherwise the board's own default (the admin
        board needs ``Finished`` — that set is its work). Split out of
        ``_setup_ui`` so the precedence is testable without a slot context.
        """
        return tenant_session_get(
            self._skey('state_filter'),
            list(self.default_state_filter or DEFAULT_STATE_FILTER),
        )

    def _refresh_unless_initializing(self) -> None:
        """Reload the table for a *user's* filter change.

        Restoring a stored filter during the initial load also fires these
        handlers, and each one used to schedule its own full reload — three table
        loads per page render instead of one. During initialization the reload is
        left to :meth:`_initial_load`, which runs once after every filter is
        restored (and therefore reads the final filter state).
        """
        if self._initializing:
            return
        self._bg(self.refresh())

    def _on_state_filter_change(self, *_args, **_kwargs):
        # Tenant-scoped: a filter is meaningful only within its own community.
        tenant_session_set(self._skey('state_filter'), self.state_filter.value)
        self._update_filter_badge()
        self._refresh_unless_initializing()

    def _on_day_filter_change(self, *_args, **_kwargs):
        tenant_session_set(self._skey('day_filter'), self.day_filter.value)
        self._update_filter_badge()
        self._refresh_unless_initializing()

    def _on_tournament_filter_change(self, *_args, **_kwargs):
        # Store the tournament ID value (namespaced by tenant — ids are global).
        tenant_session_set(self._skey('tournament_filter'), self.tournament_filter.value)
        self._update_filter_badge()
        self._refresh_unless_initializing()

    def _on_stage_filter_change(self, *_args, **_kwargs):
        # Store the stage ID value (namespaced by tenant — ids are global).
        tenant_session_set(self._skey('stage_filter'), self.stage_filter.value)
        self._update_filter_badge()
        self._refresh_unless_initializing()

    async def _initial_load(self) -> None:
        """Populate the filters, then load the table exactly once.

        The view owns its own first load: callers used to kick a `refresh()` of
        their own after constructing it, which raced the filter restore (the
        table could load before the stored tournament filter was applied) and
        cost an extra query fan-out.
        """
        try:
            await self._load_tournaments()
            await self._load_stages()
        finally:
            self._initializing = False
        await self.refresh()

    def _toggle_filters(self):
        """Show/hide the filter card on mobile; CSS gates ``wiz-filters-open`` to <1024px."""
        self._filters_open = not self._filters_open
        if self._filters_open:
            self.filters_card.classes(add='wiz-filters-open')
        else:
            self.filters_card.classes(remove='wiz-filters-open')

    def _active_filter_count(self) -> int:
        """Number of the three filters set away from this board's own default.

        Compared against ``default_state_filter`` rather than the module
        constant, so a board that legitimately defaults to a different state
        set does not permanently claim its own default is a custom filter.
        """
        count = 0
        if self.tournament_filter and self.tournament_filter.value:
            count += 1
        if self.stage_filter and self.stage_filter.value:
            count += 1
        default_states = set(self.default_state_filter or DEFAULT_STATE_FILTER)
        if self.state_filter and set(self.state_filter.value or []) != default_states:
            count += 1
        if self.day_filter and self.day_filter.value not in (None, self.default_day_scope):
            count += 1
        return count

    def _update_filter_badge(self):
        """Sync both reset chips (phone toggle row, desktop strip) with the selections."""
        count = self._active_filter_count()
        if count:
            self._reset_undo = None
        for chip, wide in self._reset_chips():
            if count:
                chip.props(f'label="{count} filter{"s" if count != 1 else ""}" icon-right=close'
                           if wide else f'label="{count}" icon-right=close')
                chip.props(f'aria-label="Reset {count} filter{"s" if count != 1 else ""}"')
            elif self._reset_undo is not None:
                chip.props('label=Undo icon-right=undo aria-label="Undo filter reset"')
            chip.set_visibility(bool(count) or self._reset_undo is not None)

    def _reset_chips(self):
        return [(chip, wide) for chip, wide in (
            (getattr(self, 'filter_badge', None), False),
            (getattr(self, 'filter_reset_desk', None), True),
        ) if chip is not None]

    def _filter_snapshot(self) -> dict:
        return {
            'day': self.day_filter.value,
            'tournament': self.tournament_filter.value,
            'stage': self.stage_filter.value,
            'state': list(self.state_filter.value or []),
        }

    def _apply_filters(self, values: dict) -> None:
        """Set every filter at once and reload the table a single time."""
        self._initializing = True
        try:
            self.day_filter.value = values['day']
            self.tournament_filter.value = values['tournament']
            self.stage_filter.value = values['stage']
            self.state_filter.value = values['state']
        finally:
            self._initializing = False
        self._bg(self.refresh())

    def _on_reset_chip(self, *_args) -> None:
        """One tap: back to this board's defaults, or put them back if Undo is showing."""
        if self._active_filter_count() == 0:
            undo, self._reset_undo = self._reset_undo, None
            if undo is not None:
                self._apply_filters(undo)
            self._update_filter_badge()
            return
        snapshot = self._filter_snapshot()
        self._apply_filters({
            'day': self.default_day_scope,
            'tournament': None,
            'stage': None,
            'state': list(self.default_state_filter or DEFAULT_STATE_FILTER),
        })
        self._reset_undo = snapshot
        self._update_filter_badge()
        undo_token = snapshot

        def expire() -> None:
            if self._reset_undo is undo_token:
                self._reset_undo = None
                self._update_filter_badge()

        with self.filters_card:
            PageTimer(RESET_UNDO_SECONDS, expire, once=True)

    async def _load_tournaments(self):
        """Load all tournament names for the filter using service layer."""
        self.tournaments_list = await self.display_service.get_tournaments_for_filter()
        # A scoped board offers only its own tournaments: the filter is a choice
        # within the scope, and listing the rest invites picking one that yields
        # an empty board for no visible reason.
        if self.scope_tournament_ids is not None:
            allowed = set(self.scope_tournament_ids)
            self.tournaments_list = {
                tid: name for tid, name in self.tournaments_list.items() if tid in allowed
            }
        default_tournament_id = tenant_session_get(self._skey('tournament_filter'), None)
        if self.tournament_filter:
            self.tournament_filter.options = self.tournaments_list
            self.tournament_filter.value = default_tournament_id
            self.tournament_filter.update()
        self._update_filter_badge()

    async def _load_stages(self):
        """Load all stage names for the filter using service layer."""
        self.stages_list = await self.display_service.get_stages_for_filter()
        default_stage_id = tenant_session_get(self._skey('stage_filter'), None)
        if self.stage_filter:
            # "No stage" leads: off-stage matches are the ones a floor runner
            # hunts for, and it mixes with real stages ("No stage" + Stage 2).
            self.stage_filter.options = {NO_STAGE: 'No stage', **self.stages_list}
            self.stage_filter.value = default_stage_id
            self.stage_filter.update()
        self._update_filter_badge()


    def _reset_chip(self):
        chip = ui.button(on_click=self._on_reset_chip) \
            .props('outline rounded dense no-caps no-wrap color=primary') \
            .classes('wiz-filter-reset')
        chip.set_visibility(False)
        return chip

    def _build_filter_bar(self):
        """Render the toggle row and the filter card; returns the two header slots.

        The search box and the preferences gear are rendered into those slots by
        the caller once the table exists — the strip is built first, for visual
        order.
        """
        # Mobile-only filter toggle (CSS hides this row >=1024px, where the card is
        # shown inline; below 1024px the card is collapsed until toggled open).
        # Each strip has an empty lead and end slot a page can fill with its own
        # controls (``toolbar_lead`` / ``toolbar_end``): one pair on the phone
        # row, one in the desktop card. Only one strip shows at a time, so a
        # control the page wants on both is built once into each.
        with ui.row().classes('wiz-filter-toggle full-width items-center no-wrap'):
            phone_lead = ui.row().classes('wiz-toolbar-slot items-center no-wrap')
            ui.button('Filters', icon='filter_list', on_click=self._toggle_filters) \
                .props('flat color=primary').classes('wiz-filter-toggle__btn')
            # The count is the reset: one tap clears every filter without
            # opening the card (Thomas's pick B).
            self.filter_badge = self._reset_chip()
            ui.space()
            ui.button(icon='refresh', on_click=self.refresh).props('flat color=primary round dense').tooltip('Refresh table')
            phone_end = ui.row().classes('wiz-toolbar-slot items-center no-wrap')

        # Each field carries its own label inside the box rather than a caption
        # line above it: with captions the open card stood ~550px tall on a
        # 393px phone, four half-width fields stacked one per line.
        self.filters_card = ui.card().classes('match-filters-card match-filters-card--compact')
        with self.filters_card:
            with ui.row().classes('match-filter-row'):
                desk_lead = ui.row().classes('wiz-toolbar-slot wiz-toolbar-slot--desk items-center no-wrap')
                self.filter_reset_desk = self._reset_chip().classes('wiz-filter-reset--desk')
                # Day filter, first because it is the coarsest narrowing and
                # the one an operator working a venue reaches for.
                self.day_filter = ui.select(
                    options=list(DAY_SCOPES),
                    label='Day',
                    value=tenant_session_get(self._skey('day_filter'), self.default_day_scope),
                    on_change=self._on_day_filter_change,
                ).classes('match-filter-field').props('outlined dense options-dense')

                # Multi-selects read as one line of text, not chips: chips
                # wrapped the State field to two lines on a phone.
                self.tournament_filter = ui.select(
                    options=[],
                    label='Tournament',
                    value=None,
                    multiple=True,
                    on_change=self._on_tournament_filter_change
                ).classes('match-filter-field').props('outlined dense options-dense')

                self.stage_filter = ui.select(
                    options=[],
                    label='Stage',
                    value=None,
                    multiple=True,
                    on_change=self._on_stage_filter_change
                ).classes('match-filter-field').props('outlined dense options-dense')

                self.state_filter = ui.select(
                    options=list(ALL_MATCH_STATES),
                    label='State',
                    value=self._stored_or_default_states(),
                    multiple=True,
                    on_change=self._on_state_filter_change
                ).classes('match-filter-field').props('outlined dense options-dense')

                # Search, then the CSV/preferences gear once the table exists,
                # then refresh (which a phone already has on the toggle row).
                with ui.row().classes('match-filter-tools items-center no-wrap'):
                    search_slot = ui.row().classes('items-center')
                    gear_slot = ui.row().classes('items-center')
                    ui.button(icon='refresh', on_click=self.refresh) \
                        .props('flat color=primary').classes('gt-sm').tooltip('Refresh table')
                    desk_end = ui.row().classes('wiz-toolbar-slot wiz-toolbar-slot--desk items-center no-wrap')

        self.toolbar_lead = (phone_lead, desk_lead)
        self.toolbar_end = (phone_end, desk_end)
        return search_slot, gear_slot
