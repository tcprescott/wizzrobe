"""Per-user table layouts — the reconciler between shipped defaults and saved config.

A table's columns are declared in code; a person's preferences are a *filter over*
that declaration, never a replacement for it. :func:`effective_columns` is where
the two meet, and it is deliberately pure: no NiceGUI, no DB, no context — just
the shipped column dicts, the stored blob, and the rules below.

The single most important rule is rule 3: **a default column the user has never
seen is appended, visible.** The opposite (honour the saved list exactly) makes
every newly added column invisible forever to everyone who has ever opened the
Preferences gear, and it is a bug that surfaces months later as a support ticket.

Column *names* are reconciled here rather than in
:class:`~application.services.TablePreferenceService`, because the authoritative
list lives in presentation and a service importing ``theme/`` would invert the
architecture. An unknown stored name is dropped silently at render time — that is
the mechanism for a developer deleting a column, not an error.
"""

import re
from dataclasses import dataclass, field
from typing import AbstractSet, Any, Final, Mapping, Optional, Sequence

from nicegui import background_tasks, context, ui

from application.services import TablePreferenceService
from application.table_preferences_context import table_prefs_for
from application.timezone_context import uses_12_hour
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.tables.admin_crud import current_actor

__all__ = [
    'DEFAULT_REQUIRED',
    'TABLE_KEYS',
    'ColumnPlan',
    'TableCustomization',
    'TableKeys',
    'apply_saved_config',
    'customization_of',
    'customize_table',
    'effective_columns',
    'preferences_button',
    'row_count_label',
    'search_input',
    'sticky_header',
]

# Columns a person may not hide: the row-action cells. Hiding them strands every
# row action behind no affordance at all, and the way back is a reset the user
# cannot find because the gear that offers it is in the toolbar, not the row.
DEFAULT_REQUIRED: Final[frozenset[str]] = frozenset({'actions', 'edit'})

# Column names are code identifiers, but a CSS class cannot assume that.
_CLASS_SAFE = re.compile(r'[^A-Za-z0-9_-]')


