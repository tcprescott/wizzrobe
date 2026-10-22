"""The volunteer day grid: one column per role on an hour scale.

Drawn by Volunteer → Full Schedule (read-only, for everyone on the schedule)
and Admin → Shift Board (staff, where a shift opens its editing sheet and an
empty hour adds one). The layout itself comes from
``application.services.volunteer.volunteer_schedule_grid``; this module only
draws it.
"""

import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple, Union

from nicegui import ui

from application.services import TablePreferenceService
from application.services.volunteer import (
    DayGrid,
    GridPerson,
    GridShift,
    arrange_roles,
    roles_config,
)
from application.services.volunteer.volunteer_schedule_grid import ARRIVED, LATE, NOT_HERE
from application.timezone_context import current_timezone_name
from application.utils.discord_messages_volunteer import late_label
from application.utils.timezone import format_local_time
from theme.dialog._helpers import dialog_actions, dialog_more_menu, form_dialog
from theme.menu import menu_action

# One grid hour, in px. Mirrored by --wiz-vgrid-hour in styles.css; the page
# needs it to work out how many names fit in a block.
HOUR_PX = 48
_LINE_PX = 18
_BLOCK_CHROME_PX = 32

# How a person's arrival reads on the Shift Board: icon, class, words.
ARRIVAL_MARKS = {
    ARRIVED: ('check_circle', 'st-ok', 'Checked in'),
    LATE: ('schedule', 'st-pending', 'Running late'),
    NOT_HERE: ('radio_button_unchecked', 'st-neutral', 'Not here yet'),
}


def event_days(start: date, end: date) -> List[date]:
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def opening_day(days: List[date], stored: Optional[str], today: date) -> Optional[date]:
    """Remembered day, else today during the event, else its first day."""
    if not days:
        return None
    by_iso = {d.isoformat(): d for d in days}
    if stored in by_iso:
        return by_iso[stored]
    return today if today in days else days[0]


def day_label(d: date) -> str:
    return f"{d.strftime('%a')} {d.day}"


def names_that_fit(shift: GridShift, reserved_lines: int = 0) -> int:
    """How many names a block shows before "+N more" takes the last line.

    ``reserved_lines`` are lines the block needs for something else (the
    Shift Board's "N open" chip).
    """
    height = (shift.end_hour - shift.start_hour) * HOUR_PX
    lines = max(1, math.floor((height - _BLOCK_CHROME_PX) / _LINE_PX) - reserved_lines)
    if len(shift.people) <= lines:
        return len(shift.people)
    return max(0, lines - 1)


def person_label(name: str, is_viewer: bool) -> str:
    return f'{name} (you)' if is_viewer else name


def shift_range(shift: GridShift) -> str:
    return f'{format_local_time(shift.starts_at)}–{format_local_time(shift.ends_at)}'


def filled_label(shift: GridShift) -> str:
    filled = len(shift.people)
    text = f'{filled} of {shift.slots_needed} filled'
    return f'{text} · {shift.open_slots} open' if shift.open_slots else text


def is_customized(roles, positions) -> bool:
    """Whether the viewer's columns differ from everyone's default."""
    return any(not r.visible for r in roles) \
        or [r.position_id for r in roles] != [pid for pid, _ in positions]


def _px(hours: float) -> str:
    return f'{hours * HOUR_PX:.1f}px'


def render_day_bar(
    days: Sequence[date], selected: Optional[date], on_pick: Callable[[date], Union[Awaitable[None], None]], *,
    on_columns: Optional[Callable[[], None]] = None, customized: bool = False,
    trailing: Optional[Callable[[], None]] = None,
) -> None:
    """The day chips, and the Columns button when there are roles to arrange.

    ``trailing`` draws a page's own toolbar buttons at the end of the row.
    """
    with ui.row().classes('wiz-vgrid-days items-center'):
        for d in days:
            pressed = d == selected
            button = ui.button(day_label(d), on_click=lambda d=d: on_pick(d)) \
                .props(f"no-caps rounded {'unelevated color=primary' if pressed else 'outline'}") \
                .classes('wiz-vgrid-day')
            button.props(f'aria-pressed={"true" if pressed else "false"}')
        if on_columns is None and trailing is None:
            return
        tools = ui.row().classes('wiz-vgrid-tools items-center no-wrap')
        if trailing is not None:
            tools.classes('wiz-vgrid-tools--crowded')
        with tools:
            if on_columns is not None:
                # The label is our own element, not the button's, so a phone can
                # hide it: Quasar's .block utility is !important inside a layer.
                with ui.button(icon='view_column', on_click=on_columns) \
                        .props('no-caps outline rounded aria-label="Columns"').classes('wiz-vgrid-columns'):
                    ui.label('Columns').classes('wiz-vgrid-columns__label')
                    if customized:
                        ui.badge().props('floating rounded color=primary').classes('wiz-vgrid-columns__dot')
                        ui.label('(customized)').classes('wiz-sr-only')
            if trailing is not None:
                ui.space()
                trailing()


