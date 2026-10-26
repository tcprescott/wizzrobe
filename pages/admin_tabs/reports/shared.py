"""Shared helpers for admin reports.

Defines the common filter strip (date range + tournament), CSV export
button, URL-state helpers, and a small page-shell wrapper.
"""

import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import AbstractSet, Any, Callable, Iterator, Mapping, NamedTuple, Optional, Sequence, TypeVar

from nicegui import context, ui

from application.services import SystemConfigService, TenantThemeService, TournamentService
from application.utils.timezone import local_day_bounds
from models import Tournament
from pages.admin_tabs.links import REPORTS, SCHEDULE, admin_url
from theme.dialog._helpers import native_date_input
from theme.empty_state import fun_fact
from theme.page_header import page_header
from theme.section import section_panel
from theme.tables.export import csv_export_button
from theme.tables.mobile_grid import enable_mobile_grid

REPORT_KEYS = ('capacity', 'match_ops', 'crew', 'stages', 'audit')

# ECharts colours. Canvas charts can't read CSS var() tokens and are painted
# once server-side, so every colour has to hold >=3:1 on BOTH the light and dark
# card surfaces. The main series is the community's own colour (``chart_palette``);
# these fixed ones carry a meaning, so they stay put whatever the accent is.
CHART_TEAL = '#17A097'     # second series (status-live hue)
CHART_AMBER = '#B5791C'    # "middling" in a score; the second series when the accent is teal
CHART_RED = '#C94E3D'      # thresholds/limits (status-cancelled hue)
CHART_NEUTRAL = '#8D8379'  # absence/idle series (warm gray — reads gray by design)

# Chart chrome. ECharts' default axis/legend grays assume a white canvas and
# go illegible in dark mode; these sit at the equal-contrast point between the
# light and dark surfaces (~4:1 against both), the best a single value can do.
CHART_TEXT = '#877D72'                       # axis labels, axis names, legend
CHART_GRID = 'rgba(135, 125, 114, 0.35)'     # gridlines/axis lines — recessive on both


class ChartPalette(NamedTuple):
    """The series colours one report's charts are painted with."""

    #: The main series, in the community's colour.
    primary: str
    #: ``primary`` at 18%, for the area under a line.
    primary_area: str
    #: ``primary`` at 15%, for the zoom slider's selected window.
    primary_wash: str
    #: The series beside it, never the same hue as ``primary``.
    secondary: str


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def chart_palette_for(accent: str) -> ChartPalette:
    """The palette for a community whose accent is ``accent``. Pure."""
    primary = TenantThemeService.chart_series_colour(accent)
    r, g, b = _rgb(primary)
    # A teal community would draw both series teal; amber stands in there.
    distance = sum((x - y) ** 2 for x, y in zip((r, g, b), _rgb(CHART_TEAL), strict=True)) ** 0.5
    return ChartPalette(
        primary=primary,
        primary_area=f'rgba({r}, {g}, {b}, 0.18)',
        primary_wash=f'rgba({r}, {g}, {b}, 0.15)',
        secondary=CHART_AMBER if distance < 90 else CHART_TEAL,
    )


async def chart_palette() -> ChartPalette:
    """The in-scope community's chart palette. Read once per report body."""
    theme = await TenantThemeService.get_current_theme()
    return chart_palette_for(theme['primary'])


def themed_chart_option(option: dict) -> dict:
    """Overlay the mode-neutral chrome colors onto an ECharts option in place.

    Sets default text, legend text, and per-axis label/name/line/split-line
    colors, plus decal patterns and an aria description, without clobbering
    anything the chart already specifies.
    """
    option.setdefault('textStyle', {}).setdefault('color', CHART_TEXT)
    # Patterned series (decals) so stacked and multi-series charts don't rely on
    # colour alone; aria also gives screen readers a generated description.
    option.setdefault('aria', {'enabled': True, 'decal': {'show': True}})
    if 'legend' in option:
        option['legend'].setdefault('textStyle', {}).setdefault('color', CHART_TEXT)
    if 'toolbox' in option:
        option['toolbox'].setdefault('iconStyle', {}).setdefault('borderColor', CHART_TEXT)
    for key in ('xAxis', 'yAxis'):
        axes = option.get(key)
        if axes is None:
            continue
        for axis in axes if isinstance(axes, list) else [axes]:
            axis.setdefault('axisLabel', {}).setdefault('color', CHART_TEXT)
            axis.setdefault('nameTextStyle', {}).setdefault('color', CHART_TEXT)
            axis.setdefault('axisLine', {}).setdefault('lineStyle', {}).setdefault('color', CHART_GRID)
            axis.setdefault('splitLine', {}).setdefault('lineStyle', {}).setdefault('color', CHART_GRID)
    return option


