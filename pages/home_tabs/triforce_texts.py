"""Triforce text submission, opened from a tournament's signup card.

This was its own Home tab with its own tournament index, which meant picking a
tournament twice: once on Tournaments to sign up, again here to submit text for
the thing you just signed up for. The index is gone and the submission form is a
dialog hanging off the card for the tournament it belongs to.
"""

from typing import Set

from nicegui import Client, ui

from application.content import parse_blocks
from application.services import (
    AuthService,
    SeedGenerationService,
    TriforceTextService,
)
from models import Tournament, User
from theme.dialog._helpers import form_dialog
from theme.help.render import render_blocks
from theme.steady import steady_refreshable

_HELP_TEXT = (
    "Submit up to 3 lines (max 19 characters each) that may be embedded into "
    "the end-game triforce screen of an ALTTP randomizer seed. Allowed "
    "characters: letters, numbers, spaces, basic punctuation, arrows, and "
    "hiragana/katakana. Submissions are reviewed by tournament admins."
)


async def supporting_tournament_ids() -> Set[int]:
    """Tournaments whose seed generator can carry triforce text.

    Only says the tournament *supports* submissions. Whether this particular
    viewer may submit is :meth:`AuthService.can_submit_triforce_text`, resolved
    when the dialog opens, because it can turn on a paid option per user.
    """
    return {t.id for t in await TriforceTextService().list_supporting_tournaments()}


async def open_triforce_dialog(tournament: Tournament, user: User, client: Client) -> None:
    """Build and open the dialog inside ``client``.

    The card's button runs this as a background task, where the slot stack is
    empty: without re-entering the client, ``ui.dialog()`` has nowhere to go and
    the button did nothing at all.
    """
    with client:
        await _build_triforce_dialog(tournament, user)


async def _build_triforce_dialog(tournament: Tournament, user: User) -> None:
    service = TriforceTextService()
    accepts = tournament.is_active and SeedGenerationService.supports_triforce_texts(
        tournament.seed_generator
    )
    can_submit = await AuthService.can_submit_triforce_text(user, tournament)

    with form_dialog(f'Triforce texts — {tournament.name}') as dialog:

        if not accepts:
            ui.label(
                'This tournament is not accepting triforce text submissions.'
            ).classes('text-grey-7')
            dialog.open()
            return

        if not can_submit:
            if tournament.triforce_access_message:
                # Parsed to blocks, never ui.markdown: admins write this and every player reads it.
                with ui.element('div').classes('wiz-help-prose'):
                    render_blocks(parse_blocks(tournament.triforce_access_message))
            else:
                ui.label(
                    'Submitting triforce texts is a paid option for this tournament.'
                ).classes('text-grey-7')
            dialog.open()
            return

        ui.label(_HELP_TEXT).classes('text-caption text-grey-7')
        ui.separator().classes('separator-spacing')

        line_inputs: list[ui.input] = []
        with ui.column().classes('w-full q-gutter-sm'):
            for i in range(1, 4):
                line_inputs.append(
                    ui.input(label=f'Line {i}')
                    .props('maxlength=19 outlined dense')
                    .classes('w-full')
                )

        @steady_refreshable
        async def submissions_list() -> None:
            rows = await service.list_user_submissions(tournament.id, user)
            if not rows:
                ui.label('You have no submissions yet.').classes('text-caption text-grey-7')
                return
            for entry in rows:
                status = (
                    'Pending' if entry.approved is None
                    else 'Approved' if entry.approved
                    else 'Rejected'
                )
                color = (
                    'orange' if entry.approved is None
                    else 'positive' if entry.approved
                    else 'negative'
                )
                with ui.card().classes('w-full q-mb-sm'):
                    with ui.row().classes('items-center justify-between w-full'):
                        ui.label(entry.text).classes(
                            'q-py-xs'
                        ).style('white-space: pre-line; font-family: monospace;')
                        ui.badge(status, color=color)

        async def on_submit():
            lines = [inp.value or '' for inp in line_inputs]
            try:
                await service.submit(tournament.id, lines, user)
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            ui.notify('Submitted! A moderator will review.', color='positive')
            for inp in line_inputs:
                inp.set_value('')
            submissions_list.refresh()

        with ui.row().classes('q-mt-md'):
            ui.button('Submit', icon='send', on_click=on_submit).props('color=primary')

        ui.separator().classes('separator-spacing')
        ui.label('Your Submissions').classes('section-title')
        await submissions_list()

    dialog.open()
