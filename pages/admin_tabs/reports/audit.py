"""Audit Log Viewer report.

Server-side paginated, filterable view of the AuditLog table: a list of entries
in plain language beside a detail panel on desktop, with the same detail opening
as a sheet below 1024px.
"""

from dataclasses import dataclass
from typing import Any, Optional

from nicegui import app, ui

from application.services import AuditService, AuthService, get_user_from_session
from application.utils.audit_display import AREAS, AuditDescription, area_by_key, describe
from application.utils.timezone import format_local_datetime, format_local_time, timezone_label
from pages.admin_tabs.links import SCHEDULE, admin_url
from theme.dialog._helpers import form_dialog
from theme.section import section_panel
from theme.tables.export import csv_export_button

from .shared import (
    _page_range_label,
    date_range_filter,
    default_date_range,
    display_bounds,
    event_jump_options,
    filter_field,
    is_custom_range,
    navigate_with_params,
    parse_details,
    parse_int,
    report_filter_bar,
    report_jumps,
    report_page_shell,
)

SUBTITLE = 'Who changed what, and when.'

PAGE_SIZE = 50

# Keep in step with .wiz-audit__detail in static/css/styles.css.
_PANEL_MIN_WIDTH = 1024


@dataclass
class _Entry:
    log_id: int
    actor_id: Optional[int]
    actor_name: str
    when_time: str
    when_day: str
    when_full: str
    action: str
    view: AuditDescription


def _who(log) -> str:
    return log.user.preferred_name if log.user else f'Member #{log.user_id}'


async def audit_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    category: Optional[str] = None,
    page: Optional[int] = None,
    **_unused,
) -> None:
    actor = await get_user_from_session(app.storage.user)
    if actor is None or not await AuthService.is_staff(actor):
        with report_page_shell('Audit Log', SUBTITLE):
            ui.label('Only Staff can view the audit log.').classes('text-error')
        return
    start_d, end_d = await default_date_range(start, end)
    user_id_int = parse_int(user_id)
    page_int = max(1, parse_int(page) or 1)
    # `action` carries the search text so old bookmarks still work.
    search = (action or '').strip()
    area_key = category if area_by_key(category) else None

    def nav(**overrides) -> None:
        params: dict[str, Any] = {
            'report': 'audit',
            'start': start_d, 'end': end_d,
            'user_id': user_id_int,
            'category': area_key,
            'action': search or None,
        }
        params.update(overrides)
        navigate_with_params(**params)

    active = (
        int(await is_custom_range(start_d, end_d)) + int(bool(search))
        + int(area_key is not None) + int(user_id_int is not None)
    )
    service = AuditService()
    bounds_start, bounds_end = display_bounds(start_d, end_d)
    query: dict[str, Any] = dict(
        actor=actor, start=bounds_start, end=bounds_end,
        user_id=user_id_int, area=area_key, search=search or None,
    )
    total = await service.count_logs(**query)
    logs = await service.list_logs(**query, limit=PAGE_SIZE, offset=(page_int - 1) * PAGE_SIZE)
    parsed = [parse_details(log.details)[0] for log in logs]
    names = await service.reference_names(actor=actor, details=parsed)
    entries = [
        _Entry(
            log_id=log.id,
            actor_id=log.user_id,
            actor_name=_who(log),
            when_time=format_local_time(log.created_at),
            when_day=format_local_datetime(log.created_at, '%a %d %b'),
            when_full=format_local_datetime(log.created_at, '%a %d %b %Y, %H:%M:%S %Z'),
            action=log.action,
            view=describe(log.action, details if details is not None else log.details, names,
                          actor_id=log.user_id),
        )
        for log, details in zip(logs, parsed, strict=True)
    ]
    person_name = next((e.actor_name for e in entries if e.actor_id == user_id_int), None)

    with report_page_shell('Audit Log', SUBTITLE):
        with report_filter_bar(active):
            date_range_filter(start_d, end_d, on_change=lambda s, e: nav(start=s, end=e, page=None))
            area_select = filter_field(ui.select(
                {'': 'All areas', **{a.key: a.label for a in AREAS}},
                label='Area', value=area_key or '',
                on_change=lambda e: nav(category=e.value or None, page=None),
            ))
            area_select.classes('wiz-audit__area')
            search_input = filter_field(ui.input('Search', value=search,
                                                 placeholder='e.g. stage, rescheduled, pronouns'))

            def _on_search(_e):
                nav(action=(search_input.value or '').strip() or None, page=None)
            search_input.on('blur', _on_search)
            search_input.on('keydown.enter', _on_search)

            if user_id_int is not None:
                ui.button(
                    f'Only {person_name}' if person_name else 'Clear person', icon='close',
                    on_click=lambda: nav(user_id=None, page=None),
                ).props('flat dense no-caps color=primary').tooltip('Show everyone again')
            report_jumps(await event_jump_options(), start_d, end_d,
                         on_pick=lambda s, e: nav(start=s, end=e, page=None))

        panel = await section_panel(
            'Entries', icon='fact_check',
            subtitle=_page_range_label(total, page_int, PAGE_SIZE, 'entries'),
        )
        csv_columns = [
            {'name': 'when', 'label': f'When ({timezone_label()})', 'field': 'when'},
            {'name': 'who', 'label': 'Who', 'field': 'who'},
            {'name': 'area', 'label': 'Area', 'field': 'area'},
            {'name': 'what', 'label': 'What happened', 'field': 'what'},
            {'name': 'summary', 'label': 'Summary', 'field': 'summary'},
            {'name': 'action', 'label': 'Action', 'field': 'action'},
            {'name': 'details', 'label': 'Details', 'field': 'details'},
        ]
        csv_rows = [
            {
                'when': e.when_full, 'who': e.actor_name, 'area': e.view.area or '',
                'what': e.view.label, 'summary': e.view.summary, 'action': e.action,
                'details': e.view.raw,
            }
            for e in entries
        ]
        with panel.aside:
            csv_export_button(
                f'audit-log-page-{page_int}-{start_d}-to-{end_d}',
                lambda: csv_columns, lambda: csv_rows,
            )
        with panel.body:
            if not entries:
                ui.label('Nothing matches these filters.').classes('text-secondary q-pa-md')
                return
            await _render_entries(entries, nav)
            _pager(total, page_int, nav)


