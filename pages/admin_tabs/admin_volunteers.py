"""Admin Volunteers management page (coordinator schedule grid)."""

from datetime import timedelta
from typing import List, Optional

from nicegui import app, ui

from application.services import AuthService, SystemConfigService, get_user_from_session
from application.services.volunteer.volunteer_arrival_service import describe_late
from application.services.volunteer.volunteer_autoschedule_service import VolunteerAutoscheduleService
from application.services.volunteer.volunteer_position_service import VolunteerPositionService
from application.services.volunteer.volunteer_schedule_service import VolunteerScheduleService
from application.utils.tenant_session import tenant_session_get, tenant_session_set
from application.utils.timezone import (
    format_local_time,
    format_local_time_range,
    parse_local_datetime,
    timezone_label,
)
from models import VolunteerAssignment
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.volunteer_assign_dialog import VolunteerAssignDialog
from theme.dialog.volunteer_autofill_dialog import VolunteerAutofillDialog
from theme.dialog.volunteer_export_dialog import VolunteerExportDialog
from theme.dialog.volunteer_position_dialog import VolunteerPositionDialog
from theme.dialog.volunteer_shift_dialog import VolunteerShiftDialog
from theme.empty_state import empty_panel
from theme.help import help_icon
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.page_header import page_header
from theme.steady import steady_refreshable
from theme.url_state import iso_day, url_param, url_state
from theme.volunteer_grid import day_label

# Standard four 4-hour shift blocks, in the community's own timezone (the
# generator resolves them against it), matching the 2025 schedule.
STANDARD_BLOCKS = [
    ('Shift 1', '08:00', '12:00'),
    ('Shift 2', '12:00', '16:00'),
    ('Shift 3', '16:00', '20:00'),
    ('Shift 4', '20:00', '00:00'),
]

# Namespaced like the match board's filter keys, and tenant-scoped, so a
# coordinator working two communities does not carry day 3 of one into the other.
_DAY_KEY = 'volunteers:day'


def stored_or_default_day(
    stored: Optional[str], day_options: List[str], fallback: str,
) -> str:
    """The day to open on. A stored day from a previous event is not a day of this one."""
    return stored if stored in day_options else fallback