def reports_url(report: Optional[str] = None, **params) -> str:
    """Build an ``/admin/reports[?report=…&…]`` URL preserving filters.

    The Reports section is a path segment (``/admin/reports``); the report name
    and its filters stay query params, since they are orthogonal report state."""
    return admin_url(REPORTS, report=report, **params)


_MIN_YEAR, _MAX_YEAR = 2000, 2100


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None
    return parsed if _MIN_YEAR <= parsed.year <= _MAX_YEAR else None


def parse_int(value) -> Optional[int]:
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def display_bounds(start_d: date, end_d: date) -> tuple[datetime, datetime]:
    """Convert a picked date range to half-open UTC bounds on the display clock.

    The dates come from the viewer's own date pickers, so the window they mean is
    their local midnight-to-midnight — not UTC's, and not the tenant's.
    """
    if end_d < start_d:
        end_d = start_d
    return local_day_bounds(start_d, end_d)


async def default_date_range(
    start_param: Optional[str],
    end_param: Optional[str],
) -> tuple[date, date]:
    if start_param and end_param:
        s = parse_date(start_param)
        e = parse_date(end_param)
        if s and e:
            return s, e
    return await SystemConfigService.get_event_window()


@contextmanager
def report_page_shell(
    title: str, subtitle: Optional[str] = None, *, all_reports_link: bool = True,
) -> Iterator[None]:
    """A report's page top: an "All reports" link back, then the page header.

    ``all_reports_link=False`` drops the link for a viewer limited to this one
    report, who has no dashboard to go back to.
    """
    with ui.column().classes('page-container-wide wiz-report'):
        if all_reports_link:
            ui.button(
                'All reports', icon='chevron_left',
                on_click=lambda: navigate_with_params(report=None),
            ).props('flat dense no-caps color=primary').classes('wiz-report-back')
        page_header(title, subtitle)
        yield


# Per client (never module state): a filter change re-renders the report body,
# and a phone's filter card should still be open on the far side of it.
_FILTERS_OPEN_ATTR = '_wiz_report_filters_open'


@contextmanager
def report_filter_bar(active: int = 0) -> Iterator[None]:
    """The match boards' filter card, for a report. Build the fields inside.

    Desktop shows one row of outlined fields. Below 1024px the card folds behind
    a Filters button whose badge counts ``active`` (the filters set away from
    their default), and the fields sit two to a row. Pass each field through
    ``filter_field``; ``report_jumps`` adds a row of shortcuts under them.
    """
    client = context.client
    is_open = getattr(client, _FILTERS_OPEN_ATTR, False)
    with ui.row().classes('wiz-filter-toggle full-width items-center'):
        toggle = ui.button('Filters', icon='filter_list').props('flat color=primary')
        badge = ui.badge(str(active)).props('color=primary')
        badge.set_visibility(active > 0)
    card = ui.card().classes('match-filters-card match-filters-card--compact')
    if is_open:
        card.classes(add='wiz-filters-open')
    toggle.props(f'aria-expanded={str(is_open).lower()}')

    def _toggle() -> None:
        now_open = not getattr(client, _FILTERS_OPEN_ATTR, False)
        setattr(client, _FILTERS_OPEN_ATTR, now_open)
        if now_open:
            card.classes(add='wiz-filters-open')
        else:
            card.classes(remove='wiz-filters-open')
        toggle.props(f'aria-expanded={str(now_open).lower()}')

    toggle.on_click(_toggle)
    with card, ui.row().classes('match-filter-row'):
        yield


FilterField = TypeVar('FilterField', bound=ui.element)


def filter_field(element: FilterField) -> FilterField:
    """Style one report filter the way the match boards style theirs."""
    element.classes('match-filter-field').props('outlined dense')
    if isinstance(element, ui.select):
        element.props('options-dense')
    return element