class TableKeys:
    """Every table's stable preference key, namespaced ``surface.table``.

    One place, so a key cannot be invented at a call site and two tables cannot
    quietly share one (which would have them trade column layouts).
    """

    # Match boards. Four surfaces share one class and each carries its own key:
    # a proctor's column choices must not follow them onto the admin board.
    # _v2 since the board became a run sheet (Time, Match, Stage, Status): v1
    # layouts name the old per-control columns, which no longer exist.
    # _v3 since the one Crew column became Commentators and Trackers: rule 3
    # would have appended both past Status on every saved v2 layout. _v4 for
    # the Broadcasters column, the same way.
    ADMIN_SCHEDULE = 'admin.schedule_v4'
    # _v2 for the same reason, when the Event > Schedule board followed it; _v3
    # for the same crew split, _v4 for Broadcasters.
    HOME_SCHEDULE = 'home.schedule_v4'
    # _v2 since the player's four action columns became one "Your actions"
    # beside Players: rule 3 appends a new default at the far right, so every
    # saved v1 layout would have put the player's own controls back past the
    # table's edge. A fresh key starts everyone on the shipped order; v1 rows
    # name columns that no longer exist, so there is little in them to keep.
    # _v3 since it became the player run sheet with a ⋮ per line, _v4 for
    # Broadcasters.
    HOME_PLAYER_MATCHES = 'home.player_matches_v4'
    # My Schedule's crew section, the same run sheet filtered to your slots.
    # _v2 for Broadcasters.
    HOME_MY_CREW = 'home.my_crew_v2'
    # _v2 since the proctor board became a run sheet too (Time, Match, Stage,
    # Next step); v1 layouts name the old Seed, Tournament and # columns.
    PROCTOR_STATION = 'volunteer.proctor_station_v2'
    # Not a ui.table: the Full Schedule grid's role columns, keyed by position id.
    VOLUNTEER_FULL_SCHEDULE = 'volunteer.full_schedule'
    # Not a ui.table either: Admin → Shift Board's role columns, as above.
    ADMIN_SHIFT_BOARD = 'admin.shift_board'
    # Not a ui.table: Match Schedule › Not booked yet, for the folded tournament
    # groups only, keyed "<tournament id>:<source>".
    ADMIN_UNBOOKED = 'admin.unbooked'

    # Admin
    ADMIN_USERS = 'admin.users'
    # _v2 since the board became six short columns with the description hidden:
    # rule 3 would have kept every v1 layout's Description and duration columns.
    # _v3 since the Bracket column joined beside Signups: rule 3 would have
    # appended it past the ⋮ on every saved v2 layout.
    ADMIN_TOURNAMENTS = 'admin.tournaments_v3'
    ADMIN_BRACKETS = 'admin.brackets'
    ADMIN_CHECK_IN_EVENTS = 'admin.check_in_events'
    ADMIN_DISCORD_EVENTS = 'admin.discord_events'
    ADMIN_DISCORD_EVENT_TOURNAMENTS = 'admin.discord_event_tournaments'
    ADMIN_DISCORD_ROLES = 'admin.discord_roles'
    ADMIN_VOLUNTEER_ROLE_MAPPINGS = 'admin.volunteer_role_mappings'
    ADMIN_EQUIPMENT = 'admin.equipment'
    ADMIN_ACCOMMODATIONS = 'admin.accommodations'
    # _v2 since the board became Submitted, From, Message, Status: a v1 layout
    # would have kept the old User, Category and Page columns.
    ADMIN_FEEDBACK = 'admin.feedback_v2'
    ADMIN_COMMUNITY_EVENTS = 'admin.community_events'
    ADMIN_EVENTS = 'admin.events'
    ADMIN_PAYOUTS = 'admin.payouts'
    ADMIN_QUALIFIERS = 'admin.qualifiers'
    ADMIN_QUALIFIER_LEADERBOARD = 'admin.qualifier_leaderboard'
    ADMIN_RACETIME = 'admin.racetime'
    ADMIN_SPEEDGAMING = 'admin.speedgaming'
    ADMIN_STAGES = 'admin.stages'
    ADMIN_VOLUNTEER_ROSTER = 'admin.volunteer_roster'
    ADMIN_WEBHOOK_DELIVERIES = 'admin.webhook_deliveries'

    # Check-in desk
    CHECK_IN_DESK = 'check_in.desk'

    # Player-facing
    # (No equipment.inventory: browsing the register left the player surface
    # when Home came down to four tabs, and the admin table has its own key.
    # Rows saved against the old key are simply never read. Nor equipment.mine:
    # your own loans became a short list on Profile.)
    QUALIFIERS_LIST = 'qualifiers.list'
    QUALIFIERS_LEADERBOARD = 'qualifiers.leaderboard'

    # Reports
    REPORTS_CAPACITY_FOCUS = 'reports.capacity_focus'
    REPORTS_CAPACITY_FORECAST = 'reports.capacity_forecast'
    REPORTS_CREW_CONTRIBUTIONS = 'reports.crew_contributions'
    REPORTS_CREW_COVERAGE = 'reports.crew_coverage'
    REPORTS_INSIGHTS_CREW = 'reports.insights_crew'
    REPORTS_INSIGHTS_PLAYERS = 'reports.insights_players'
    REPORTS_MATCH_OPS_DETAIL = 'reports.match_ops_detail'
    REPORTS_MATCH_OPS_SUMMARY = 'reports.match_ops_summary'
    REPORTS_SENT_MESSAGES = 'reports.sent_messages'
    REPORTS_EXPORT_TOURNAMENTS = 'reports.export_tournaments'
    REPORTS_EXPORTS = 'reports.exports'
    REPORTS_STAGE_MATCHES = 'reports.stage_matches'
    REPORTS_STAGE_SUMMARY = 'reports.stage_summary'
    REPORTS_TELEMETRY_LOG = 'reports.telemetry_log'
    REPORTS_TELEMETRY_TOP = 'reports.telemetry_top'
    REPORTS_TOURNAMENT_HEALTH = 'reports.tournament_health'
    REPORTS_VOLUNTEER_HOURS = 'reports.volunteer_hours'

    # Platform (super-admin)
    PLATFORM_FEATURE_GROUPS = 'platform.feature_groups'
    PLATFORM_APPS = 'platform.apps'
    PLATFORM_BROWSER_IGNORE_RULES = 'platform.browser_ignore_rules'
    PLATFORM_LOG_LEVELS = 'platform.log_levels'
    PLATFORM_RACETIME_BOTS = 'platform.racetime_bots'
    PLATFORM_SERVICE_HEALTH = 'platform.service_health'
    PLATFORM_TENANTS = 'platform.tenants'
    PLATFORM_USERS = 'platform.users'


