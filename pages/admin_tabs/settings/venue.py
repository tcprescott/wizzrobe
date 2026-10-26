"""Settings → Venue: the room itself. Stations, how they are numbered, room screens."""

from nicegui import ui

from application.services import StationService, SystemConfigService
from application.services.system_config_service import KEY_STATION_FORMAT
from models import StationFormat, StationSide
from pages.admin_tabs.room_tokens_section import render_room_tokens_section
from pages.admin_tabs.settings._common import current_actor
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error
from theme.save_footer import SaveFooter
from theme.section import section_panel
from theme.steady import steady_refreshable

_STATION_FORMATS = {
    StationFormat.FREE.value: 'Anything (no check)',
    StationFormat.NUMERIC.value: 'Numbers only (1, 2, 3…)',
    StationFormat.STRUCTURED.value: 'Letter and number (A1, B12)',
    StationFormat.ALPHANUMERIC.value: 'Letters, numbers and hyphens',
}


async def _numbering_card(can_edit: bool) -> None:
    station_format = await SystemConfigService.get_station_format()
    card = await section_panel(
        'Station names', icon='pin',
        subtitle='What a proctor may type when assigning a station by hand.',
    )
    with card.body:
        format_input = ui.select(_STATION_FORMATS, value=station_format.value, label='Format').classes('w-72')
        if not can_edit:
            format_input.disable()

    async def save() -> bool:
        try:
            await SystemConfigService.set_raw(
                KEY_STATION_FORMAT, format_input.value or StationFormat.FREE.value, await current_actor(),
            )
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        ui.notify('Station names saved', color='positive')
        return True

    with card.panel:
        SaveFooter(save, enabled=can_edit).watch(format_input)


async def _stations_card(can_edit: bool) -> None:
    """The venue's station pool, edited in place.

    A community with no stations keeps the historical free-text station field, so
    an empty pool is a valid state rather than a setup step.
    """
    service = StationService()

    @steady_refreshable
    async def station_list() -> None:
        stations = await service.list_stations()
        if not stations:
            ui.label(
                'No stations defined. Proctors type the station by hand; add '
                'stations here to pick from a list and block double-booking.'
            ).classes('text-caption text-grey')
            return
        unsided = [s for s in stations if s.is_active and s.side is None]
        if unsided:
            ui.label(
                f'{len(unsided)} active station(s) have no side set, so check-in '
                'cannot draw seats across the room for them: '
                + ', '.join(s.name for s in unsided)
            ).classes('text-caption text-warning')

        for station in stations:
            with ui.row().classes('wiz-station-row items-center gap-3 q-mb-xs full-width'):
                ui.label(station.name).classes('w-16 text-weight-medium')
                ui.label(station.section or '—').classes('col text-caption text-grey')
                # Editable in place rather than through a dialog: describing a
                # room's layout means touching every row once, and a pool built
                # before these fields existed would otherwise have to be
                # deleted and retyped to gain a side.
                async def set_side(e, s=station) -> None:
                    try:
                        await service.update_station(
                            s.id,
                            await current_actor(),
                            side=StationSide(e.value) if e.value else None,
                            clear_side=not e.value,
                        )
                    except (ValueError, PermissionError) as err:
                        notify_error(err)
                    station_list.refresh()

                async def set_position(e, s=station) -> None:
                    try:
                        await service.update_station(
                            s.id,
                            await current_actor(),
                            position=int(e.value) if e.value else None,
                            clear_position=not e.value,
                        )
                    except (ValueError, PermissionError) as err:
                        notify_error(err)

                ui.select(
                    options={sd.value: sd.value.title() for sd in StationSide},
                    value=station.side.value if station.side else None,
                    label='Side',
                    clearable=True,
                    on_change=set_side,
                ).props('outlined dense').classes('w-32').set_enabled(can_edit)
                # Debounced: without it every keystroke in the seat number is a
                # write and an audit row.
                ui.number(
                    'Seat #',
                    value=station.position,
                    format='%d',
                    on_change=set_position,
                ).props('outlined dense debounce=800').classes('w-20').set_enabled(can_edit)

                async def toggle(_e, s=station) -> None:
                    try:
                        await service.update_station(
                            s.id, await current_actor(), is_active=not s.is_active,
                        )
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                    station_list.refresh()

                async def do_remove(s) -> None:
                    try:
                        await service.delete_station(s.id, await current_actor())
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                    station_list.refresh()

                def remove(_e, s=station) -> None:
                    ConfirmationDialog(
                        f'Delete {s.name}? Turning it off keeps it for later instead.',
                        on_confirm=lambda: do_remove(s), confirm_text='Delete',
                        title='Delete station?',
                    ).open()

                ui.switch(
                    'Active', value=station.is_active, on_change=toggle,
                ).props('dense').set_enabled(can_edit)
                ui.button(icon='delete', on_click=remove).props(
                    'flat dense color=negative'
                ).tooltip(f'Delete {station.name}').set_enabled(can_edit)

    card = await section_panel(
        'Stations', icon='chair',
        subtitle=(
            'The physical stations in your venue. Once any exist, proctors pick '
            'from this list and a station a live match is using cannot be '
            'assigned twice. Deactivate rather than delete to keep past matches '
            'readable. Check-in uses side and seat number to put a match\'s two '
            'players on opposite sides with free neighbours; both are optional.'
        ),
    )
    with card.body:
        with ui.column().classes('gap-1 full-width'):
            await station_list()

        if not can_edit:
            return

        with ui.row().classes('items-center gap-3 wrap'):
            name_input = ui.input('Name').props('outlined dense maxlength=50').classes('w-32')
            section_input = ui.input('Section').props('outlined dense maxlength=50').classes('w-40')
            side_input = ui.select(
                options={s.value: s.value.title() for s in StationSide},
                label='Side',
                clearable=True,
            ).props('outlined dense').classes('w-32')
            position_input = ui.number('Seat #', format='%d').props('outlined dense').classes('w-24')
            order_input = ui.number('Order', value=0, format='%d').props('outlined dense').classes('w-24')

            async def add() -> None:
                try:
                    await service.create_station(
                        name_input.value or '',
                        await current_actor(),
                        section=section_input.value or None,
                        side=StationSide(side_input.value) if side_input.value else None,
                        position=int(position_input.value) if position_input.value else None,
                        sort_order=int(order_input.value or 0),
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                name_input.value = ''
                section_input.value = ''
                position_input.value = None
                station_list.refresh()

            ui.button('Add station', icon='add', on_click=add).props('color=primary unelevated no-caps').classes('wiz-write')


async def venue_settings_pane(can_edit: bool) -> None:
    await _stations_card(can_edit)
    await _numbering_card(can_edit)
    await render_room_tokens_section(can_edit)