def report_jumps(
    options: Sequence[tuple[str, date, date]],
    start_d: date,
    end_d: date,
    on_pick: Callable[[date, date], None],
) -> None:
    """A segmented row of preset ranges under the filter fields.

    The preset matching the current range shows as selected, so the row also
    says which day you are looking at; a hand-picked range selects none.
    """
    by_label = {text: (s, e) for text, s, e in options}
    current = next((text for text, s, e in options if (s, e) == (start_d, end_d)), None)
    with ui.row().classes('wiz-report-jumps items-center no-wrap'):
        ui.label('Jump to').classes('text-caption')
        # url-state: exempt — reports carry their own filters in the URL (navigate_with_params)
        jumps = ui.toggle(list(by_label), value=current) \
            .props('no-caps dense unelevated aria-label="Jump to"').classes('wiz-segmented')

        def _picked(e) -> None:
            if e.value in by_label:
                on_pick(*by_label[e.value])

        jumps.on_value_change(_picked)


async def event_jump_options() -> list[tuple[str, date, date]]:
    """"Whole event", plus one shortcut per day when the event is a week or less."""
    event_start, event_end = await SystemConfigService.get_event_window()
    options = [('Whole event', event_start, event_end)]
    days = (event_end - event_start).days + 1
    if 1 < days <= 7:
        for offset in range(days):
            day = event_start + timedelta(days=offset)
            options.append((day.strftime('%a %m-%d'), day, day))
    return options


async def is_custom_range(start_d: date, end_d: date) -> bool:
    """Whether the range is not the event window, i.e. counts as a set filter."""
    return (start_d, end_d) != await SystemConfigService.get_event_window()


def date_range_filter(
    default_start: date,
    default_end: date,
    on_change: Callable[[date, date], None],
) -> tuple[ui.input, ui.input]:
    """Render a From/To date pair as two filter fields. Calls ``on_change`` when either changes."""
    state = {'start': default_start, 'end': default_end}

    def _fire():
        on_change(state['start'], state['end'])

    start_input = filter_field(native_date_input('From', default_start.isoformat()))
    end_input = filter_field(native_date_input('To', default_end.isoformat()))

    def _on_start(_e):
        parsed = parse_date(start_input.value)
        if parsed and parsed != state['start']:
            state['start'] = parsed
            _fire()

    def _on_end(_e):
        parsed = parse_date(end_input.value)
        if parsed and parsed != state['end']:
            state['end'] = parsed
            _fire()

    # Typing a date fires a change per finished segment, so a year typed digit by
    # digit passes through 0002, 0020 and 0202. Only the value a pause settles on
    # re-runs the report; parse_date drops the out-of-range years in between.
    start_input.on('change', _on_start, throttle=0.8, leading_events=False)
    end_input.on('change', _on_end, throttle=0.8, leading_events=False)
    return start_input, end_input


async def tournament_filter(
    current_id: Optional[int],
    on_change: Callable[[Optional[int]], None],
    *,
    only: Optional[AbstractSet[int]] = None,
) -> ui.select:
    """``only`` narrows the choices to a viewer's own tournaments."""
    tournaments = await TournamentService().get_all_tournaments()
    options: dict = {0: 'All tournaments' if only is None else 'All my tournaments'}
    options.update(Tournament.short_label_options(
        t for t in tournaments if only is None or t.id in only
    ))
    value = current_id if current_id and current_id in options else 0
    select = filter_field(ui.select(options=options, value=value, label='Tournament'))

    def _on_change(_e):
        v = select.value
        on_change(int(v) if v else None)

    select.on('update:model-value', _on_change)
    return select


def show_navigating() -> None:
    """Acknowledge a click that is about to cost a full page navigation.

    Built as a NiceGUI element rather than ``ui.run_javascript``, which is
    deliberate: the outbox emits element *updates* before *messages* in a batch,
    so an element created here reaches the browser ahead of the ``open`` that
    navigates — a fire-and-forget ``run_javascript`` is deferred to a background
    task and loses that race.

    The overlay dies with the page, which is what is wanted: the next page
    renders fresh. Flagged on the client so a fast double-change is a no-op
    rather than a second overlay (never module state — one module is shared by
    every operator in every tenant).

    Filter changes no longer reach this — they re-render in place (see
    ``navigate_with_params``). It still covers the per-row drill-out, which is a
    real navigation to another admin tab.
    """
    client = context.client
    if getattr(client, '_wiz_report_navigating', False):
        return
    client._wiz_report_navigating = True
    with client.content:
        with ui.element('div').classes('wiz-report-busy'):
            with ui.element('div').classes('wiz-report-busy__box'):
                with ui.row().classes('items-center no-wrap gap-3'):
                    ui.spinner(size='sm')
                    ui.label('Updating report…')
                fun_fact()


