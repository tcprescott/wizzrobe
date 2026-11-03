"""Staff / Crew Activity report.

Coverage-by-match table plus contribution-by-person table.
"""

from typing import Any, Optional

from nicegui import app, ui

from application.services import AuthService, EventService, ReportsService, get_user_from_session
from application.utils.timezone import format_local_display
from theme.section import section_panel
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import TableKeys

from .shared import (
    clicked_row,
    csv_export_button,
    date_range_filter,
    default_date_range,
    display_bounds,
    enable_drill_link,
    event_jump_options,
    filter_field,
    is_custom_range,
    navigate_with_params,
    parse_int,
    report_filter_bar,
    report_jumps,
    report_page_shell,
    schedule_drill,
    tournament_filter,
)

APPROVAL_OPTIONS = ['All', 'Approved only', 'Pending only']


async def crew_page(
    start: Optional[str] = None,
    end: Optional[str] = None,
    tournament_id: Optional[int] = None,
    user_id: Optional[int] = None,
    approval: Optional[str] = None,
    **_unused,
) -> None:
    start_d, end_d = await default_date_range(start, end)
    selected_approval = approval if approval in APPROVAL_OPTIONS else 'All'
    user_id_int = parse_int(user_id)
    viewer = await get_user_from_session(app.storage.user)
    board_drill, can_open_board = schedule_drill(await AuthService.schedule_board_scope(viewer))

    def nav(**overrides) -> None:
        params: dict[str, Any] = {
            'report': 'crew',
            'start': start_d, 'end': end_d,
            'tournament_id': tournament_id,
            'user_id': user_id_int,
            'approval': None if selected_approval == 'All' else selected_approval,
        }
        params.update(overrides)
        navigate_with_params(**params)

    active = (
        int(await is_custom_range(start_d, end_d)) + int(tournament_id is not None)
        + int(selected_approval != 'All') + int(user_id_int is not None)
    )
    with report_page_shell(
        'Staff / Crew Activity',
        'Commentator, tracker and broadcaster coverage for each match, and who covered it.',
    ):
        with report_filter_bar(active):
            date_range_filter(start_d, end_d, on_change=lambda s, e: nav(start=s, end=e))
            await tournament_filter(tournament_id, on_change=lambda t_id: nav(tournament_id=t_id),
                                    only=await AuthService.report_tournament_scope(viewer))
            approval_select = filter_field(ui.select(
                APPROVAL_OPTIONS,
                value=selected_approval,
                label='Approval',
            ))
            approval_select.on(
                'update:model-value',
                lambda _e: nav(approval=None if approval_select.value == 'All' else approval_select.value),
            )
            if user_id_int is not None:
                ui.button(
                    'Clear person', icon='close',
                    on_click=lambda: nav(user_id=None),
                ).props('flat dense no-caps color=primary')
            report_jumps(await event_jump_options(), start_d, end_d,
                         on_pick=lambda s, e: nav(start=s, end=e))

        bounds_start, bounds_end = display_bounds(start_d, end_d)
        data = await ReportsService().crew_coverage(
            bounds_start, bounds_end,
            tournament_id=tournament_id,
            user_id=user_id_int, actor=viewer,
        )

        broadcast = await EventService.current_event_broadcasters() or any(
            r.get('broadcasters_total') for r in data['coverage_rows'])

        await _crewed_by_tournament_panel(data['tournament_rows'], start_d, end_d, broadcast)

        coverage_rows = data['coverage_rows']
        if selected_approval == 'Approved only':
            coverage_rows = [
                r for r in coverage_rows
                if r['commentators_approved'] > 0 or r['trackers_approved'] > 0
                or r.get('broadcasters_approved', 0) > 0
            ]
        elif selected_approval == 'Pending only':
            coverage_rows = [
                r for r in coverage_rows
                if r['commentators_total'] > r['commentators_approved']
                or r['trackers_total'] > r['trackers_approved']
                or r.get('broadcasters_total', 0) > r.get('broadcasters_approved', 0)
            ]

        cov_rows_display = [
            {
                'match_id': r['match_id'],
                'tournament_id': r['tournament_id'],
                'tournament': r['tournament_name'],
                'scheduled_at': format_local_display(r['scheduled_at']),
                'stage': r['stage'],
                'stream_candidate': 'yes' if r['is_stream_candidate'] else '',
                'commentators': (
                    f"{r['commentators_approved']}/{r['commentators_total']}"
                    f" · need {r['commentators_required']}"
                ),
                'trackers': (
                    f"{r['trackers_approved']}/{r['trackers_total']}"
                    f" · need {r['trackers_required']}"
                ),
                'broadcasters': (
                    f"{r.get('broadcasters_approved', 0)}/{r.get('broadcasters_total', 0)}"
                    f" · need {r.get('broadcasters_required', 0)}"
                ),
                'gap': 'GAP' if r['coverage_gap'] else '',
            }
            for r in coverage_rows
        ]
        cov_columns = [
            {'name': 'match_id', 'label': 'Match', 'field': 'match_id', 'sortable': True},
            {'name': 'tournament', 'label': 'Tournament', 'field': 'tournament', 'sortable': True},
            {'name': 'scheduled_at', 'label': 'Scheduled', 'field': 'scheduled_at', 'sortable': True},
            {'name': 'stage', 'label': 'Stage', 'field': 'stage', 'sortable': True},
            {'name': 'stream_candidate', 'label': 'Candidate', 'field': 'stream_candidate'},
            {'name': 'commentators', 'label': 'Comms (apprv/total)', 'field': 'commentators'},
            {'name': 'trackers', 'label': 'Trackers (apprv/total)', 'field': 'trackers'},
            *([{'name': 'broadcasters', 'label': 'Broadcasters (apprv/total)', 'field': 'broadcasters',
                'hidden': True}] if broadcast else []),
            {'name': 'gap', 'label': 'Coverage gap', 'field': 'gap'},
        ]
        cov_panel = await section_panel('Coverage by match', icon='headset_mic')
        with cov_panel.aside:
            csv_export_button(
                f'crew-coverage-{start_d}-to-{end_d}',
                lambda: cov_columns,
                lambda: cov_rows_display,
            )
        with cov_panel.body:
            cov_table = ui.table(
                columns=cov_columns,
                rows=cov_rows_display,
                pagination=25,
                row_key='match_id',
            ).classes('full-width')
            drill_actions = enable_drill_link(
                cov_table, cov_columns, cov_rows_display,
                board_drill,
                enabled=can_open_board,
                hint='Open on the schedule board',
            )
            enable_mobile_grid(cov_table, cov_columns, actions=drill_actions,
                               table_key=TableKeys.REPORTS_CREW_COVERAGE)

        contrib_display = [
            {
                **r,
                'commentary_pct': _ratio_pct(r['commentator_approved'], r['commentator_total']),
                'tracker_pct': _ratio_pct(r['tracker_approved'], r['tracker_total']),
                'broadcaster_pct': _ratio_pct(r.get('broadcaster_approved', 0), r.get('broadcaster_total', 0)),
            }
            for r in data['contribution_rows']
        ]
        contrib_columns = [
            {'name': 'name', 'label': 'Name', 'field': 'name', 'sortable': True},
            {'name': 'commentator_total', 'label': 'Commentary signups', 'field': 'commentator_total', 'sortable': True},
            {'name': 'commentator_approved', 'label': 'Commentary approved', 'field': 'commentator_approved', 'sortable': True},
            {'name': 'commentary_pct', 'label': 'Commentary %', 'field': 'commentary_pct', 'sortable': True},
            {'name': 'tracker_total', 'label': 'Tracker signups', 'field': 'tracker_total', 'sortable': True},
            {'name': 'tracker_approved', 'label': 'Tracker approved', 'field': 'tracker_approved', 'sortable': True},
            {'name': 'tracker_pct', 'label': 'Tracker %', 'field': 'tracker_pct', 'sortable': True},
            *([
                {'name': 'broadcaster_total', 'label': 'Broadcaster signups', 'field': 'broadcaster_total', 'sortable': True, 'hidden': True},
                {'name': 'broadcaster_approved', 'label': 'Broadcaster approved', 'field': 'broadcaster_approved', 'sortable': True, 'hidden': True},
                {'name': 'broadcaster_pct', 'label': 'Broadcaster %', 'field': 'broadcaster_pct', 'sortable': True, 'hidden': True},
            ] if broadcast else []),
            {'name': 'hours_covered', 'label': 'Hours covered', 'field': 'hours_covered', 'sortable': True},
        ]
        contrib_panel = await section_panel(
            'Contribution by person', icon='groups',
            subtitle='' if user_id_int is not None else 'Pick a person to filter both tables to them.',
        )
        with contrib_panel.aside:
            csv_export_button(
                f'crew-contribution-{start_d}-to-{end_d}',
                lambda: contrib_columns,
                lambda: contrib_display,
            )

        def _row_clicked(e):
            clicked_uid = clicked_row(e).get('user_id')
            if clicked_uid:
                nav(user_id=clicked_uid)

        with contrib_panel.body:
            contrib_table = ui.table(
                columns=contrib_columns,
                rows=contrib_display,
                pagination=25,
                row_key='user_id',
            ).classes('full-width wiz-rowclick')
            contrib_table.on('row-click', _row_clicked)
            enable_mobile_grid(contrib_table, contrib_columns, row_click_event='row-click',
                               table_key=TableKeys.REPORTS_CREW_CONTRIBUTIONS)


