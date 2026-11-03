from nicegui import ui

from application.services import FeedbackService
from models import FeedbackCategory, User
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet, submit_on_enter
from theme.help import resolve_help
from theme.notify import notify_error
from theme.pickers import ColorSelect

CATEGORY_OPTIONS = {
    FeedbackCategory.BUG.value: 'Bug',
    FeedbackCategory.SUGGESTION.value: 'Suggestion',
    FeedbackCategory.PRAISE.value: 'Praise',
    FeedbackCategory.OTHER.value: 'Other',
}

CATEGORY_ICONS = {
    FeedbackCategory.BUG.value: {'icon': 'o_bug_report'},
    FeedbackCategory.SUGGESTION.value: {'icon': 'o_lightbulb'},
    FeedbackCategory.PRAISE.value: {'icon': 'o_favorite_border'},
    FeedbackCategory.OTHER.value: {'icon': 'o_chat_bubble_outline'},
}


class FeedbackDialog:
    """Lets a logged-in attendee submit feedback, recording the page they were on."""

    def __init__(self, user: User, initial_category: str | None = None, initial_message: str = ''):
        self.user = user
        self.dialog = None
        self.feedback_service = FeedbackService()
        self.initial_category = initial_category or FeedbackCategory.SUGGESTION.value
        self.initial_message = initial_message

    async def open(self):
        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header('Send Feedback', dialog, help=await resolve_help(['feedback-send'], user=self.user))
            with ui.column().classes('q-pa-md gap-2 full-width'):
                category_input = ColorSelect(
                    CATEGORY_OPTIONS,
                    value=self.initial_category,
                    label='Category',
                    extras=CATEGORY_ICONS,
                ).classes('full-width').props(remove='options-dense')
                message_input = ui.textarea(
                    label='Message',
                    value=self.initial_message,
                    placeholder='Tell us what works, what doesn\'t, or what you\'d like to see...',
                ).props('required autofocus').classes('full-width')
                ui.label('* required').classes('required-legend')

            async def submit():
                message = (message_input.value or '').strip()
                if not message:
                    with self.dialog:
                        ui.notify('Please enter a message before sending.', color='warning')
                    return
                page_url = await ui.run_javascript(
                    'window.location.pathname + window.location.search'
                )
                with self.dialog:
                    try:
                        await self.feedback_service.submit(
                            actor=self.user,
                            category=category_input.value,
                            message=message,
                            page_url=page_url or '',
                        )
                    except (ValueError, PermissionError) as e:
                        notify_error(e)
                        return
                    ui.notify('Thanks for your feedback!', color='positive')
                    dialog.close()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                send_button = ui.button('Send', on_click=submit).props('color=primary')
                send_button.bind_enabled_from(
                    message_input, 'value',
                    backward=lambda v: bool(v and v.strip()),
                )

            submit_on_enter(dialog, submit)
            dialog.open()
