"""The one-time "Tell us when you can't play" card at the top of My Schedule.

Drawn for a player who has blocked nothing out in this community and hasn't put
the card away. Either button puts it away for good: **Got it** just hides it,
**Block out times** also takes them to the editor.
"""

from typing import Awaitable, Callable

from nicegui import ui

from application.services.player_availability_service import PlayerAvailabilityService
from models import User

#: A sample day for the strip: ``True`` is a blocked-out hour.
_SAMPLE_DAY = (False, False, False, True, True, False, False, False, False, True, False, False)


async def render_availability_reminder(user: User, on_block_out: Callable[[], Awaitable[None]]) -> None:
    service = PlayerAvailabilityService()
    if not await service.needs_reminder(user):
        return

    async def put_away(then_open: bool) -> None:
        await service.dismiss_reminder(user)
        card.delete()
        if then_open:
            await on_block_out()

    with ui.element('div').classes('wiz-avail-reminder w-full') \
            .props('role=region aria-label="When you can\'t play"') as card:
        with ui.column().classes('wiz-avail-reminder__body gap-1'):
            with ui.row().classes('items-center gap-2 no-wrap'):
                ui.icon('event_busy').classes('wiz-avail-reminder__icon')
                ui.label("Tell us when you can't play").classes('wiz-avail-reminder__title')
            ui.label(
                'Right now you count as free all weekend. Block out a dinner, a flight '
                "or a shift and we'll keep your matches off it."
            )
            with ui.element('div').classes('wiz-avail-reminder__strip').props('aria-hidden=true'):
                for blocked in _SAMPLE_DAY:
                    ui.element('span').classes(
                        'wiz-avail-reminder__hour' + (' wiz-tl-hatch' if blocked else '')
                    )
            ui.label('Example: a day with two times blocked out').classes('wiz-avail-reminder__caption')
        with ui.row().classes('wiz-avail-reminder__actions'):
            ui.button('Got it', on_click=lambda: put_away(False)).props('flat dense no-caps')
            ui.button('Block out times', on_click=lambda: put_away(True)) \
                .props('unelevated dense no-caps color=primary')