_ROLE_COLUMNS = (('commentary', 'Commentary', 'comms'), ('tracker', 'Tracker', 'tracker'),
                 ('broadcast', 'Broadcast', 'broadcast'))


def _roles(broadcast: bool) -> tuple:
    return _ROLE_COLUMNS if broadcast else _ROLE_COLUMNS[:2]


def crewed_columns(broadcast: bool) -> list[dict]:
    return [
        {'name': 'name', 'label': 'Name', 'field': 'name', 'align': 'left'},
        *({'name': key, 'label': label, 'field': key, 'align': 'right'} for key, label, _ in _roles(broadcast)),
        {'name': 'total', 'label': 'Total', 'field': 'total', 'align': 'right'},
    ]


_COUNT = "{{ props.row.%s || '–' }}"


def _crewed_body(broadcast: bool) -> str:
    """A heading row per tournament that folds the people under it. The rows are
    rebuilt server-side on a fold, so the slot only has to tell the two apart."""
    counts = ''.join(f'<q-td key="{key}" :props="props">{_COUNT % key}</q-td>' for key, _, _ in _roles(broadcast))
    return (
        '<q-tr v-if="props.row.group" :props="props" class="wiz-crewed__group">'
        '<q-td key="name" :props="props">'
        '<q-btn flat dense round size="sm" :icon="props.row.open ? \'expand_less\' : \'expand_more\'"'
        ' :aria-expanded="props.row.open ? \'true\' : \'false\'"'
        ' :aria-label="(props.row.open ? \'Fold \' : \'Unfold \') + props.row.name"'
        ' @click="$parent.$emit(\'toggle_group\', props.row)" />'
        '<span class="wiz-crewed__group-name">{{ props.row.name }}</span>'
        '<span class="wiz-crewed__group-meta"> · {{ props.row.people }}'
        " {{ props.row.people === 1 ? 'person' : 'people' }}</span>"
        '</q-td>'
        + counts
        + '<q-td key="total" :props="props">{{ props.row.total }}</q-td>'
        '</q-tr>'
        '<q-tr v-else :props="props">'
        '<q-td key="name" :props="props" class="wiz-crewed__person">{{ props.row.name }}</q-td>'
        + counts
        + '<q-td key="total" :props="props" class="wiz-crewed__total">{{ props.row.total }}</q-td>'
        '</q-tr>'
    )