def _declared_keys() -> frozenset[str]:
    return frozenset(
        value for name, value in vars(TableKeys).items()
        if not name.startswith('_') and isinstance(value, str)
    )


TABLE_KEYS: Final[frozenset[str]] = _declared_keys()


@dataclass(frozen=True)
class ColumnPlan:
    """What one table should paint for one viewer."""

    columns: list[dict]        # ordered, width/style applied, ready for table.columns
    visible: list[str]         # for Quasar's visible-columns prop
    page_size: int
    density: str
    wrap: bool
    is_customized: bool        # drives the gear's "modified" dot
    widths: dict[str, int] = field(default_factory=dict)

    @property
    def has_widths(self) -> bool:
        """Whether any column carries a stored width (⇒ the ``--sized`` class)."""
        return bool(self.widths)


def _merge_style(existing: Any, addition: str) -> str:
    existing = (existing or '').strip()
    if not existing:
        return addition
    if not existing.endswith(';'):
        existing += ';'
    return f'{existing} {addition}'


def _signature(columns: Sequence[Mapping], visible: Sequence[str],
               widths: Mapping[str, int]) -> list[tuple]:
    signature = []
    for column in columns:
        name = str(column.get('name', ''))
        signature.append((name, name in visible, widths.get(name)))
    return signature


