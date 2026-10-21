"""Exports report: tournament data downloads, and everything you've exported lately.

The tournament picker builds a participants + matches export in the background
(``DataExportService``); the file downloads here once the worker finishes, and
stays under "Your exports" for three days. Staff see every tournament; a
tournament admin sees the ones they run. Anyone else who reaches this page (a
volunteer coordinator) only gets "Your exports", which is where their volunteer
data ZIPs land.
"""

from typing import Any

from nicegui import app, background_tasks, context, ui

from application.services import DataExportService, get_user_from_session
from theme.exports import download_export, export_row, watch_until_ready
from theme.menu import SlotItem, menu_action, slot_more_menu, slot_row_button
from theme.notify import notify_error
from theme.section import section_panel
from theme.steady import steady_refreshable
from theme.tables.mobile_grid import MOBILE_GRID_BREAKPOINT, apply_column_visibility, enable_mobile_grid
from theme.tables.preferences import TableKeys, customize_table

from .shared import report_page_shell

TITLE = 'Exports'
SUBTITLE = "Download tournament results, and anything you've exported in the last 3 days."

_SITE_LABEL = {'wizzrobe': 'Wizzrobe', 'challonge': 'Challonge', 'startgg': 'start.gg', None: 'None'}

_SITE_CHIP = '<span class="wiz-chip wiz-chip--neutral">{{ props.row.bracket }}</span>'
_STATUS_CHIP = (
    '<span class="wiz-chip" :class="props.row.status_cls">{{ props.row.status }}</span>'
    '<div v-if="props.row.error" class="text-caption st-cancelled q-mt-xs">{{ props.row.error }}</div>'
)
# The phone card is one line of name and one of numbers, as in the picked
# mockup; the generic column-per-row card ran 16 tournaments to six screens.
_TOURNAMENT_CARD = (
    '<div class="q-pa-xs col-xs-12 col-sm-6">'
    '<q-card bordered flat class="q-pa-sm wiz-grid-card">'
    '<div class="row no-wrap items-start q-gutter-x-sm">'
    '<q-checkbox dense v-model="props.selected" :aria-label="\'Select \' + props.row.name_label" />'
    '<div class="col"><div class="text-weight-medium">{{ props.row.name_label }}</div>'
    '<div class="text-caption text-grey-7">{{ props.row.summary }}</div></div>'
    '</div></q-card></div>'
)
_EXPORT_TITLE = (
    '<div>{{ props.row.title }}</div>'
    '<div class="text-caption text-grey-7">{{ props.row.kind }}'
    '<span v-if="props.row.size"> · {{ props.row.size }}</span></div>'
)
_EXPORT_ACTIONS = (
    '<div class="row no-wrap items-center justify-end">'
    + slot_row_button('Download', 'download', 'download', v_if='props.row.ready')
    + slot_more_menu('props.row.title', [
        SlotItem('data_object', 'Download JSON', 'download_json', v_if='props.row.has_json'),
    ]).replace('<q-btn ', '<q-btn v-if="props.row.ready && props.row.has_json" ', 1)
    + '</div>'
)


async def exports_page(all_reports_link: bool = True, **_unused: Any) -> None:
    service = DataExportService()
    viewer = await get_user_from_session(app.storage.user)
    if viewer is None:
        return

    with report_page_shell(TITLE, SUBTITLE, all_reports_link=all_reports_link):
        overview = await service.tournament_overview(viewer)
        picker = ui.column().classes('full-width gap-0')
        refresh_list = await _your_exports(viewer)
        if overview:
            with picker:
                await _tournament_picker(viewer, overview, refresh_list)