async def admin_volunteers_page(day: str | None = None) -> None:
    actor = await get_user_from_session(app.storage.user)
    if not await AuthService.can_manage_volunteers(actor):
        ui.label('You do not have permission to manage volunteers.').classes('text-error')
        return

    position_service = VolunteerPositionService()
    schedule_service = VolunteerScheduleService()
    autoschedule_service = VolunteerAutoscheduleService()

    event_start, event_end = await SystemConfigService.get_event_window()
    day_options = []
    d = event_start
    while d <= event_end:
        day_options.append(d.isoformat())
        d += timedelta(days=1)
    # Per-page state, never module level: NiceGUI shares one process across users.
    # ``last_run`` holds the most recent auto-fill result for the summary panel;
    # the durable record of a run is its audit row, not this.
    #
    # A deep link (``?day=``) is a deliberate destination, so it outranks the
    # remembered day — but only when it names a day this event actually has; an
    # out-of-window or malformed date falls through to the stored one rather
    # than opening an empty grid for a day that isn't real. Read from the
    # address keeper rather than ``day``, which is the request's and so stale
    # once the viewer has switched tabs.
    day = url_state().requested('day')
    state = {
        'day': stored_or_default_day(
            day if day in day_options else tenant_session_get(_DAY_KEY),
            day_options,
            day_options[0] if day_options else event_start.isoformat(),
        ),
        'last_run': None,
        # Per-position "I opened this fully-staffed one anyway", keyed by id.
        'folds': {},
    }

    def _day_window(day_str: str):
        start = parse_local_datetime(day_str, '00:00')
        return start, start + timedelta(hours=30)

    with ui.column().classes('page-container-wide'):
        with page_header(
            'Volunteer Shifts',
            'Build the volunteer shift grid: create positions and shifts, auto-fill '
            'from availability, and assign volunteers per shift.',
        ):
            await help_icon('vol-schedule', 'vol-autofill', 'vol-drafts', user=actor)

        # --- Coverage strip ----------------------------------------------
        # The one number a coordinator wants on-site — how much of today is
        # uncovered — used to require scrolling 5.8 screenfuls and adding up.
        strip_container = ui.row().classes('full-width')

        def refresh_all() -> None:
            """Every handler on this page changes the grid, the strip, the draft
            count or the panel's staleness — so they all refresh together."""
            coverage_strip.refresh()
            draft_banner.refresh()
            result_panel.refresh()
            grid.refresh()

        @steady_refreshable
        async def coverage_strip() -> None:
            win_start, win_end = _day_window(state['day'])
            summary = await schedule_service.day_summary(win_start, win_end)
            if not summary['shifts']:
                ui.label('No shifts on this day yet.').classes('text-caption text-grey')
                return
            with ui.row().classes('items-center gap-2').style('flex-wrap: wrap;'):
                ui.label(f"{summary['filled']}/{summary['needed']} slots filled").classes('wiz-chip wiz-chip--neutral')
                if summary['open']:
                    ui.label(f"{summary['open']} open").classes('wiz-chip wiz-chip--pending')
                else:
                    ui.label('Fully staffed').classes('wiz-chip wiz-chip--ok')
                if summary['unacknowledged']:
                    ui.label(f"{summary['unacknowledged']} awaiting acknowledgment").classes('wiz-chip wiz-chip--neutral')
                if summary['drafts']:
                    ui.label(f"{summary['drafts']} draft").classes('wiz-chip wiz-chip--live')

        # --- Controls ----------------------------------------------------
        with ui.card().classes('full-width q-pa-md'):
            with ui.row().classes('full-width items-center no-wrap gap-3'):
                # Shrinks below 12rem so the menu stays on the same line at 360px.
                day_select = ui.select(
                    day_options, value=state['day'], label='Event day',
                ).classes('col').style('max-width: 12rem; min-width: 0')

                def on_day_change(e):
                    if e.value == state['day']:
                        return
                    state['day'] = e.value
                    tenant_session_set(_DAY_KEY, e.value)
                    refresh_all()
                    day_param.set(e.value)

                def restore_day(value: str) -> None:
                    state['day'] = value if value in day_options else day_options[0] if day_options else state['day']
                    day_select.set_value(state['day'])
                    refresh_all()
                day_select.on_value_change(on_day_change)
                day_param = url_param(
                    'day', day_select, default='', value=state['day'],
                    label=lambda v: day_label(d) if (d := iso_day(v)) else None, on_restore=restore_day,
                )

                ui.space()
                with ui.button('Actions').props('flat color=primary icon-right=expand_more'):
                    with ui.menu():
                        menu_action('smart_toy', 'Auto-fill from availability', lambda: auto_fill())
                        menu_action('badge', 'Manage positions', lambda: open_positions_dialog())
                        menu_action('download', 'Export data', lambda: VolunteerExportDialog().open())
                        ui.separator()
                        menu_action('delete_forever', 'Reset all volunteer data',
                                     lambda: open_reset_dialog(), negative=True)

        # --- Draft banner ------------------------------------------------
        banner_container = ui.column().classes('full-width')

        @steady_refreshable
        async def draft_banner() -> None:
            win_start, win_end = _day_window(state['day'])
            pending = await schedule_service.count_drafts(win_start, win_end)
            if not pending:
                return
            with ui.card().classes('full-width q-pa-sm wiz-draft-banner'):
                with ui.row().classes('items-center gap-2 full-width').style('flex-wrap: wrap;'):
                    ui.icon('edit_note', color='secondary')
                    ui.label(
                        f'{pending} draft assignment(s) on this day. '
                        'The volunteers have not been told yet.'
                    ).classes('text-body2')
                    ui.space()
                    ui.button('Clear draft', icon='clear_all',
                              on_click=lambda: confirm_clear_draft(pending)).props('flat color=negative')
                    ui.button('Publish draft', icon='send',
                              on_click=lambda: confirm_publish(pending)) \
                        .props('color=positive')

        # --- Last auto-fill result ---------------------------------------
        panel_container = ui.column().classes('full-width')

        @steady_refreshable
        async def result_panel() -> None:
            run = state['last_run']
            if not run:
                return
            policy = run.get('policy')
            open_slots = sum(u['open'] for u in run['unfilled'])
            header = (f"Draft: {run['created']} assigned · {open_slots} slot(s) open "
                      f"· pool of {run['pool_size']}")
            if policy is not None and policy.max_hours:
                header += f" · max {policy.max_hours:g} h/volunteer"

            def dismiss() -> None:
                state['last_run'] = None
                result_panel.refresh()

            with ui.card().classes('full-width q-pa-sm'):
                with ui.row().classes('items-center gap-2 full-width').style('flex-wrap: wrap;'):
                    ui.label(header).classes('text-body2 text-weight-medium')
                    ui.space()
                    ui.button(icon='close', on_click=dismiss) \
                        .props('flat dense round').tooltip('Dismiss this summary')

                if run['unfilled']:
                    ui.label('Open slots').classes('text-caption text-weight-medium q-mt-sm')
                    for row in run['unfilled']:
                        with ui.row().classes('items-center gap-2 full-width') \
                                .style('flex-wrap: wrap;'):
                            ui.label(_slot_title(row)).classes('text-caption')
                            ui.badge(f"{row['open']} open", color='warning')
                            ui.label(row['reason']).classes('text-caption text-grey')
                            ui.space()
                            ui.button('Assign',
                                      on_click=lambda r=row: assign_from_panel(r['shift_id'])) \
                                .props('flat dense color=primary')

                if run.get('outside_availability'):
                    ui.label('Placed outside stated availability') \
                        .classes('text-caption text-weight-medium q-mt-sm')
                    for row in run['outside_availability']:
                        with ui.row().classes('items-center gap-2 full-width') \
                                .style('flex-wrap: wrap;'):
                            ui.label(
                                f"{row['name']} — {_slot_title(row)}"
                            ).classes('text-caption')
                            ui.space()
                            ui.button('Remove',
                                      on_click=lambda r=row: confirm_remove_from_panel(r)) \
                                .props('flat dense color=negative')

                if run.get('heavy_loads'):
                    ui.label('Heavy loads').classes('text-caption text-weight-medium q-mt-sm')
                    for row in run['heavy_loads']:
                        ui.label(
                            f"{row['name']} — {row['hours']:g} h across "
                            f"{row['shifts']} shift(s)"
                        ).classes('text-caption text-grey')

        def _slot_title(row) -> str:
            label = f" — {row['label']}" if row.get('label') else ''
            return (f"{row['position']}{label}   "
                    f"{format_local_time(row['starts_at'])} {timezone_label(at=row['starts_at'])}")

        async def assign_from_panel(shift_id: int) -> None:
            shift = await schedule_service.get_shift(shift_id)
            if shift is None:
                ui.notify('That shift no longer exists.', color='warning')
                return
            await open_assign_dialog(shift)

        def confirm_remove_from_panel(row) -> None:
            ConfirmationDialog(
                message=f"Take {row['name']} off {_slot_title(row).strip()}?",
                on_confirm=lambda: remove_from_panel(row), confirm_text='Remove',
                title='Remove from shift?',
            ).open()

        async def remove_from_panel(row) -> None:
            assignment = await schedule_service.find_assignment(
                row['shift_id'], row['user_id'],
            )
            if assignment is None:
                ui.notify('That assignment is already gone.', color='info')
            else:
                try:
                    await schedule_service.unassign(actor, assignment)
                    ui.notify(f"Removed {row['name']}.", color='info')
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
            state['last_run']['outside_availability'] = [
                r for r in state['last_run']['outside_availability']
                if not (r['shift_id'] == row['shift_id'] and r['user_id'] == row['user_id'])
            ]
            refresh_all()

        # --- Grid --------------------------------------------------------
        grid_container = ui.column().classes('full-width')

        @steady_refreshable
        async def grid() -> None:
            win_start, win_end = _day_window(state['day'])
            positions = await position_service.list_active()
            if not positions:
                empty_panel('No active positions yet. Use "Manage positions" to add one.',
                            icon='badge')
                return

            shifts = await schedule_service.list_shifts_for_window(win_start, win_end)
            by_position: dict = {}
            for shift in shifts:
                by_position.setdefault(shift.position_id, []).append(shift)

            for position in positions:
                pos_shifts = sorted(
                    by_position.get(position.id, []), key=lambda s: s.starts_at,
                )
                open_slots = sum(
                    max(0, s.slots_needed - len(s.assignments)) for s in pos_shifts
                )
                filled = sum(len(s.assignments) for s in pos_shifts)
                needed = sum(s.slots_needed for s in pos_shifts)
                with ui.card().classes('full-width q-pa-sm q-mb-sm'):
                    # The header always states the position's state, so a collapsed
                    # position hides finished work and never hidden work. The action
                    # buttons stay outside the expansion: they are how an empty day
                    # gets populated.
                    with ui.row().classes('items-center justify-between full-width'):
                        with ui.column().classes('gap-0'):
                            with ui.row().classes('items-center gap-2'):
                                ui.label(position.name).classes('text-subtitle1')
                                if pos_shifts:
                                    ui.badge(f'{filled}/{needed}',
                                             color='warning' if open_slots else 'positive')
                                    if open_slots:
                                        ui.label(f'{open_slots} open') \
                                            .classes('text-caption text-warning')
                            if position.description:
                                ui.label(position.description).classes('text-caption text-grey wiz-free-text')
                        with ui.row().classes('gap-1'):
                            ui.button('Add shift', icon='add',
                                      on_click=lambda p=position: open_shift_dialog(position=p)) \
                                .props('flat dense color=primary')
                            with more_menu(position.name):
                                menu_action('auto_awesome_motion', 'Generate standard shifts',
                                            lambda p=position: generate_shifts(p))
                    if not pos_shifts:
                        ui.label('No shifts for this day yet.').classes('italic-note q-pa-sm')
                        continue
                    # A fully-staffed position folds away — on phones only, where
                    # 5.8 screenfuls of finished work is the finding. The fold is a
                    # class our own stylesheet only honours below `md`, so the
                    # cards render once and the desktop layout is provably
                    # untouched; a `ui.expansion` per width would double the DOM
                    # and register every row action twice.
                    folded = not open_slots and not state['folds'].get(position.id)
                    if not open_slots:
                        with ui.row().classes('items-center lt-md q-pt-xs'):
                            ui.button(
                                f"{'Show' if folded else 'Hide'} {len(pos_shifts)} shift(s)",
                                icon='expand_more' if folded else 'expand_less',
                                on_click=lambda p=position: toggle_fold(p.id),
                            ).props('flat dense color=primary')
                    classes = 'gap-2 items-stretch'
                    if folded:
                        classes += ' wiz-position-folded'
                    with ui.row().classes(classes).style('flex-wrap: wrap;'):
                        for shift in pos_shifts:
                            _render_shift_card(shift)

        def toggle_fold(position_id: int) -> None:
            state['folds'][position_id] = not state['folds'].get(position_id)
            grid.refresh()

        def _last_run_reason(shift_id: int):
            """Why the last auto-fill left this shift short, so the grid and the
            summary panel agree without the coordinator holding the mapping."""
            run = state['last_run']
            if not run:
                return None
            return next((u['reason'] for u in run['unfilled']
                         if u['shift_id'] == shift_id), None)

        def _render_shift_card(shift) -> None:
            filled = len(shift.assignments)
            understaffed = filled < shift.slots_needed
            with ui.card().classes('q-pa-sm wiz-shift-card'):
                header = shift.label or f'{format_local_time(shift.starts_at)}'
                with ui.row().classes('items-center justify-between full-width'):
                    ui.label(header).classes('text-weight-medium')
                    badge = ui.badge(f'{filled}/{shift.slots_needed}',
                                     color='warning' if understaffed else 'positive')
                    reason = _last_run_reason(shift.id)
                    if understaffed and reason:
                        badge.tooltip(reason)
                    if shift.notes:
                        # The notes field is write-only otherwise; the volunteer
                        # reads it on their card, the coordinator could not.
                        ui.icon('sticky_note_2', size='xs').tooltip(shift.notes)
                ui.label(
                    format_local_time_range(shift.starts_at, shift.ends_at)
                ).classes('text-caption')
                for assignment in shift.assignments:
                    _render_assignment_chip(shift, assignment)
                with ui.row().classes('items-center gap-1'):
                    ui.button('Assign', icon='person_add',
                              on_click=lambda s=shift: open_assign_dialog(s)).props('flat dense color=primary')
                    ui.space()
                    with more_menu(header):
                        menu_action('edit', 'Edit shift', lambda s=shift: open_shift_dialog(shift=s))
                        ui.separator()
                        menu_action('delete', 'Delete shift', lambda s=shift: confirm_delete_shift(s),
                                    negative=True)

        def _render_assignment_chip(shift, assignment: VolunteerAssignment) -> None:
            name = assignment.user.preferred_name if assignment.user else 'Unknown'
            with ui.row().classes('items-center gap-1 no-wrap'):
                if assignment.auto_generated:
                    state, tone = 'Draft', 'draft'
                elif assignment.acknowledged_at:
                    state, tone = 'Acknowledged', 'ok'
                else:
                    state, tone = 'Waiting', 'waiting'
                chip = ui.chip('', removable=True, color=None).props('dense') \
                    .classes('wiz-chip wiz-chip--neutral wiz-assign-chip')
                with chip:
                    ui.label(name).classes('wiz-assign-chip__name')
                    ui.label(f'· {state}').classes(f'wiz-assign-chip__state wiz-assign-chip__state--{tone}')
                if assignment.auto_generated:
                    chip.tooltip('Draft — this volunteer has not been told')

                async def remove(a=assignment) -> None:
                    try:
                        await schedule_service.unassign(actor, a)
                        ui.notify('Removed assignment.', color='info')
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                    finally:
                        refresh_all()

                def ask_remove(e, a=assignment, chip=chip) -> None:
                    if e.value:
                        return
                    # Quasar hides a removable chip the moment its ✕ is tapped, so
                    # put it back until the confirm is answered.
                    chip.value = True
                    told = '' if a.auto_generated else (
                        '\n\nThey’ve already been told about this shift, so they’ll get a DM '
                        'saying it’s off.'
                    )
                    ConfirmationDialog(
                        message=f'Take {name} off the {format_local_time_range(shift.starts_at, shift.ends_at)} '
                                f'shift?{told}',
                        on_confirm=lambda: remove(a), confirm_text='Remove',
                        title='Remove from shift?',
                    ).open()
                chip.on_value_change(ask_remove)

                if assignment.checked_in_at:
                    who = ('they said they were here'
                           if assignment.checked_in_by_id == assignment.user_id else 'by a coordinator')  # type: ignore[attr-defined]
                    ui.icon('how_to_reg', color='teal', size='sm') \
                        .tooltip(f'Checked in {format_local_time(assignment.checked_in_at)}, {who}')
                else:
                    late = describe_late(assignment)
                    if late:
                        ui.icon('schedule', size='sm').classes('st-pending').tooltip(late)
                    async def do_check_in(a_id=assignment.id) -> None:
                        try:
                            await schedule_service.check_in(a_id, actor)
                            ui.notify('Volunteer checked in.', color='positive')
                        except (ValueError, PermissionError) as e:
                            notify_error(e)
                        refresh_all()
                    ui.button(icon='login', on_click=do_check_in) \
                        .props('flat dense color=teal').tooltip('Check in volunteer')

        # --- Assignment picker ------------------------------------------
        async def open_assign_dialog(shift) -> None:
            async def do_assign(u) -> bool:
                try:
                    _, warnings = await schedule_service.assign(actor, shift, u)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return False
                for w in warnings:
                    ui.notify(w, color='warning')
                ui.notify(f'Assigned {u.preferred_name}.', color='positive')
                refresh_all()
                return True
            await VolunteerAssignDialog(shift, do_assign).open()

        # --- Actions -----------------------------------------------------
        async def generate_shifts(position) -> None:
            try:
                await schedule_service.generate_day_shifts(
                    actor, state['day'], [position.id], STANDARD_BLOCKS,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify(f'Generated shifts for {position.name} (staggered where configured).',
                      color='positive')
            refresh_all()

        async def open_shift_dialog(shift=None, position=None) -> None:
            async def after(_):
                refresh_all()
            await VolunteerShiftDialog(
                shift=shift, position=position, default_day=state['day'], on_submit=after,
            ).open()

        async def confirm_delete_shift(shift) -> None:
            assigned = len(shift.assignments)
            message = 'Delete this shift?'
            if assigned:
                message = (f'This shift has {assigned} assigned volunteer(s). '
                           'Deleting it removes those assignments. Continue?')

            async def on_confirm() -> None:
                try:
                    await schedule_service.delete_shift(actor, shift)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Shift deleted.', color='info')
                refresh_all()

            confirm = ConfirmationDialog(message=message, on_confirm=on_confirm, confirm_text='Delete')
            confirm.open()

        async def auto_fill() -> None:
            positions = await position_service.list_active()
            await VolunteerAutofillDialog(positions, on_submit=run_auto_fill).open()

        async def run_auto_fill(policy, position_ids) -> None:
            win_start, win_end = _day_window(state['day'])
            try:
                result = await autoschedule_service.generate_draft(
                    actor, win_start, win_end,
                    position_ids=position_ids, policy=policy,
                )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            state['last_run'] = result
            ui.notify(
                'Draft created — see the summary below.',
                color='positive' if result['created'] else 'warning',
            )
            refresh_all()

        def confirm_clear_draft(pending: int) -> None:
            async def clear_draft() -> None:
                win_start, win_end = _day_window(state['day'])
                try:
                    removed = await autoschedule_service.clear_draft(actor, win_start, win_end)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify(f'Cleared {removed} draft assignment(s).', color='info')
                refresh_all()

            ConfirmationDialog(
                message=f'Clear all {pending} draft assignment(s) on this day? '
                        'Nobody has been told about them, but you’ll have to run auto-fill '
                        'or assign by hand again.',
                on_confirm=clear_draft, confirm_text='Clear draft', title='Clear draft?',
            ).open()

        async def confirm_publish(pending: int) -> None:
            async def on_confirm() -> None:
                win_start, win_end = _day_window(state['day'])
                try:
                    result = await autoschedule_service.publish_draft(actor, win_start, win_end)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify(
                    f"Published {result['published']} assignment(s) to "
                    f"{result['volunteers']} volunteer(s). Each got a Discord DM.",
                    color='positive',
                )
                clashes = result.get('clashes') or []
                if clashes:
                    ui.notify(
                        f"{len(clashes)} draft(s) left unpublished because they clash: "
                        + ' '.join(clashes),
                        color='warning', multi_line=True, timeout=0, close_button=True,
                    )
                refresh_all()

            confirm = ConfirmationDialog(
                message=(f'Publish {pending} draft assignment(s)? Each volunteer gets a '
                         'Discord DM asking them to acknowledge. This cannot be undone — '
                         'removing an assignment afterwards notifies them again.'),
                on_confirm=on_confirm, confirm_text='Publish & notify', busy_text='Publishing…',
            )
            confirm.open()

        def open_reset_dialog() -> None:
            async def do_reset() -> None:
                try:
                    deleted = await schedule_service.reset_all_shifts(actor)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify(f'Deleted {deleted} shift(s) and all assignments.', color='negative')
                refresh_all()

            ConfirmationDialog(
                'This permanently deletes every shift and assignment across every day. '
                'There is no undo.',
                on_confirm=do_reset, confirm_text='Delete everything',
                title='Reset all volunteer data?',
                require_phrase='yes please delete all shifts',
            ).open()

        # --- Positions manager ------------------------------------------
        async def open_positions_dialog() -> None:
            with form_dialog('Volunteer Positions') as dialog:

                @steady_refreshable
                async def position_list() -> None:
                    positions = await position_service.list_all()
                    if not positions:
                        ui.label('No positions yet.').classes('italic-note q-pa-sm')
                    for position in positions:
                        with ui.row().classes('items-center justify-between full-width q-px-sm'):
                            label = position.name + ('' if position.is_active else ' (inactive)')
                            ui.label(label)
                            async def edit(p=position) -> None:
                                async def after(_):
                                    position_list.refresh()
                                    refresh_all()
                                await VolunteerPositionDialog(position=p, on_submit=after).open()
                            ui.button(icon='edit', on_click=edit).props('flat dense')

                with ui.column().classes('q-pa-sm gap-1').style('max-height: 50vh; overflow-y: auto;'):
                    await position_list()

                async def add_position() -> None:
                    async def after(_):
                        position_list.refresh()
                        refresh_all()
                    await VolunteerPositionDialog(on_submit=after).open()

                with dialog_actions().classes('justify-end'):
                    ui.button('Add position', icon='add', on_click=add_position).props('color=primary')
                dialog.open()

        with strip_container:
            await coverage_strip()
        with banner_container:
            await draft_banner()
        with panel_container:
            await result_panel()
        with grid_container:
            await grid()