def effective_columns(
    defaults: Sequence[Mapping],
    saved: Optional[Mapping],
    required: AbstractSet[str] = DEFAULT_REQUIRED,
    *,
    page_size: int = 0,
    density: str = 'comfortable',
    wrap: bool = False,
) -> ColumnPlan:
    """Reconcile a stored layout against the columns the code ships today.

    Rules, in order:

    1. No saved config ⇒ the defaults, unchanged, ``is_customized=False``.
    2. Saved entries match defaults **by name**; a saved name with no default is
       dropped (a developer removed the column).
    3. A default not named in the saved list is **appended at the end**, at its
       shipped visibility — visible unless the column declares ``hidden``.
    4. Any column in ``required`` is forced visible, whatever is stored.
    5. If nothing non-required would remain visible, the saved config is
       discarded wholesale. An empty table is never the intent, and it is
       unrecoverable without a reset the user cannot reach.
    6. A stored ``width`` becomes ``style`` / ``headerStyle`` on a **copy** of the
       default dict — the caller's module-level column lists are never mutated.
    7. ``page_size`` / ``density`` / ``wrap`` fall back to this table's shipped
       values when the stored config does not carry them.
    """
    shipped = [dict(c) for c in defaults]
    default_visible = [
        c.get('name', '') for c in shipped if not c.get('hidden')]
    fallback = ColumnPlan(
        columns=shipped,
        visible=default_visible,
        page_size=page_size,
        density=density,
        wrap=wrap,
        is_customized=False,
    )

    if not saved or not isinstance(saved, Mapping):
        return fallback

    by_name: dict[str, dict] = {}
    for col in shipped:
        by_name.setdefault(col.get('name', ''), col)

    saved_columns = saved.get('columns')
    ordered: list[tuple[dict, Optional[Mapping]]] = []
    seen: set[str] = set()
    if isinstance(saved_columns, (list, tuple)):
        for entry in saved_columns:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get('name')
            if not isinstance(name, str) or name in seen or name not in by_name:
                continue
            seen.add(name)
            ordered.append((by_name[name], entry))
    for col in shipped:
        name = col.get('name', '')
        if name not in seen:
            seen.add(name)
            ordered.append((col, None))

    columns: list[dict] = []
    visible: list[str] = []
    widths: dict[str, int] = {}
    for col, entry in ordered:
        out = dict(col)
        name = out.get('name', '')

        if entry is None:
            is_visible = not col.get('hidden')
        else:
            is_visible = bool(entry.get('visible', True))
        if name in required:
            is_visible = True

        width = entry.get('width') if entry is not None else None
        if isinstance(width, int) and not isinstance(width, bool) and width > 0:
            widths[name] = width
            # width alone is a *suggestion* the browser overrides from content;
            # the min/max pair is what actually pins the column. Deliberately not
            # `table-layout: fixed`, which would divide the remaining space
            # equally between every unsized column and clip their headers — one
            # dragged column on a nine-column board should not resize the other
            # eight.
            pinned = (f'width: {width}px; min-width: {width}px; '
                      f'max-width: {width}px')
            out['style'] = _merge_style(out.get('style'), pinned)
            out['headerStyle'] = _merge_style(out.get('headerStyle'), pinned)

        # Keep the app's own ``hidden`` convention in step with the plan so the
        # card builder and visible-columns never disagree about one column.
        if is_visible:
            out.pop('hidden', None)
            visible.append(name)
        else:
            out['hidden'] = True

        columns.append(out)

    if not [n for n in visible if n not in required]:
        return fallback

    resolved_page_size = page_size
    stored_page_size = saved.get('page_size')
    if isinstance(stored_page_size, int) and not isinstance(stored_page_size, bool):
        resolved_page_size = stored_page_size

    resolved_density = density
    if isinstance(saved.get('density'), str):
        resolved_density = saved['density']

    resolved_wrap = wrap
    if isinstance(saved.get('wrap'), bool):
        resolved_wrap = saved['wrap']

    is_customized = (
        _signature(columns, visible, widths)
        != _signature(shipped, default_visible, {})
        or resolved_density != density
        or resolved_wrap != wrap
    )

    return ColumnPlan(
        columns=columns,
        visible=visible,
        page_size=resolved_page_size,
        density=resolved_density,
        wrap=resolved_wrap,
        is_customized=is_customized,
        widths=widths,
    )


# ---------------------------------------------------------------- application

@dataclass
class TableCustomization:
    """Everything the gear needs, stashed on the table it belongs to."""

    key: str
    defaults: list[dict]
    required: AbstractSet[str]
    page_size: int
    density: str
    wrap: bool
    plan: ColumnPlan
    pager: bool = True
    badge: Any = None          # the gear's "modified" dot, when a gear was rendered


_STASH = '_wiz_table_customization'

# Quasar's footer order: ascending, with 0 ("All") last. The service's
# ALLOWED_PAGE_SIZES is the same set; test_table_preferences pins the two together.
ROWS_PER_PAGE_OPTIONS: Final[list[int]] = [10, 25, 50, 100, 0]


def customization_of(table: ui.table) -> Optional[TableCustomization]:
    """The customization stashed on ``table`` by :func:`customize_table`, if any."""
    return getattr(table, _STASH, None)