def _crewed_item(broadcast: bool) -> str:
    group = ' · '.join(f'{{{{ props.row.{key} }}}} {word}' for key, _, word in _roles(broadcast))
    person = ' · '.join(f'<b>{{{{ props.row.{key} }}}}</b> {word}' for key, _, word in _roles(broadcast))
    return (
        '<div class="col-12">'
        '<div v-if="props.row.group" class="wiz-crewed__card wiz-crewed__card--group row items-center no-wrap">'
        '<div class="col">'
        '<div class="wiz-crewed__group-name">{{ props.row.name }}</div>'
        '<div class="wiz-crewed__group-meta">{{ props.row.people }}'
        " {{ props.row.people === 1 ? 'person' : 'people' }}"
        f' · {group}</div>'
        '</div>'
        '<q-btn flat dense round :icon="props.row.open ? \'expand_less\' : \'expand_more\'"'
        ' :aria-expanded="props.row.open ? \'true\' : \'false\'"'
        ' :aria-label="(props.row.open ? \'Fold \' : \'Unfold \') + props.row.name"'
        ' @click="$parent.$emit(\'toggle_group\', props.row)" />'
        '</div>'
        '<div v-else class="wiz-crewed__card row items-baseline no-wrap">'
        '<div class="col wiz-crewed__person">{{ props.row.name }}</div>'
        f'<div class="wiz-crewed__counts">{person}</div>'
        '</div>'
        '</div>'
    )