async def _your_exports(viewer):
    """Render "Your exports"; returns the call that refreshes it."""
    service = DataExportService()
    panel = await section_panel('Your exports', icon='history', user=viewer)
    client = context.client

    @steady_refreshable
    async def body() -> None:
        exports = await service.list_mine(viewer)
        rows = [export_row(e) for e in exports]
        if not rows:
            ui.label('Nothing exported in the last 3 days.').classes('italic-note')
            return
        columns = [
            {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
            {'name': 'title', 'label': 'Export', 'field': 'title', 'align': 'left'},
            {'name': 'requested', 'label': 'Requested', 'field': 'requested', 'align': 'left'},
            {'name': 'status', 'label': 'Status', 'field': 'status', 'align': 'left'},
            {'name': 'expires', 'label': 'Deleted after', 'field': 'expires', 'align': 'left'},
            {'name': 'actions', 'label': '', 'field': 'actions'},
        ]
        table = ui.table(columns=columns, rows=rows, row_key='id').classes('full-width wiz-table')
        table.add_slot('body-cell-title', f'<q-td :props="props">{_EXPORT_TITLE}</q-td>')
        table.add_slot('body-cell-status', f'<q-td :props="props">{_STATUS_CHIP}</q-td>')
        table.add_slot('body-cell-actions', f'<q-td :props="props" class="text-right">{_EXPORT_ACTIONS}</q-td>')
        enable_mobile_grid(
            table, columns, actions=_EXPORT_ACTIONS,
            field_slots={'title': _EXPORT_TITLE, 'status': _STATUS_CHIP},
            table_key=TableKeys.REPORTS_EXPORTS, wrap=True,
        )
        table.on('download', lambda e: background_tasks.create(_download(e.args, 'file', client)))
        table.on('download_json', lambda e: background_tasks.create(_download(e.args, 'json', client)))
        poll.active = any(r['pending'] for r in rows)

    async def _download(row: dict, fmt: str, client) -> None:
        with client:
            await download_export(viewer, row['id'], fmt)

    with panel.body:
        poll = ui.timer(3.0, lambda: body.refresh(), active=False)
        await body()
    return body.refresh


async def _tournament_picker(viewer, overview: list[dict], refresh_list) -> None:
    rows = [
        {
            **r,
            'bracket': _SITE_LABEL.get(r['bracket_site'], 'None'),
            'name_label': r['name'] + (' (test)' if r['test'] else '') + ('' if r['active'] else ' (inactive)'),
            'summary': ' · '.join([
                f"{_SITE_LABEL.get(r['bracket_site'])} bracket" if r['bracket_site'] else 'No bracket',
                f"{r['players']} enrolled",
                f"{r['matches']} matches, {r['finished']} finished",
            ]),
        }
        for r in overview
    ]
    panel = await section_panel(
        'Tournaments', icon='emoji_events',
        subtitle='Each tournament gets participants.csv and matches.csv; the JSON holds all of them. '
                 'Names, Discord usernames and Discord ids only.',
        user=viewer,
    )
    with panel.body:
        with ui.row().classes('items-center full-width no-wrap q-mb-sm gap-2'):
            count = ui.label().classes('text-caption')
            ui.space()
            zip_button = ui.button('Download ZIP', icon='download').props('color=primary no-caps')
            with ui.button('Actions').props('flat no-caps color=primary icon-right=expand_more'):
                with ui.menu().props('anchor="bottom right" self="top right"'):
                    json_item = menu_action('data_object', 'Download JSON only', lambda: request('json'))
                    menu_action('help_outline', 'About the export files',
                                lambda: ui.navigate.to('/help/staff-exports', new_tab=True))
                    menu_action('select_all', 'Select all', lambda: select(rows))
                    menu_action('deselect', 'Clear selection', lambda: select([]))

        columns = [
            {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
            {'name': 'name_label', 'label': 'Tournament', 'field': 'name_label', 'sortable': True, 'align': 'left'},
            {'name': 'enrolled', 'label': 'Enrolled', 'field': 'players', 'sortable': True, 'align': 'left'},
            {'name': 'matches', 'label': 'Matches', 'field': 'matches', 'sortable': True, 'align': 'left'},
            {'name': 'finished', 'label': 'Finished', 'field': 'finished', 'sortable': True, 'align': 'left'},
            {'name': 'bracket', 'label': 'Bracket', 'field': 'bracket', 'sortable': True, 'align': 'left'},
        ]
        table = ui.table(
            columns=columns, rows=rows, row_key='id', selection='multiple',
        ).classes('full-width wiz-table')
        table.add_slot('body-cell-bracket', f'<q-td :props="props">{_SITE_CHIP}</q-td>')
        table.add_slot('header-selection',
                       '<q-checkbox v-model="props.selected" dense aria-label="Select all tournaments" />')
        table.add_slot('body-selection',
                       '<q-checkbox v-model="props.selected" dense '
                       ':aria-label="\'Select \' + props.row.name_label" />')
        table.props(f':grid="Quasar.Screen.{MOBILE_GRID_BREAKPOINT}"')
        apply_column_visibility(table, columns)
        table.add_slot('item', _TOURNAMENT_CARD)
        customize_table(table, columns, key=TableKeys.REPORTS_EXPORT_TOURNAMENTS)
        table.selected = list(rows)

    def select(chosen: list[dict]) -> None:
        table.selected = list(chosen)
        table.update()
        sync()

    def sync() -> None:
        n = len(table.selected)
        count.set_text(f'{n} of {len(rows)} selected')
        zip_button.set_enabled(n > 0)
        json_item.set_enabled(n > 0)

    table.on_select(lambda _e: sync())
    sync()

    async def request(fmt: str) -> None:
        ids = [r['id'] for r in table.selected]
        try:
            export = await DataExportService().request_tournaments(
                viewer, None if len(ids) == len(rows) else ids,
            )
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify(
            f'{export.title}: building now. It downloads here when ready, '
            'and stays under Your exports for 3 days.',
            color='info',
        )
        with panel.body:
            watch_until_ready(viewer, export.id, fmt, on_change=lambda _e: refresh_list())
        refresh_list()

    zip_button.on_click(lambda: request('file'))