def customize_table(
    table: ui.table,
    defaults: Sequence[Mapping],
    *,
    key: str,
    required: AbstractSet[str] = DEFAULT_REQUIRED,
    page_size: Optional[int] = None,
    density: str = 'comfortable',
    wrap: bool = False,
    pager: bool = True,
) -> ColumnPlan:
    """Apply this viewer's saved preferences to ``table``'s **desktop** view.

    Call **after** ``enable_mobile_grid`` (or a bespoke ``item`` slot). That
    ordering is the whole mobile-card guarantee: the card slot is a static Vue
    string generated from the *defaults* at build time, addressing
    ``props.row.<field>`` directly, so it never reads ``table.columns`` or
    ``visible-columns`` and this call is invisible to it.
    ``test_preferences_do_not_touch_the_mobile_card`` pins that; if you find
    yourself reordering these calls, that test is the reason not to.

    Reads the primed contextvar synchronously — a table build cannot await, and a
    page can host a dozen tables. An unprimed context yields the shipped columns,
    which is a correct answer rather than a failure.

    ``page_size`` defaults to whatever the table was constructed with, so a
    report that ships ``pagination=25`` keeps it until someone chooses otherwise.

    Rows per page lives in the table's footer and nowhere else: picking a size
    there saves it for this viewer (:func:`_wire_page_size_saves`). ``pager=False``
    is for a table that pages on the server and draws its own pager — it gets no
    footer, and every row it is handed is shown.
    """
    if not pager:
        page_size = 0
    elif page_size is None:
        pagination = table._props.get('pagination') or {}
        page_size = pagination.get('rowsPerPage', 0) or 0

    saved = table_prefs_for(key)
    if not pager and saved:
        saved = {k: v for k, v in saved.items() if k != 'page_size'}
    plan = effective_columns(
        defaults, saved, required,
        page_size=page_size, density=density, wrap=wrap,
    )
    custom = TableCustomization(
        key=key,
        defaults=[dict(c) for c in defaults],
        required=required,
        page_size=page_size,
        density=density,
        wrap=wrap,
        plan=plan,
        pager=pager,
    )
    setattr(table, _STASH, custom)
    # Read off the DOM by static/js/table-columns.js to know which table a
    # dragged header belongs to; nothing else identifies one.
    table._props['data-wiz-table-key'] = key
    _apply_plan(table, plan, pager=pager)
    _wire_width_drags(table, key)
    if pager:
        _wire_page_size_saves(table, key)
    return plan


def _wire_page_size_saves(table: ui.table, key: str) -> None:
    """Save the footer's rows-per-page pick as this viewer's preference.

    ``update:pagination`` also fires on every page turn and sort, so only a
    changed ``rowsPerPage`` is written. A signed-out viewer keeps the choice for
    the life of the page. ``context.client`` is captured at build time for the
    same reason as :func:`_wire_width_drags`.
    """
    client = context.client

    async def _persist(args) -> None:
        if not isinstance(args, Mapping):
            return
        size = args.get('rowsPerPage')
        custom = customization_of(table)
        if custom is None or not isinstance(size, int) or isinstance(size, bool) \
                or size == custom.plan.page_size:
            return
        with client:
            actor = await current_actor()
            if actor is None:
                return
            try:
                saved = await TablePreferenceService().set_page_size(actor, key, size)
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            # Quasar already shows the new size; the stashed plan follows so the
            # gear's Confirm and the next comparison here see it too.
            custom.plan = effective_columns(
                custom.defaults, saved, custom.required,
                page_size=custom.page_size, density=custom.density, wrap=custom.wrap,
            )

    table.on('update:pagination', lambda e: background_tasks.create(_persist(e.args)))


def _wire_width_drags(table: ui.table, key: str) -> None:
    """Persist widths the resize client reports for this table.

    Registered on ``ui.on`` rather than ``table.on``: the drag is handled by a
    delegated listener in plain JS, outside Vue, which can only reach the app's
    global event channel — so every customized table listens and each one filters
    on its own key.

    ``context.client`` is captured **here**, at build time. Reading it inside the
    coroutine raises: a background task has no slot context (``check_slot_context``
    enforces the same rule for slot handlers).
    """
    client = context.client

    async def _persist(args) -> None:
        if not isinstance(args, Mapping) or args.get('key') != key:
            return
        width = args.get('width')
        column = args.get('column')
        if not isinstance(column, str):
            return
        with client:
            actor = await current_actor()
            if actor is None:
                return
            try:
                saved = await TablePreferenceService().set_width(
                    actor, key, column, int(width) if width is not None else None)
            except (ValueError, TypeError) as e:
                ui.notify(str(e), color='warning')
                return
            # The browser has already painted the new width, so nothing is
            # repainted here — but the stashed plan has to follow, or the modal
            # and the "customized" dot would show the pre-drag state.
            custom = customization_of(table)
            if custom is not None:
                custom.plan = effective_columns(
                    custom.defaults, saved, custom.required,
                    page_size=custom.page_size, density=custom.density,
                    wrap=custom.wrap,
                )
                if custom.badge is not None:
                    custom.badge.set_visibility(custom.plan.is_customized)

    ui.on('wiz_table_width', lambda e: background_tasks.create(_persist(e.args)))