async def _crewed_by_tournament_panel(tournaments: list[dict], start_d, end_d, broadcast: bool) -> None:
    """Matches crewed per person, grouped under a foldable heading per tournament."""
    collapsed: set[int] = set()

    def grouped_rows() -> list[dict]:
        rows: list[dict] = []
        for t in tournaments:
            tid = t['tournament_id']
            is_open = tid not in collapsed
            rows.append({
                'key': f't{tid}', 'group': True, 'open': is_open,
                'tournament_id': tid, 'name': t['tournament_name'],
                'people': len(t['people']),
                'commentary': t['commentary'], 'tracker': t['tracker'],
                'broadcast': t.get('broadcast', 0),
                'total': t['commentary'] + t['tracker'] + t.get('broadcast', 0),
            })
            if is_open:
                rows.extend(
                    {**p, 'key': f't{tid}-u{p["user_id"]}', 'group': False}
                    for p in t['people']
                )
        return rows

    def csv_rows() -> list[dict]:
        return [
            {'tournament': t['tournament_name'], **p}
            for t in tournaments for p in t['people']
        ]

    panel = await section_panel(
        'Crew by tournament', icon='emoji_events',
        subtitle='Approved crew on finished matches that went to a stage.',
    )
    with panel.aside:
        csv_export_button(
            f'crew-by-tournament-{start_d}-to-{end_d}',
            lambda: [{'name': 'tournament', 'label': 'Tournament', 'field': 'tournament'},
                     *crewed_columns(broadcast)],
            csv_rows,
        )
    with panel.body:
        if not tournaments:
            ui.label('Nobody has crewed a finished match on a stage in this window yet.').classes('italic-note')
            return
        # mobile-grid: the bespoke item slot below renders the same groups as cards.
        # table-prefs: exempt — grouped rows; hiding, reordering or paging columns would split a tournament from its people
        table = ui.table(
            columns=crewed_columns(broadcast),
            rows=grouped_rows(),
            row_key='key',
            pagination=0,
        ).classes('full-width wiz-crewed').props(
            ':grid="Quasar.Screen.lt.md" hide-pagination flat'
        )
        table.add_slot('body', _crewed_body(broadcast))
        table.add_slot('item', _crewed_item(broadcast))

        def toggle(e) -> None:
            tid = (e.args or {}).get('tournament_id')
            if tid is None:
                return
            collapsed.symmetric_difference_update({tid})
            table.rows = grouped_rows()
            table.update()

        table.on('toggle_group', toggle)


def _ratio_pct(num: int, denom: int) -> Optional[float]:
    if not denom:
        return None
    return round(num / denom * 100, 1)
