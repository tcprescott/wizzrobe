"""The "Check this time" sheet every booking dialog shows before it saves.

Asks, never refuses: the person sees why the time might not work (a player
already busy, a full room, past closing) and can schedule it anyway. The reasons
come from ``MatchSuggestionService.find_clashes``, so they follow the same rules
as Suggest a time.
"""

import logging
from datetime import datetime
from typing import List, Optional

from nicegui import ui

from application.services import MatchSuggestionService
from application.utils.timezone import format_local_display, parse_local_datetime
from models import User
from theme.dialog._helpers import dialog_actions, form_dialog

logger = logging.getLogger(__name__)


async def confirm_time(
    *,
    tournament_id: Optional[int],
    player_ids: List[int],
    scheduled_at: Optional[datetime],
    actor: Optional[User],
    exclude_match_id: Optional[int] = None,
    confirm_label: str = 'Schedule anyway',
) -> bool:
    """True when the save should go ahead: no clashes, or the person said so.

    A check that can't run (no time yet, no actor, an error) lets the save go
    ahead: the warning is a courtesy, and the save path has its own refusals.
    """
    if not (tournament_id and scheduled_at and actor) or not [p for p in player_ids if p]:
        return True
    try:
        clashes = await MatchSuggestionService().find_clashes(
            tournament_id, [p for p in player_ids if p], scheduled_at,
            actor=actor, exclude_match_id=exclude_match_id,
        )
    except Exception:
        logger.warning('Clash check failed; saving without it', exc_info=True)
        return True
    if not clashes:
        return True

    with form_dialog('Check this time', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('q-pa-md gap-2'):
            ui.label(format_local_display(scheduled_at)).classes('text-bold')
            ui.label('This time might not work:').classes('text-muted')
            with ui.element('ul').classes('wiz-clash-list'):
                for clash in clashes:
                    with ui.element('li'):
                        ui.label(clash.message).classes('wiz-free-text')
            ui.label(
                'Schedule it anyway, or go back and pick another time.'
            ).classes('text-caption text-grey-7')
        with dialog_actions().classes('justify-end'):
            ui.button('Change time', on_click=lambda: dialog.submit(False)).props('flat')
            ui.button(confirm_label, on_click=lambda: dialog.submit(True)).props('color=primary')
    return bool(await dialog)


async def confirm_local_time(
    *,
    tournament_id: Optional[int],
    player_ids: List[int],
    date_value: Optional[str],
    time_value: Optional[str],
    actor: Optional[User],
    exclude_match_id: Optional[int] = None,
    confirm_label: str = 'Schedule anyway',
) -> bool:
    """:func:`confirm_time` for a dialog's date and time fields, as typed."""
    try:
        scheduled_at = parse_local_datetime(date_value or '', time_value or '')
    except ValueError:
        return True
    return await confirm_time(
        tournament_id=tournament_id, player_ids=player_ids, scheduled_at=scheduled_at,
        actor=actor, exclude_match_id=exclude_match_id, confirm_label=confirm_label,
    )