def apply_saved_config(table: ui.table, saved: Optional[Mapping]) -> ColumnPlan:
    """Recompute and re-apply ``table``'s plan from a freshly saved config.

    Used by the preferences dialog, which already knows what it stored and must
    not wait for the next page load to show it.
    """
    custom = customization_of(table)
    if custom is None:
        raise ValueError('This table is not customizable.')
    plan = effective_columns(
        custom.defaults, saved, custom.required,
        page_size=custom.page_size, density=custom.density, wrap=custom.wrap,
    )
    custom.plan = plan
    _apply_plan(table, plan, pager=custom.pager)
    if custom.badge is not None:
        custom.badge.set_visibility(plan.is_customized)
    return plan


def _apply_plan(table: ui.table, plan: ColumnPlan, *, pager: bool = True) -> None:
    """Push a plan onto the live table.

    Quasar renders columns in array order and resolves ``body-cell-<name>`` slots
    by name, so reordering needs nothing beyond the new array — existing slots
    follow their column. Visibility goes through ``visible-columns``, the only
    mechanism QTable actually reads; row dicts are untouched, so ``row_key`` and
    every row handler keep working.
    """
    hour12 = uses_12_hour()
    table.columns = [_with_clock_sort(_with_header_class(c)) if hour12 else _with_header_class(c)
                     for c in plan.columns]
    table._props['visible-columns'] = list(plan.visible)

    pagination = dict(table._props.get('pagination') or {})
    pagination['rowsPerPage'] = plan.page_size if pager else 0
    table._props['pagination'] = pagination
    # The footer stays even on "All": it is the only place to pick a size again.
    table._props['hide-pagination'] = not pager
    if pager:
        table._props['rows-per-page-options'] = list(ROWS_PER_PAGE_OPTIONS)
        table._props['rows-per-page-label'] = 'Rows per page:'

    # Set as a real bool, not via .props('dense=true'): the prop string parser
    # stores 'true', and Quasar reads a non-empty string as truthy either way —
    # so 'dense=false' would render dense.
    table._props['dense'] = plan.density == 'compact'
    _toggle_class(table, 'wiz-table--wrap', plan.wrap)
    _toggle_class(table, 'wiz-table--sized', plan.has_widths)
    table.update()


# Quasar's default column sort, except that a ``9:30 PM`` inside the value is
# compared as ``21:30``. A 12-hour viewer's formatted times otherwise sort
# 10:00 PM before 9:30 AM; 24-hour text already sorts in time order.
CLOCK_SORT = (
    "(a, b) => {"
    " if (typeof a === 'number' && typeof b === 'number') return a - b;"
    " if (typeof a === 'boolean' && typeof b === 'boolean') return a - b;"
    " const k = v => (v === null || v === undefined ? '' : String(v)).toLowerCase().replace("
    "/\\b(\\d{1,2}):(\\d{2})(:\\d{2})?\\s?([ap])m\\b/g,"
    " (_, h, m, s, p) => String(h % 12 + (p === 'p' ? 12 : 0)).padStart(2, '0') + ':' + m + (s || ''));"
    " const x = k(a), y = k(b); return x < y ? -1 : (x > y ? 1 : 0); }"
)


def _with_clock_sort(column: dict) -> dict:
    """Give a sortable column :data:`CLOCK_SORT` unless it brings its own sort."""
    if column.get('sortable') and 'sort' not in column and ':sort' not in column:
        column[':sort'] = CLOCK_SORT
    return column


def _with_header_class(column: Mapping) -> dict:
    """Tag a column's header cell with ``wiz-col-<name>``.

    The resize client reads the column name off this class: Quasar's own header
    cells carry no identifier, and matching by position breaks the moment a
    column is hidden.
    """
    out = dict(column)
    name = _CLASS_SAFE.sub('-', str(out.get('name', '')))
    marker = f'wiz-col-{name}'
    existing = str(out.get('headerClasses') or '')
    if marker not in existing.split():
        out['headerClasses'] = f'{existing} {marker}'.strip()
    return out


