"""A person's own feedback submissions, on their profile.

The missing half of the feedback loop. Someone types a question into the
sidebar form — *"who do I ask about getting my racetime name fixed?"* — and
until now that was the last they ever saw of it: staff could mark it reviewed,
but nothing said so anywhere the submitter could look, and there was no list of
what they had sent. The queue was one-directional by construction.

Read-only on purpose. This is not a reply thread — staff answer in whatever
channel the community actually uses — it is the acknowledgement that the thing
was received and looked at, which is what its absence was costing.

Presentation only: one service read, no ORM.
"""

from nicegui import ui

from application.services import FeedbackService
from application.utils.timezone import format_local_display
from models import Feedback, User

VISIBLE_LIMIT = 10

_CATEGORY_LABELS = {
    'bug': 'Bug',
    'suggestion': 'Suggestion',
    'praise': 'Praise',
    'other': 'Other',
}


def status_chip(feedback: Feedback) -> tuple[str, str]:
    """``(chip class, label)`` for one submission — the two states it can be in.

    Pure so the vocabulary is testable without a browser, and so this card and
    the staff queue cannot end up describing the same row differently.
    """
    if feedback.status.value == 'reviewed':
        return 'wiz-chip--ok', 'Read by staff'
    return 'wiz-chip--pending', 'Not read yet'


def category_label(feedback: Feedback) -> str:
    return _CATEGORY_LABELS.get(feedback.category.value, feedback.category.value)


async def render_my_feedback_section(user: User) -> None:
    """Render the card, or nothing at all when the person has sent nothing.

    An empty card on a profile is noise for the large majority who have never
    used the form; the sidebar is where you go to *send* feedback, and this only
    exists to tell you what happened to what you sent.
    """
    try:
        submissions = await FeedbackService().list_mine(user, limit=VISIBLE_LIMIT)
    except ValueError:
        return
    if not submissions:
        return

    with ui.card().classes('card-full-width'):
        ui.label('Your feedback').classes('section-title')
        ui.label(
            'What you have sent through the feedback form, and whether staff '
            'have read it.'
        ).classes('text-muted text-caption')
        for feedback in submissions:
            chip_class, status = status_chip(feedback)
            with ui.column().classes('gap-0 q-mt-sm full-width'):
                with ui.row().classes('items-center gap-2 flex-wrap full-width'):
                    with ui.element('span').classes(f'wiz-chip {chip_class}'):
                        ui.label(status)
                    ui.label(category_label(feedback)).classes('text-weight-medium')
                    ui.space()
                    ui.label(
                        format_local_display(feedback.created_at)
                    ).classes('text-caption text-muted')
                # Plain text, never markup — the submitter wrote it.
                ui.label(feedback.message).classes('text-body2 wiz-free-text')
