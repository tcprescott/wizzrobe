"""A player telling staff they're running late: how far out, and a short note.

Opened from the Running late button on the player's own match row (My Schedule,
Event > Schedule) and from the stage reminder DM's link. The choices are the
volunteer ones, 10, 20 or 30 minutes; anything worse is a reschedule, so the
dialog points there instead of offering a bigger number.
"""

from typing import Awaitable, Callable, Optional

from nicegui import ui

from application.services import PlayerLateService
from application.services.match.player_late_service import MAX_LATE_NOTE
from application.utils.discord_messages_volunteer import LATE_CHOICES
from application.utils.timezone import format_local_time
from models import Match, User
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.notify import notify_error

STAFF_ONLY_NOTE = "Staff on duty see this. Your opponent doesn't."


def _choice_label(minutes: int) -> str:
    return f'{minutes}+ min' if minutes >= LATE_CHOICES[-1] else f'{minutes} min'


class RunningLateDialog:
    def __init__(
        self,
        match: Match,
        actor: User,
        *,
        on_sent: Optional[Callable[[int], Awaitable[None]]] = None,
        on_ask_to_change: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self.match = match
        self.actor = actor
        self.on_sent = on_sent
        self.on_ask_to_change = on_ask_to_change

    def _subtitle(self) -> str:
        names = ' vs '.join(p.user.preferred_name for p in self.match.players)  # type: ignore[attr-defined]
        parts = [names] if names else []
        if self.match.scheduled_at:
            parts.append(format_local_time(self.match.scheduled_at))
        mine = next(
            (p for p in self.match.players if p.user_id == self.actor.id), None,  # type: ignore[attr-defined]
        )
        if self.match.stage:
            parts.append(self.match.stage.name)
        elif mine and mine.assigned_station:
            parts.append(f'Station {mine.assigned_station}')
        return ' · '.join(parts)

    async def _ask(self, dialog) -> None:
        dialog.close()
        if self.on_ask_to_change is not None:
            await self.on_ask_to_change()

    async def open(self) -> None:
        mine = next(
            (p for p in self.match.players if p.user_id == self.actor.id), None,  # type: ignore[attr-defined]
        )
        state = {'minutes': (mine.late_minutes if mine and mine.late_minutes else LATE_CHOICES[1])}

        with form_dialog('Running late', card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('q-pa-md gap-3 full-width'):
                ui.label(self._subtitle()).classes('text-caption text-grey-7')
                # url-state: exempt — a form choice inside a dialog
                ui.toggle(
                    {m: _choice_label(m) for m in LATE_CHOICES}, value=state['minutes'],
                    on_change=lambda e: state.update(minutes=e.value),
                ).props('spread no-caps unelevated toggle-color=primary aria-label="How late"') \
                    .classes('wiz-segmented full-width running-late__choices')
                note = ui.input(
                    'Anything staff should know? (optional)',
                    value=(mine.late_note if mine else '') or '',
                ).props(f'outlined dense maxlength={MAX_LATE_NOTE} counter').classes('full-width')
                with ui.row().classes('items-center gap-1 text-caption'):
                    ui.label(STAFF_ONLY_NOTE)
                    if self.on_ask_to_change is not None:
                        ui.label("Can't make it at all?")
                        ui.button('Ask to change', on_click=lambda: self._ask(dialog)) \
                            .props('flat dense no-caps color=primary padding="0 4px"')

            async def send() -> None:
                try:
                    await PlayerLateService().report_late(
                        self.match.id, self.actor, state['minutes'], note.value,
                    )
                except ValueError as e:
                    notify_error(e)
                    return
                ui.notify("Staff know you're on your way.", color='positive')
                dialog.close()
                if self.on_sent is not None:
                    await self.on_sent(state['minutes'])

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat no-caps')
                ui.button('Tell staff', on_click=busy_click('Sending…', send)) \
                    .props('color=primary no-caps')
        dialog.open()