# Set per client (never module state — one module serves every operator in every
# tenant) by the Reports dispatcher, which owns the refreshable body.
_REFRESH_ATTR = '_wiz_report_refresh'


def _refresh_params(params: Mapping) -> dict:
    """Normalise filter params to what a real page load would have delivered.

    The refresh path has to agree with the reload path exactly, or a filter
    would mean one thing when clicked and another when the URL is opened. Two
    differences to close: ``admin_url`` drops ``None``/``''`` so the reload sees
    the handler's default, and it renders a ``date`` as ISO — which is what the
    route's ``start: str`` annotation would have produced. Ints are left alone,
    since the annotations (``tournament_id: int``, ``page: int``) coerce them
    and the handlers do arithmetic on them.
    """
    out: dict = {}
    for key, value in params.items():
        if value is None or value == '':
            continue
        out[key] = value.isoformat() if isinstance(value, (date, datetime)) else value
    return out


def bind_report_refresh(refresh: Callable[[Optional[str], dict], None]) -> None:
    """Register this client's in-place report re-render.

    Called once by the Reports dispatcher. Every filter handler in the subsystem
    goes through ``navigate_with_params``, so registering here is what turns all
    of them from a page reload into a refresh, without each report knowing.
    """
    setattr(context.client, _REFRESH_ATTR, refresh)


def navigate_with_params(report: Optional[str] = None, **params) -> None:
    """Apply new filter params to the Reports tab.

    Re-renders the report body in place and rewrites the URL with
    ``history.replaceState``, so the filters stay linkable and bookmarkable
    without the full navigation they used to cost. Falls back to a real
    navigation when no refreshable is bound — a report body rendered outside the
    dispatcher has nothing to refresh.
    """
    refresh = getattr(context.client, _REFRESH_ATTR, None)
    url = reports_url(report=report, **params)
    if refresh is None:
        show_navigating()
        ui.navigate.to(url)
        return
    # ``reports_url`` is root-relative and NiceGUI adds the client prefix when
    # *it* navigates; replaceState is ours, so the /t/<slug> prefix is ours to
    # add. Then hold the operator's scroll position across the rebuild: the body
    # is streamed in, so the document collapses mid-refresh and the browser
    # clamps scrollY unless something puts it back (static/js/report-nav.js).
    ui.run_javascript(
        f'history.replaceState(null, "", (window.path_prefix || "") + {json.dumps(url)});'
        'window.wizReportKeepPlace && window.wizReportKeepPlace();'
    )
    refresh(report, _refresh_params(params))


def parse_details(raw: Optional[str]) -> tuple[Optional[Any], str]:
    """Return ``(parsed_json_or_none, display_text)`` for an audit/telemetry blob.

    Legacy rows store plain-text details — those parse to None and display
    as-is. New rows store JSON and display pretty-printed.
    """
    if not raw:
        return None, ''
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None, raw
    if parsed is None:
        return None, ''
    if isinstance(parsed, (dict, list)):
        return parsed, json.dumps(parsed, indent=2, sort_keys=True)
    return parsed, str(parsed)


def kpi_card(
    title: str,
    value: str,
    subtitle: str,
    color: str = 'primary',
    href: Optional[str] = None,
    href_label: str = 'See the detail →',
) -> None:
    """One KPI tile (title / big value / subtitle). Put them in ``kpi_grid``.

    ``href`` turns the tile into a route to the report that explains the number
    — it must carry the same window the number was computed over, or the
    destination contradicts the tile it came from.
    """
    with ui.card().classes('wiz-kpi q-pa-md'):
        ui.label(title).classes('text-caption text-grey-7')
        ui.label(value).classes(f'text-h4 text-{color}')  # style-guide: exempt — a stat figure, not a title
        ui.label(subtitle).classes('text-caption')
        if href:
            ui.link(href_label, href).classes('text-caption q-mt-xs')


def kpi_grid() -> ui.element:
    """The tile grid a row of ``kpi_card`` tiles sits in."""
    return ui.element('div').classes('wiz-tile-grid wiz-tile-grid--sm')


