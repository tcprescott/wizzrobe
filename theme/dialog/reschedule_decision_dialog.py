"""Staff's answer to a player's reschedule request.

Everything needed to decide is on the card, because the alternative is closing
it to go and look: what the match is, when it is now, when they want it, why,
whether the opponent agreed, and what else either player is committed to around
the proposed time.

Approving here *performs* the change — the service calls ``update_match`` /
``cancel_match`` — so the buttons say so rather than saying "Save".
"""

from typing import Callable, Optional

from nicegui import ui

from application.services import MatchRescheduleService, PlayerAvailabilityService
from application.utils.timezone import (
    format_local_display,
    parse_local_datetime,
    to_local,
)
from models import (
    MatchRescheduleRequest,
    RescheduleRequestKind,
    User,
    VolunteerAvailabilityStatus,
)
from theme.dialog._helpers import (
    dialog_actions,
    form_dialog,
    native_date_input,
    native_time_input,
)
from theme.dialog.clash_warning import confirm_time
from theme.notify import notify_error

_DEFAULT_MATCH_MINUTES = 90


class RescheduleDecisionDialog:
    """Approve (optionally at a different time) or decline, with a note."""

    def __init__(
        self,
        request: MatchRescheduleRequest,
        actor: User,
        *,
        on_decision: Optional[Callable] = None,
    ) -> None:
        self.request = request
        self.actor = actor
        self.on_decision = on_decision
        self.service = MatchRescheduleService()

    async def open(self) -> None:
        request = self.request
        match = request.match
        cancel = request.kind is RescheduleRequestKind.CANCEL
        players = list(match.players)
        names = ' vs '.join(p.user.preferred_name for p in players)

        conflicts = await self._conflicts(request, players)

        local = to_local(request.proposed_at)
        default_date = local.strftime('%Y-%m-%d') if local else ''
        default_time = local.strftime('%H:%M') if local else ''

        title = 'Cancellation request' if cancel else 'Reschedule request'
        with form_dialog(title) as dialog:
            with ui.column().classes('q-pa-md gap-3 full-width'):
                ui.label(f'{match.tournament.name} — {names}').classes('text-bold')
                ui.label(
                    f'Asked by {request.requested_by.preferred_name}'
                ).classes('text-caption text-grey-7')

                with ui.column().classes('gap-1 full-width'):
                    ui.label(
                        f'Currently: {format_local_display(request.original_scheduled_at or match.scheduled_at)}'
                    )
                    if cancel:
                        ui.label('Asking to: call the match off').classes('text-bold')
                    elif request.proposed_at:
                        ui.label(
                            f'Proposed: {format_local_display(request.proposed_at)}'
                        ).classes('text-bold')
                    else:
                        ui.label(
                            'Proposed: no specific time — they just cannot make this one'
                        ).classes('text-bold')

                with ui.card().classes('full-width q-pa-sm'):
                    ui.label('Their reason').classes('text-caption text-grey-7')
                    ui.label(request.reason).classes('wiz-free-text')

                if request.opponent_agreed_at:
                    with ui.row().classes('items-center gap-1'):
                        ui.icon('check_circle', color='positive')
                        ui.label('The other player has agreed').classes('text-positive')
                else:
                    ui.label(
                        'The other player has not agreed yet. You can still decide.'
                    ).classes('text-caption text-grey-7')

                for line in conflicts:
                    with ui.row().classes('items-center gap-1'):
                        ui.icon('warning', color='warning')
                        ui.label(line).classes('text-warning')

                if not cancel:
                    ui.separator()
                    ui.label(
                        'Approve at this time, or change it — either way they are told '
                        'which time they got.'
                    ).classes('text-caption text-grey-7')
                    with ui.row().classes('form-pair gap-2'):
                        date_input = native_date_input('New date', default_date)
                        time_input = native_time_input('New time', default_time)

                note_input = ui.textarea(
                    label='Note to the player',
                    placeholder='Required when declining. Shown to them either way.',
                ).classes('full-width')

            async def approve() -> None:
                scheduled_at = None
                if not cancel:
                    date_value = (date_input.value or '').strip()
                    time_value = (time_input.value or '').strip()
                    if not (date_value and time_value):
                        ui.notify(
                            'Pick a date and a time to approve this at.', color='warning',
                        )
                        return
                    try:
                        scheduled_at = parse_local_datetime(date_value, time_value)
                    except ValueError as e:
                        ui.notify(str(e), color='warning')
                        return
                    if not await confirm_time(
                        tournament_id=match.tournament_id,
                        player_ids=[p.user_id for p in players],
                        scheduled_at=scheduled_at, actor=self.actor,
                        exclude_match_id=match.id, confirm_label='Move it anyway',
                    ):
                        return
                try:
                    await self.service.approve(
                        request.id, self.actor,
                        scheduled_at=scheduled_at,
                        note=(note_input.value or '').strip() or None,
                    )
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify(
                    'Match cancelled and everyone told.' if cancel
                    else 'Match moved and everyone told.',
                    color='positive',
                )
                await self._finish(dialog)

            async def decline() -> None:
                note = (note_input.value or '').strip()
                if not note:
                    ui.notify(
                        'Say why — they only ever see this note.', color='warning',
                    )
                    return
                try:
                    await self.service.decline(request.id, self.actor, note)
                except (ValueError, PermissionError) as e:
                    notify_error(e)
                    return
                ui.notify('Declined, and they have been told why.', color='positive')
                await self._finish(dialog)

            with dialog_actions().classes('justify-end'):
                ui.button('Decline', on_click=decline).props('flat color=negative')
                ui.button(
                    'Cancel the match' if cancel else 'Approve and move it',
                    on_click=approve,
                ).props('color=primary')

        dialog.open()

    async def _finish(self, dialog) -> None:
        dialog.close()
        if self.on_decision:
            await self.on_decision()

    @staticmethod
    async def _conflicts(request: MatchRescheduleRequest, players) -> list:
        """What either player has said about the proposed window.

        Read-only and advisory, like the crew-approval conflict check it mirrors:
        it names what staff would otherwise have to go and look up, and blocks
        nothing.
        """
        if request.proposed_at is None:
            return []
        from datetime import timedelta

        minutes = request.match.tournament.average_match_duration or _DEFAULT_MATCH_MINUTES
        start = request.proposed_at
        end = start + timedelta(minutes=minutes)
        user_ids = [p.user_id for p in players]
        windows = await PlayerAvailabilityService().availability_map(user_ids, start, end)

        lines = []
        for player in players:
            status = PlayerAvailabilityService.covers(
                windows.get(player.user_id, []), start, end,
            )
            if status == VolunteerAvailabilityStatus.UNAVAILABLE:
                lines.append(
                    f'{player.user.preferred_name} blocked that time out.'
                )
        return lines