def open_columns_dialog(
    *, user, key: str, positions, roles, saved: list,
    on_saved: Callable[[tuple, list], None],
) -> None:
    """Pick which roles show and their order, saved per person under ``key``."""
    staged = list(roles)
    dragging = {'index': -1}
    prefs = TablePreferenceService()

    with form_dialog('Columns', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('q-pa-md gap-2 full-width'):
            ui.label('Pick which roles you see and their order. Saved for you on every device.') \
                .classes('text-caption text-muted')
            role_list = ui.column().classes('full-width gap-0')

        def move(index: int, delta: int) -> None:
            target = index + delta
            if 0 <= target < len(staged):
                staged[index], staged[target] = staged[target], staged[index]
                render_roles()

        def drop(source: int, target: int) -> None:
            if source == target or not 0 <= source < len(staged):
                return
            staged.insert(min(target, len(staged) - 1), staged.pop(source))
            render_roles()

        def render_roles() -> None:
            role_list.clear()
            with role_list:
                for index, entry in enumerate(staged):
                    # Drag adds to the ▲/▼ buttons rather than replacing
                    # them: touch screens and keyboards can't drag.
                    row = ui.row().classes('full-width items-center no-wrap gap-2 q-py-xs')
                    row.props('draggable="true"')
                    row.on('dragstart', lambda _, i=index: dragging.update(index=i))
                    row.on('dragover.prevent', lambda: None)
                    row.on('drop', lambda _, i=index: drop(dragging['index'], i))
                    with row:
                        ui.icon('drag_indicator').classes('text-muted cursor-move gt-sm') \
                            .tooltip('Drag to reorder')
                        ui.checkbox(
                            entry.name, value=entry.visible,
                            on_change=lambda e, i=index: set_visible(i, e.value),
                        ).props('dense').classes('col-grow')
                        up = ui.button(icon='arrow_upward', on_click=lambda _, i=index: move(i, -1)) \
                            .props('flat dense round aria-label="Move up"').tooltip('Move up')
                        up.set_enabled(index > 0)
                        down = ui.button(icon='arrow_downward', on_click=lambda _, i=index: move(i, 1)) \
                            .props('flat dense round aria-label="Move down"').tooltip('Move down')
                        down.set_enabled(index < len(staged) - 1)

        def set_visible(index: int, visible: bool) -> None:
            staged[index] = replace(staged[index], visible=bool(visible))

        def show_all() -> None:
            staged[:] = arrange_roles(positions, None)
            render_roles()

        async def save() -> None:
            if not any(r.visible for r in staged):
                ui.notify('Keep at least one role showing.', color='warning')
                return
            if user is None:
                return
            chosen = tuple(staged)
            config = {'columns': roles_config(chosen, saved, TablePreferenceService.MAX_COLUMNS)}
            try:
                stored = await prefs.save(user, key, config)
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            dialog.close()
            on_saved(chosen, stored.get('columns', []))

        render_roles()
        with dialog_actions():
            with dialog_more_menu():
                menu_action('restart_alt', 'Show all in default order', show_all)
            ui.space()
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Save', on_click=save).props('color=primary')
    dialog.open()


def render_legend(grid: DayGrid, now_hour: Optional[float], *, staff: bool) -> None:
    with ui.row().classes('wiz-vgrid-legend items-center'):
        if staff:
            for icon, tone, words in ARRIVAL_MARKS.values():
                with ui.row().classes('items-center gap-1 no-wrap'):
                    ui.icon(icon, size='xs').classes(tone).props('aria-hidden=true')
                    ui.label(words)
            with ui.row().classes('items-center gap-1 no-wrap'):
                ui.label('Draft').classes('wiz-vgrid__name--draft')
                ui.label('(not told yet)')
        else:
            with ui.row().classes('items-center gap-1 no-wrap'):
                ui.element('span').classes('wiz-vgrid-legend__mine')
                ui.label('Your shifts')
        if now_hour is not None:
            with ui.row().classes('items-center gap-1 no-wrap'):
                ui.element('span').classes('wiz-vgrid-legend__now')
                ui.label('Now')
        if len(grid.columns) > 2:
            ui.label('Swipe sideways for more roles').classes('lt-md wiz-vgrid-legend__hint')


def _render_person(person: GridPerson, *, staff: bool) -> None:
    classes = 'wiz-vgrid__name'
    if person.is_viewer and not staff:
        classes += ' wiz-vgrid__name--you'
    if not staff:
        ui.label(person_label(person.name, person.is_viewer)).classes(classes)
        return
    mark = ARRIVAL_MARKS.get(person.arrival)
    with ui.row().classes('wiz-vgrid__person items-center no-wrap'):
        if mark:
            icon, tone, words = mark
            ui.icon(icon, size='14px').classes(tone).props(f'role=img aria-label="{words}"')
        ui.label(person.name).classes(classes + (' wiz-vgrid__name--draft' if person.draft else ''))


def render_day_grid(
    grid: DayGrid, day: date, *,
    staff: bool = False,
    on_shift: Optional[Callable[[GridShift, str], None]] = None,
    on_empty: Optional[Callable[[int, datetime], Union[Awaitable[None], None]]] = None,
    selected_shift_id: int = 0,
) -> None:
    """The grid itself. ``on_shift`` makes each shift a button (the Shift
    Board opens its sheet; the volunteers' grid opens the full name list when
    names overflow); ``on_empty`` is called with a position id and the start of
    the hour tapped in an empty part of its column."""
    now_hour = grid.hour_at(datetime.now(timezone.utc))
    render_legend(grid, now_hour, staff=staff)
    body_height = _px(grid.last_hour - grid.first_hour)
    region = timeline_region(
        grid.midnight, grid.first_hour, f'Volunteer schedule for {day_label(day)}', focusable=True,
    )
    with region:
        render_hour_column(grid.midnight, grid.first_hour, grid.last_hour, now_hour)
        for column in grid.columns:
            with ui.element('div').classes('wiz-vgrid__col'):
                ui.label(column.position).classes('wiz-vgrid__head')
                body = ui.element('div').classes('wiz-vgrid__body wiz-vgrid__body--lines') \
                    .style(f'height: {body_height};')
                if on_empty is not None:
                    body.classes('wiz-vgrid__body--addable')
                    body.on(
                        'click',
                        lambda e, pid=column.position_id: _empty_clicked(grid, pid, e.args, on_empty),
                        # Only a tap on the column itself, not on a shift in it.
                        js_handler='(e) => { if (e.target === e.currentTarget) emit(e.offsetY) }',
                    )
                with body:
                    for shift in column.shifts:
                        _render_shift(
                            shift, column.position, grid, staff=staff, on_shift=on_shift,
                            selected=bool(selected_shift_id) and shift.shift_id == selected_shift_id,
                        )
                    render_column_markers(grid.first_hour, now_hour)


def _empty_clicked(grid: DayGrid, position_id: int, offset_y, on_empty):
    try:
        hour = grid.first_hour + math.floor(float(offset_y) / HOUR_PX)
    except (TypeError, ValueError):
        return None
    # Returned, so an async on_empty is awaited by the event handler.
    return on_empty(position_id, grid.midnight + timedelta(hours=hour))


def _render_shift(
    shift: GridShift, position: str, grid: DayGrid, *, staff: bool,
    on_shift: Optional[Callable[[GridShift, str], None]], selected: bool,
) -> None:
    classes = 'wiz-vgrid__shift'
    if shift.has_viewer and not staff:
        classes += ' wiz-vgrid__shift--mine'
    if selected:
        classes += ' wiz-vgrid__shift--selected'
    top = shift.start_hour - grid.first_hour
    block = ui.element('div').classes(classes).style(
        # 3px in from each hour line so back-to-back shifts never touch.
        f'top: calc({_px(top)} + 3px); height: calc({_px(shift.end_hour - shift.start_hour)} - 6px); '
        f'left: calc(4px + (100% - 8px) * {shift.lane} / {shift.lanes}); '
        f'width: calc((100% - 8px) / {shift.lanes} - {4 if shift.lanes > 1 else 0}px);'
    ).props(f'data-wiz-shift={shift.shift_id}')
    if staff and on_shift is not None:
        block.classes('wiz-vgrid__shift--button')
        block.props(
            f'tabindex=0 role=button '
            f'aria-label="{position}, {shift_range(shift)}, {filled_label(shift)}"'
        )
        block.on('click', lambda s=shift: on_shift(s, position))
        block.on('keydown.enter', lambda s=shift: on_shift(s, position))
    with block:
        ui.label(shift_range(shift)).classes('wiz-vgrid__time')
        shown = names_that_fit(shift, reserved_lines=1 if staff and shift.open_slots else 0)
        for person in shift.people[:shown]:
            _render_person(person, staff=staff)
        hidden = len(shift.people) - shown
        if hidden:
            if staff:
                ui.label(f'+{hidden} more').classes('wiz-vgrid__open')
            elif on_shift is not None:
                ui.button(f'+{hidden} more', on_click=lambda s=shift: on_shift(s, position)) \
                    .props('flat dense no-caps size=sm color=primary').classes('wiz-vgrid__more')
        if staff and shift.open_slots:
            ui.label(f'{shift.open_slots} open').classes('wiz-vgrid__gap')
        elif not shift.people:
            ui.label('Nobody yet').classes('wiz-vgrid__open')


def timeline_region(midnight: datetime, first_hour: int, label: str, *, focusable: bool = False) -> ui.element:
    """The outer element of an hour-scale grid. ``data-t0`` (the instant at the
    top of the scale) and ``data-tz`` let static/js/timeline.js turn a pointer
    position back into a time, and keep the now line moving."""
    t0 = int((midnight + timedelta(hours=first_hour)).timestamp() * 1000)
    props = f'role=region aria-label="{label}" data-t0={t0} data-tz="{current_timezone_name()}"'
    if focusable:
        props = f'tabindex=0 {props}'
    return ui.element('div').classes('wiz-vgrid').props(props)


def render_hour_column(midnight: datetime, first_hour: int, last_hour: int, now_hour: Optional[float]) -> None:
    """The pinned hour labels, with the time tags for the now and pointer lines."""
    with ui.element('div').classes('wiz-vgrid__times'):
        ui.element('div').classes('wiz-vgrid__head')
        with ui.element('div').classes('wiz-vgrid__body').style(f'height: {_px(last_hour - first_hour)};'):
            for hour in range(first_hour, last_hour):
                ui.label(format_local_time(midnight + timedelta(hours=hour))) \
                    .classes('wiz-vgrid__hour').style(f'top: {_px(hour - first_hour)};')
            now_tag = ui.label(format_local_time(datetime.now(timezone.utc))).classes('wiz-vgrid__tag wiz-vgrid__tag--now')
            _place(now_tag, first_hour, now_hour)
            ui.label().classes('wiz-vgrid__tag wiz-vgrid__tag--cursor').props('hidden aria-hidden=true')


def render_column_markers(first_hour: int, now_hour: Optional[float]) -> None:
    """The now and pointer lines inside one column; timeline.js moves them."""
    _place(ui.element('div').classes('wiz-vgrid__now'), first_hour, now_hour)
    ui.element('div').classes('wiz-vgrid__cursor').props('hidden aria-hidden=true')


def _place(marker: ui.element, first_hour: int, hour: Optional[float]) -> None:
    # A transform rather than top, so the minute-by-minute move is not a layout shift.
    marker.props('aria-hidden=true')
    if hour is None:
        marker.props('hidden')
    else:
        marker.style(f'transform: translateY({_px(hour - first_hour)});')


def person_status(person: GridPerson) -> Tuple[str, str]:
    """What the Shift Board's sheet says under a name, and its tone class."""
    if person.draft:
        return 'Draft, not told yet', 'st-neutral'
    if person.arrival == ARRIVED and person.checked_in_at is not None:
        return f'Checked in {format_local_time(person.checked_in_at)}', 'st-ok'
    if person.arrival == LATE and person.late_minutes is not None:
        return f'Running late ({late_label(person.late_minutes).lower()})', 'st-pending'
    if person.arrival == NOT_HERE:
        return 'Not here yet', 'st-pending'
    if person.acknowledged:
        return 'Confirmed', 'st-ok'
    return "Hasn't confirmed yet", 'st-neutral'
