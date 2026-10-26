"""What the viewer currently has checked out, as a card at the top of Profile.

This module used to be Home's Equipment *tab*, carrying a full inventory browser
alongside the viewer's own loans. The inventory left the player surface when Home
came down to four tabs: browsing the cage is staff work and lives on Admin →
Equipment, while the way an attendee actually borrows something is scanning the
QR code on the device itself, which lands on ``/equipment/<id>`` and checks out
from there. What remained worth showing a player is the answer to "what am I
still holding?", which is this.

It sat on My Schedule for a while, where it read as out of place among the
things you committed to, and an empty panel saying "you have nothing" sat at the
bottom of every player's schedule. On Profile it draws nothing at all unless you
are holding something.
"""

from nicegui import ui

from application.services import EquipmentService
from application.utils.timezone import format_local_display
from models import User
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.help import help_icon
from theme.links import link_button

#: Anchor for links that should land on the card.
EQUIPMENT_SECTION_ID = 'equipment-yours'


async def equipment_checkouts_section(user: User) -> None:
    loans = await EquipmentService().my_checkouts(user)
    if not loans:
        return

    with ui.card().classes('card-full-width wiz-equipment-out').props(f'id={EQUIPMENT_SECTION_ID}'):
        with ui.row().classes('items-center gap-1 no-wrap wiz-help-row'):
            ui.label('Equipment you have out').classes('section-title')
            ui.badge(str(len(loans))).props('outline color=primary')
            await help_icon('equipment-yours', 'equipment-return', user=user)
        ui.label(
            "Hand it back at the equipment desk when you're done."
        ).classes('text-muted text-caption')
        for loan in loans:
            with ui.row().classes('wiz-equipment-out__row items-center no-wrap w-full gap-3'):
                with ui.column().classes('gap-0 col min-w-0'):
                    ui.label(loan.equipment.name).classes('text-weight-medium ellipsis')
                    ui.label(
                        f'#{loan.equipment.asset_number} · out since '
                        f'{format_local_display(loan.checked_out_at)}'
                    ).classes('text-muted text-caption')
                link_button('View', f'/equipment/{loan.equipment_id}', new_tab=False).props(f'flat no-caps color=primary aria-label="View {loan.equipment.name}"') \
                    .classes(REQUIRES_SOCKET_CLASS)