async def _render_entries(entries: list[_Entry], nav) -> None:
    by_id = {e.log_id: e for e in entries}
    rows: dict[int, ui.element] = {}
    state = {'selected': entries[0].log_id}

    def detail_body(entry: _Entry) -> None:
        _render_detail(entry, nav)

    @ui.refreshable
    def side_panel() -> None:
        detail_body(by_id[state['selected']])

    async def select(log_id: int) -> None:
        rows[state['selected']].classes(remove='is-selected')
        state['selected'] = log_id
        rows[log_id].classes(add='is-selected')
        side_panel.refresh()
        narrow = await ui.run_javascript(f'window.innerWidth < {_PANEL_MIN_WIDTH}')
        if narrow:
            entry = by_id[log_id]
            dialog_host.clear()
            with dialog_host, form_dialog(entry.view.label, card_classes='dialog-card wiz-audit__sheet') as dialog:
                with ui.column().classes('wiz-audit__detail-body'):
                    detail_body(entry)
            dialog.open()

    dialog_host = ui.element('div')
    with ui.element('div').classes('wiz-audit'):
        with ui.element('div').classes('wiz-audit__list').props('role=list'):
            day = None
            for entry in entries:
                if entry.when_day != day:
                    day = entry.when_day
                    ui.label(day).classes('wiz-audit__day').props('role=presentation')
                row = ui.element('button').classes('wiz-audit__row').props(
                    f'type=button role=listitem aria-label="{_aria(entry)}"'
                )
                if entry.log_id == state['selected']:
                    row.classes('is-selected')
                rows[entry.log_id] = row
                row.on('click', lambda _e, i=entry.log_id: select(i))
                with row:
                    ui.label(entry.when_time).classes('wiz-audit__time')
                    with ui.element('div').classes('wiz-audit__main'):
                        ui.label(entry.view.label).classes('wiz-audit__label')
                        with ui.element('div').classes('wiz-audit__sub'):
                            ui.label(entry.actor_name).classes('wiz-audit__who')
                            if entry.view.summary:
                                ui.label(entry.view.summary).classes('wiz-audit__summary')
                    if entry.view.area:
                        ui.label(entry.view.area).classes('wiz-chip wiz-chip--neutral wiz-audit__area-chip')
        with ui.element('div').classes('wiz-audit__detail'):
            side_panel()


