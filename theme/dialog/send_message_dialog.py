from nicegui import ui

from application.services import DiscordService
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet, submit_on_enter


class SendMessageDialog:
    def __init__(self, user, send_callback=None):
        self.user = user
        self.send_callback = send_callback if send_callback else self.send
        self.dialog = None
        self.discord_service = DiscordService()

    async def open(self):
        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header('Send Message', dialog)
            with ui.column().classes('q-pa-md gap-2'):
                message_input = ui.textarea(
                    label='Message',
                    placeholder='Enter message to send...',
                ).props('required').classes('full-width')
                ui.label('* required').classes('required-legend')

            async def send_message():
                message = (message_input.value or '').strip()
                if not message:
                    with self.dialog:
                        ui.notify('Please enter a message before sending.', color='warning')
                    return
                with self.dialog:
                    await self.send_callback(self.user, message)
                    ui.notify('Message sent.', color='positive')
                    dialog.close()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                send_button = ui.button('Send', on_click=busy_click('Sending…', send_message)).props('color=primary')
                send_button.bind_enabled_from(
                    message_input, 'value',
                    backward=lambda v: bool(v and v.strip()),
                )

            submit_on_enter(dialog, send_message)
            dialog.open()

    async def send(self, user, message):
        result, msg = await self.discord_service.send_dm(user.discord_id, message)
        if not result:
            ui.notify(f'Failed to send message.  Bot returned error: {msg}', color='negative')
        else:
            ui.notify(f"Send message to {user.username} ({user.id}): {message}", color='positive')