# Per-row drill-out. Reports identify work; the surface that fixes it lives
# elsewhere, so a row carries an id there rather than growing its own mutation.
DRILL_FIELD = 'drill_url'
DRILL_HINT_FIELD = 'drill_hint'

# Both halves are static Vue: the destination arrives as row *data*
# (``props.row.drill_url``), never as markup templated into the slot. The button
# emits rather than rendering an ``<a href>`` — NiceGUI prepends the client's
# path prefix to a navigate target, so an anchor built in a template would drop
# the ``/t/<slug>`` in path-mode tenancy and 404.
_DRILL_CELL = r'''
        <q-td :props="props" class="text-right" @click.stop>
            <q-btn v-if="props.row.drill_url" flat dense round
                   icon="open_in_new" color="primary"
                   @click.stop="$parent.$emit('drill', props.row)"
                   :aria-label="props.row.drill_hint">
                <q-tooltip>{{ props.row.drill_hint }}</q-tooltip>
            </q-btn>
        </q-td>
'''
# The card mirror. enable_mobile_grid builds its card body from the columns and
# skips the actions column entirely, so a cell slot alone is invisible on a
# phone — this is what makes the control exist there.
_DRILL_ACTION = r'''
            <q-btn v-if="props.row.drill_url" flat dense
                   icon="open_in_new" color="primary"
                   :label="props.row.drill_hint"
                   @click.stop="$parent.$emit('drill', props.row)" />
'''


def enable_drill_link(
    table: ui.table,
    columns: Sequence[Mapping],
    rows: Sequence[dict],
    url_for: Callable[[Mapping], Optional[str]],
    *,
    enabled: bool = True,
    hint: str = 'Open on the schedule board',
) -> str:
    """Add a per-row navigating control to ``table``; return its card mirror.

    Call after building the table and pass the return value as
    ``enable_mobile_grid(..., actions=…)`` — the desktop cell and the card
    footer are one call so they cannot be shipped apart.

    ``url_for`` returns the destination for a row, or ``None`` for a row with
    nowhere to go (its cell stays empty). ``enabled`` is the destination's own
    authorization predicate: when it is false the column is not rendered at all
    rather than rendered disabled, because a control that is present and
    refuses is worse than one that was never offered.

    The caller's ``columns`` list is left untouched, so a CSV export built from
    it does not grow a URL column.
    """
    if not enabled:
        return ''

    for row in rows:
        row[DRILL_FIELD] = url_for(row) or ''
        row[DRILL_HINT_FIELD] = hint

    table.columns = [
        *columns,
        {'name': 'drill', 'label': '', 'field': DRILL_FIELD, 'align': 'right'},
    ]
    # Re-assign, don't rely on the mutation above reaching the client: NiceGUI
    # copies rows into ObservableDicts when the table is built, so the caller's
    # dicts and ``table.rows`` are different objects from that point on.
    table.rows = rows
    table.add_slot('body-cell-drill', _DRILL_CELL)
    table.on('drill', _follow_drill)
    return _DRILL_ACTION


def schedule_drill(
    board_scope: Optional[frozenset[int]],
) -> tuple[Callable[[Mapping], Optional[str]], bool]:
    """``(url_for, enabled)`` for a match row's "Open on the schedule board".

    ``board_scope`` is :meth:`AuthService.schedule_board_scope`. A tournament
    admin's board shows only the tournaments they run, so a row from any other
    tournament gets no link rather than one that opens a board without it.
    """
    def url_for(row: Mapping) -> Optional[str]:
        if board_scope is not None and row.get('tournament_id') not in board_scope:
            return None
        return admin_url(SCHEDULE, match_id=row['match_id'])

    return url_for, board_scope is None or bool(board_scope)


def _follow_drill(e) -> None:
    url = clicked_row(e).get(DRILL_FIELD)
    if url:
        # Same acknowledgment a filter change gets: this is a full navigation
        # to another admin tab, not an in-page update.
        show_navigating()
        ui.navigate.to(url)


def clicked_row(e) -> dict:
    """Extract the row dict from a NiceGUI table ``row-click`` event.

    Body-slot templates emit ``$event, props.row`` (args ``[evt, row]``); a bare
    table emits the row directly. Return ``{}`` when neither yields a dict.
    """
    args = e.args
    row = args[1] if isinstance(args, list) and len(args) > 1 else args
    return row if isinstance(row, dict) else {}