def _aria(entry: _Entry) -> str:
    text = f'{entry.when_time}, {entry.actor_name}: {entry.view.label}'
    if entry.view.summary:
        text += f'. {entry.view.summary}'
    return text.replace('"', "'")


def _render_detail(entry: _Entry, nav) -> None:
    view = entry.view
    with ui.column().classes('wiz-audit__panel'):
        with ui.element('div').classes('wiz-audit__head'):
            if view.area:
                ui.label(view.area).classes('wiz-chip wiz-chip--neutral')
            heading = ui.label(view.label).classes('wiz-audit__title')
            heading.tag = 'h2'
        with ui.element('dl').classes('wiz-audit__kv'):
            _kv_row('Who', lambda: ui.button(
                entry.actor_name,
                on_click=lambda: nav(user_id=entry.actor_id, page=None) if entry.actor_id else None,
            ).props('flat dense no-caps color=primary').classes('wiz-audit__link')
                .tooltip(f'Show only what {entry.actor_name} did'))
            _kv_row('When', lambda: ui.label(entry.when_full))
            if view.subject:
                _kv_row('About', lambda: ui.label(view.subject))

        if view.changes or view.changed_fields:
            with ui.element('div').classes('wiz-audit__changes'):
                ui.label('What changed').classes('wiz-audit__block-title')
                if view.changes:
                    with ui.element('div').classes('wiz-audit__diff').props('role=table'):
                        with ui.element('div').classes('wiz-audit__diff-row wiz-audit__diff-head') \
                                .props('role=row'):
                            for text in ('Field', 'Before', 'After'):
                                ui.label(text).props('role=columnheader')
                        for change in view.changes:
                            with ui.element('div').classes('wiz-audit__diff-row').props('role=row'):
                                ui.label(change.field).props('role=cell')
                                if change.before is None:
                                    ui.label('—').classes('wiz-audit__before').props(
                                        'role=cell aria-label="Not recorded"')
                                else:
                                    ui.label(change.before).classes('wiz-audit__before').props('role=cell')
                                ui.label(change.after or '').classes('wiz-audit__after').props('role=cell')
                else:
                    ui.label('Changed ' + ', '.join(view.changed_fields) + '.')

        if view.facts:
            with ui.element('dl').classes('wiz-audit__kv'):
                for label, value in view.facts:
                    _kv_row(label, lambda v=value: ui.label(v))

        with ui.row().classes('wiz-audit__links items-center'):
            if view.match_id is not None:
                ui.button(
                    'Open the match', icon='open_in_new',
                    on_click=lambda: ui.navigate.to(admin_url(SCHEDULE, match_id=view.match_id)),
                ).props('flat dense no-caps color=primary')
            if entry.actor_id:
                ui.button(
                    f'More by {entry.actor_name}', icon='person_search',
                    on_click=lambda: nav(user_id=entry.actor_id, page=None),
                ).props('flat dense no-caps color=primary')

        if view.raw:
            with ui.expansion('Raw data').classes('wiz-audit__raw-toggle').props('dense'):
                raw = ui.label(view.raw).classes('wiz-audit__raw').props('tabindex=0')
                raw.tag = 'pre'


def _kv_row(label: str, render_value) -> None:
    term = ui.label(label).classes('wiz-audit__k')
    term.tag = 'dt'
    with ui.element('dd').classes('wiz-audit__v'):
        render_value()


def _pager(total: int, page: int, nav) -> None:
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    with ui.row().classes('items-center q-mt-sm'):
        ui.label(f'Page {page} of {total_pages}').classes('text-caption')
        ui.button(
            'Previous', icon='chevron_left', on_click=lambda: nav(page=page - 1),
        ).props('flat dense').set_enabled(page > 1)
        ui.button(
            'Next', icon='chevron_right', on_click=lambda: nav(page=page + 1),
        ).props('flat dense').set_enabled(page < total_pages)
