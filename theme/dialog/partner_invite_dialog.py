"""Invite a commentary partner: pick a member, send them the invite.

Opened from "Invite a partner" in the viewer's own commentary tray. The partner
gets a DM with Decline and Accept, and the same pair on My Schedule.
"""

from typing import Awaitable, Callable

from nicegui import ui

from application.services import CommentaryInviteService
from application.utils.match_labels import match_row_label
from models import User
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.dialog._helpers import dialog_actions, form_dialog, submit_on_enter
from theme.pickers import member_select

HINT = ("They get a DM to accept or decline. Once they accept, you're both signed up and "
        'staff approve each of you on your own.')


async def open_partner_invite_dialog(row: dict, user: User, on_done: Callable[[], Awaitable[None]]) -> None:
    service = CommentaryInviteService()
    try:
        choices = await service.partner_choices(row['id'], user)
    except ValueError as e:
        ui.notify(str(e), color='warning')
        return

    with form_dialog('Invite a partner') as dialog:
        with ui.column().classes('gap-2 full-width'):
            ui.label(f'Commentate {match_row_label(row)} with someone.').classes('text-body2')
            picker = member_select(choices, value=None, label='Partner').classes('full-width')
            ui.label(HINT).classes('text-caption text-muted')

        async def send() -> None:
            if picker.value is None:
                ui.notify('Pick who to invite first.', color='warning')
                return
            try:
                invite = await service.invite(row['id'], user, int(picker.value))
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            dialog.close()
            ui.notify(f'Invite sent to {invite.invitee.preferred_name}.', color='positive')
            await on_done()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat no-caps')
            ui.button('Send invite', icon='send', on_click=send) \
                .props('unelevated no-caps color=primary').classes(REQUIRES_SOCKET_CLASS)
        submit_on_enter(dialog, send)
    dialog.open()