# Expandable-details ``body`` slot for event-log tables. The ``details`` column
# renders a collapsible pretty-printed JSON cell; every other column is plain.
# Two variants: one emits ``row-click`` for a drill-down filter, one does not.
_EVENT_LOG_DETAILS_CELL = r'''
        <q-td v-for="col in props.cols" :key="col.name" :props="props">
            <template v-if="col.name !== 'details'">
                {{ col.value }}
            </template>
            <div v-else @click.stop>
                <q-expansion-item
                    v-if="props.row.full_details && props.row.full_details.length > 0"
                    dense dense-toggle switch-toggle-side
                    :label="props.row.details"
                    class="text-body2"
                >
                    <pre class="q-mt-xs wiz-json-block">{{ props.row.full_details }}</pre>
                </q-expansion-item>
                <span v-else class="text-grey-7">—</span>
            </div>
        </q-td>
'''
_EVENT_LOG_BODY_ROWCLICK = (
    r'''<q-tr :props="props" @click="$parent.$emit('row-click', $event, props.row)" style="cursor: pointer">'''
    + _EVENT_LOG_DETAILS_CELL + '</q-tr>'
)
_EVENT_LOG_BODY_PLAIN = r'<q-tr :props="props">' + _EVENT_LOG_DETAILS_CELL + '</q-tr>'


def _page_range_label(total: int, page: int, page_size: int, noun: str) -> str:
    """``Showing 51–100 of 124 entries`` — or just ``124 entries`` on one page.

    The header used to be handed a pre-formatted count, so it read ``124
    entries`` above a table paginated to 50. Both numbers were true and together
    they were misleading: what you are reading is a page, and nothing said so.
    """
    if total <= page_size:
        return f'{total:,} {noun}'
    first = (page - 1) * page_size + 1
    if first > total:
        # A hand-edited ``?page=`` past the end. Saying "Showing 101–95" would
        # be worse than saying nothing about the range.
        return f'{total:,} {noun} — page {page} is past the end'
    last = min(page * page_size, total)
    return f'Showing {first:,}–{last:,} of {total:,} {noun}'


async def paginated_event_log(
    *,
    title: str,
    icon: str,
    columns: Sequence[Mapping],
    rows: Sequence[Mapping],
    row_key: str,
    total: int,
    page: int,
    page_size: int,
    on_page: Callable[[int], None],
    csv_filename_prefix: str,
    count_noun: str,
    note: str,
    on_row_click: Optional[Callable[[dict], None]] = None,
    table_key: Optional[str] = None,
) -> None:
    """Server-paginated event-log panel (count + CSV + expandable table + pager).

    Shared by the Audit Log and Engagement Telemetry reports. ``on_row_click``,
    when given, wires a per-row drill-down filter (receives the clicked row
    dict); ``on_page`` reloads the page for a new 1-based page number.

    The whole-``body`` slot below is customization-safe despite appearances: it
    renders ``<q-td v-for="col in props.cols">``, and ``props.cols`` is Quasar's
    own visible-columns-in-order list, so hiding and reordering both land. Only
    the ``details`` cell is special-cased, and it is matched by name.
    """
    panel = await section_panel(
        title, icon=icon, subtitle=_page_range_label(total, page, page_size, count_noun),
    )
    with panel.aside:
        csv_export_button(csv_filename_prefix, lambda: columns, lambda: rows)
    with panel.body:
        table = ui.table(columns=columns, rows=rows, row_key=row_key).classes('full-width')
        table.add_slot('body', _EVENT_LOG_BODY_ROWCLICK if on_row_click else _EVENT_LOG_BODY_PLAIN)
        if on_row_click is not None:
            table.on('row-click', lambda e: on_row_click(clicked_row(e)))
        # pager=False: this table pages on the server with the buttons below.
        enable_mobile_grid(table, columns, table_key=table_key,
                           row_click_event='row-click' if on_row_click else None,
                           pager=False)
        ui.label(note).classes('italic-note')

        total_pages = max(1, (total + page_size - 1) // page_size)
        with ui.row().classes('items-center q-mt-sm'):
            ui.label(f'Page {page} of {total_pages}').classes('text-caption')
            ui.button(
                'Previous', icon='chevron_left',
                on_click=lambda: on_page(page - 1),
            ).props('flat dense').set_enabled(page > 1)
            ui.button(
                'Next', icon='chevron_right',
                on_click=lambda: on_page(page + 1),
            ).props('flat dense').set_enabled(page < total_pages)
