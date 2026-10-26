"""Event settings → Dates & hours, and → Capacity: when this event runs and how much of it runs at once."""

from datetime import date, timedelta

from nicegui import ui

from application.services import SystemConfigService, TimezoneService
from application.services.system_config_service import (
    KEY_EVENT_END_DATE,
    KEY_EVENT_START_DATE,
    KEY_MAX_CONCURRENT_PLAYERS,
    KEY_MAX_CONCURRENT_STAGES,
)
from application.utils.timezone import timezone_label
from pages.admin_tabs.settings._common import current_actor, field_hint, positive_int_or_blank
from theme.dialog._helpers import native_date_input, native_time_input
from theme.notify import notify_error
from theme.save_footer import SaveFooter
from theme.section import section_panel
from theme.steady import steady_refreshable


async def dates_and_hours_pane(can_edit: bool) -> None:
    # Operating hours are the event's rule, so they are written and enforced on
    # the event's clock, never the admin's, and labelled with that zone so an
    # admin abroad is not silently reading the numbers as their own time.
    tenant_tz_label = timezone_label(await TimezoneService.tenant_timezone_name())

    @steady_refreshable
    async def hours_rows() -> None:
        tournament_hours = await SystemConfigService.get_tournament_hours()
        event_start, event_end = await SystemConfigService.get_event_window()
        hours_inputs.clear()
        current = event_start
        while current <= event_end:
            window = tournament_hours.get(current)
            with ui.row().classes('wiz-settings-hours items-center gap-3 no-wrap'):
                ui.label(f'{current:%a %b} {current.day}').classes('wiz-settings-day')
                open_input = native_time_input(
                    'Opens', window[0].strftime('%H:%M') if window else '',
                    dense=True, stack_label=False, show_timezone=False,
                ).classes('wiz-settings-time')
                close_input = native_time_input(
                    'Closes', window[1].strftime('%H:%M') if window else '',
                    dense=True, stack_label=False, show_timezone=False,
                ).classes('wiz-settings-time')
            hours_footer.watch(open_input, close_input)
            if not can_edit:
                open_input.disable()
                close_input.disable()
            hours_inputs[current] = (open_input, close_input)
            current += timedelta(days=1)

    hours_inputs: dict[date, tuple[ui.input, ui.input]] = {}

    start_date = await SystemConfigService.get_date(KEY_EVENT_START_DATE)
    end_date = await SystemConfigService.get_date(KEY_EVENT_END_DATE)

    dates = await section_panel(
        'Event dates', icon='date_range',
        subtitle='Leave both blank and the event runs from the first scheduled match to the last.',
    )
    with dates.body:
        with ui.row().classes('gap-4 items-start'):
            start_input = native_date_input('Starts', start_date.isoformat() if start_date else '', clearable=True)
            end_input = native_date_input('Ends', end_date.isoformat() if end_date else '', clearable=True)

    async def save_dates() -> bool:
        start_raw = (start_input.value or '').strip()
        end_raw = (end_input.value or '').strip()
        try:
            for label, raw in (('start', start_raw), ('end', end_raw)):
                if raw:
                    try:
                        date.fromisoformat(raw)
                    except ValueError:
                        raise ValueError(f'The {label} date must be in YYYY-MM-DD format.') from None
            actor = await current_actor()
            await SystemConfigService.set_raw(KEY_EVENT_START_DATE, start_raw, actor)
            await SystemConfigService.set_raw(KEY_EVENT_END_DATE, end_raw, actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        ui.notify('Event dates saved', color='positive')
        # The hours card lists one row per event day, so new dates mean new rows.
        hours_rows.refresh()
        return True

    with dates.panel:
        SaveFooter(save_dates, enabled=can_edit).watch(start_input, end_input)
    if not can_edit:
        start_input.disable()
        end_input.disable()

    hours = await section_panel(
        'Daily hours', icon='schedule',
        subtitle=(
            f'Matches can only be scheduled to start inside these hours, in '
            f'{tenant_tz_label}, the event’s own timezone. Leave a day blank '
            f'to allow any time.'
        ),
    )

    async def save_hours() -> bool:
        mapping: dict[date, tuple[str, str]] = {}
        for day, (open_input, close_input) in hours_inputs.items():
            open_str = (open_input.value or '').strip()
            close_str = (close_input.value or '').strip()
            if open_str and close_str:
                mapping[day] = (open_str, close_str)
        try:
            await SystemConfigService.set_tournament_hours(mapping, await current_actor())
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        ui.notify('Daily hours saved', color='positive')
        return True

    # Created before the rows so they can register with it; it still renders
    # under them because the panel's body element already exists.
    with hours.panel:
        hours_footer = SaveFooter(save_hours, enabled=can_edit)
    with hours.body:
        await hours_rows()



async def capacity_pane(can_edit: bool) -> None:
    max_players = await SystemConfigService.get_int(KEY_MAX_CONCURRENT_PLAYERS)
    max_stages = await SystemConfigService.get_int(KEY_MAX_CONCURRENT_STAGES)

    capacity = await section_panel(
        'Capacity', icon='groups_2',
        subtitle='How much the scheduler lets run at the same time.',
    )
    with capacity.body, ui.row().classes('gap-6 items-start'):
        with ui.column().classes('gap-1'):
            players_input = ui.number('Players at once', value=max_players, min=1, format='%d').classes('w-48')
            field_hint('Blank uses 60.')
        with ui.column().classes('gap-1'):
            stages_input = ui.number('Stages at once', value=max_stages, min=1, format='%d').classes('w-48')
            field_hint('Blank uses the number of active stages.')

    async def save_capacity() -> bool:
        try:
            players_raw = positive_int_or_blank(players_input.value, 'Players at once')
            stages_raw = positive_int_or_blank(stages_input.value, 'Stages at once')
            actor = await current_actor()
            await SystemConfigService.set_raw(KEY_MAX_CONCURRENT_PLAYERS, players_raw, actor)
            await SystemConfigService.set_raw(KEY_MAX_CONCURRENT_STAGES, stages_raw, actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        ui.notify('Capacity saved', color='positive')
        return True

    with capacity.panel:
        SaveFooter(save_capacity, enabled=can_edit).watch(players_input, stages_input)
    if not can_edit:
        players_input.disable()
        stages_input.disable()