def _toggle_class(table: ui.table, name: str, on: bool) -> None:
    if on:
        table.classes(add=name)
    else:
        table.classes(remove=name)


def preferences_button(table: ui.table, *, tooltip: str = 'Table preferences') -> ui.button:
    """The gear that opens this table's Preferences dialog.

    Hidden below the mobile-grid breakpoint by CSS: a gear above a card list,
    offering to reorder columns nobody can see, is worse than no gear at all.
    """
    custom = customization_of(table)
    if custom is None:
        raise ValueError('Call customize_table(...) before rendering its gear.')

    async def _open() -> None:
        from theme.dialog.table_preferences_dialog import open_table_preferences
        await open_table_preferences(table)

    button = ui.button(icon='settings', on_click=_open) \
        .props('flat round dense') \
        .classes(f'wiz-table-prefs-btn {REQUIRES_SOCKET_CLASS}') \
        .tooltip(tooltip)
    with button:
        # "Why does my table look like this" needs an answer on screen.
        custom.badge = ui.badge().props('rounded floating color=primary') \
            .classes('wiz-table-prefs-dot')
        custom.badge.set_visibility(custom.plan.is_customized)
    return button


def search_input(table: ui.table, *,
                 placeholder: str = 'Search…',
                 width: str = 'w-64',
                 search_terms: bool = False) -> ui.input:
    """A text box that filters ``table`` across its visible columns.

    Quasar's ``filter`` prop is the real mechanism, and until now nothing in the
    app set it: twelve columns carried a filterable flag that QTable has never
    read, so "search this board" looked implemented and was not.

    The text is **not** persisted. It is working state, not a preference — a
    board that reopens still filtered to one name is a board that looks empty.
    """
    box = ui.input(placeholder=placeholder).props('dense outlined clearable') \
        .props('debounce=200').classes(f'wiz-table-search {width}')
    with box.add_slot('prepend'):
        ui.icon('search')
    if search_terms:
        from theme.tables.match_run_sheet import run_sheet_terms
        table.bind_filter_from(box, 'value', backward=run_sheet_terms)
    else:
        table.bind_filter_from(box, 'value', backward=lambda v: v or '')
    return box


_SINGULAR = {'matches': 'match', 'people': 'person', 'rows': 'row', 'runs': 'run', 'players': 'player'}


def row_count_label(table: ui.table, noun: str = 'rows') -> ui.label:
    """A live ``124 matches`` caption above a client-paginated board.

    Deliberately *not* the reports' ``Showing 51–100 of 124`` — that one is
    computed server-side because those tables page server-side. These boards hold
    every row and let Quasar page over them, so the range is the pager's to state
    and the only number the server can honestly add is the total.
    """
    return ui.label().classes('text-caption text-grey-7') \
        .bind_text_from(table, 'rows',
                        backward=lambda rows: f'{len(rows or []):,} '
                                              f'{_SINGULAR.get(noun, noun) if len(rows or []) == 1 else noun}')


def sticky_header(table: ui.table, max_height: str = '70vh') -> ui.table:
    """Keep ``table``'s header on screen while its rows scroll.

    Quasar's header sticks to the table's own scroll container, so a table with
    no bounded height has nothing to stick to — the two have to be set together,
    which is what this pairs. Worth it on a long operational board and pure cost
    on a short reference table, so it is opt-in rather than automatic.

    The cap travels as a CSS variable, not an inline ``max-height``, because it
    must not apply in grid mode: below ``md`` a mobile-grid table renders cards in
    ``.q-table__grid-content``, which is not the scroll container, so a capped
    table painted every card past the third over whatever followed it and the
    page ended before them. The stylesheet applies the cap only to
    ``:not(.q-table--grid)``.
    """
    table.classes(add='wiz-table--sticky')
    table.style(f'--wiz-sticky-max-height: {max_height}')
    return table
