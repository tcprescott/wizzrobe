"""Check result: staff settle a race the racetime room bot held back.

Opened from the "Racetime results held" group in the Needs you inbox. Shows
why the result waited (a fair play pause, a close finish, a forfeit, a runner
the bot couldn't match), then each player with the place and time racetime
reported, ready to correct. Confirm result records it and reports it to the
bracket exactly as a clean finish would have.
"""

from typing import Awaitable, Callable, Dict, Optional, Tuple

from nicegui import context, ui

from application.services import RaceRoomService
from application.services.race_room_holds import hold_label
from application.utils.duration import format_hms, parse_hms
from theme.banner import banner
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.notify import notify_error
from theme.tables.admin_crud import current_actor

_FORFEIT = 0


async def open_check_result(match_id: int, on_done: Optional[Callable[[], Awaitable[None]]] = None) -> None:
    client = context.client
    actor = await current_actor()
    try:
        held = await RaceRoomService().held_result(actor, match_id)
    except (ValueError, PermissionError) as e:
        notify_error(e)
        return

    count = len(held.players)
    place_options = {n: str(n) for n in range(1, count + 1)}
    place_options[_FORFEIT] = 'Forfeit'
    fields: Dict[int, Tuple[ui.select, ui.input]] = {}

    with form_dialog('Check result', card_classes='dialog-card') as dialog:
        with ui.column().classes('q-pa-md gap-3 full-width'):
            with banner(held.detail or 'This result is waiting for a check.',
                        title=hold_label(held.reason), tone='warning', icon='pause_circle'):
                pass
            for call in held.fpa_calls:
                if call.get('note'):
                    ui.label(f"{call.get('name') or 'A runner'} said: {call['note']}") \
                        .classes('text-muted')
            if held.other_runners:
                ui.label('Also in the room: ' + ', '.join(held.other_runners)).classes('text-muted')
            ui.label('Set each place and time as it should stand.').classes('text-muted')
            for player in held.players:
                with ui.row().classes('items-center gap-2 full-width wrap'):
                    with ui.column().classes('col gap-0'):
                        ui.label(player.name).classes('text-weight-medium')
                        if player.racetime_name and player.racetime_name != player.name:
                            ui.label(f'racetime: {player.racetime_name}').classes('text-muted text-caption')
                    place = ui.select(
                        place_options, label='Place',
                        value=player.place if player.place in place_options else _FORFEIT,
                    ).props('outlined dense').style('width: 8rem') \
                        .props(f'aria-label="Place for {player.name}"')
                    time_input = ui.input(
                        'Time', value=format_hms(player.finish_seconds, dash=''),
                        placeholder='1:23:45',
                    ).props('outlined dense').style('width: 8rem') \
                        .props(f'aria-label="Finish time for {player.name}"')
                    time_input.bind_enabled_from(place, 'value', backward=lambda v: v != _FORFEIT)
                    fields[player.match_player_id] = (place, time_input)

        async def confirm() -> None:
            placements: Dict[int, Tuple[Optional[int], Optional[int]]] = {}
            try:
                for mp_id, (place, time_input) in fields.items():
                    if place.value == _FORFEIT:
                        placements[mp_id] = (None, None)
                    else:
                        placements[mp_id] = (int(place.value), parse_hms(time_input.value))
                with client:
                    who = await current_actor()
                    await RaceRoomService().confirm_held_result(who, match_id, placements)
            except (ValueError, PermissionError) as e:
                with client:
                    notify_error(e)
                return
            with client:
                ui.notify('Result recorded and sent to the bracket.', color='positive')
            dialog.close()
            if on_done is not None:
                await on_done()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Confirm result', on_click=busy_click('Recording…', confirm)).props('color=primary')
    dialog.open()
