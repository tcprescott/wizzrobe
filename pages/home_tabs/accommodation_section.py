"""The ADA accommodation card on a member's profile.

A checkbox and, once it is ticked, an optional details box and the status staff
have given the request. Per community: the card reads and writes the request
for the community being viewed, never another one's.

Ticking the checkbox commits at once; unticking an open request asks first,
because it withdraws the request and clears its details. The details box saves on blur and after a short
pause in typing, the same autosave shape as the personal-info fields above it.
Staff notes are never shown here.

Presentation only: service calls, no ORM.
"""

import asyncio

from nicegui import ui
from nicegui.events import ValueChangeEventArguments

from application.services import AccommodationService, TenantService
from application.services.accommodation_service import DETAILS_MAX_LENGTH
from models import AccommodationRequest, AccommodationStatus, User
from theme.accommodation_copy import STATUS_CHIP, STATUS_LABELS, privacy_disclaimer
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.help import help_icon


def _is_open(request: AccommodationRequest | None) -> bool:
    return request is not None and request.status is not AccommodationStatus.WITHDRAWN


async def render_accommodation_section(user: User) -> None:
    service = AccommodationService()
    try:
        current = {'request': await service.get_mine(user)}
    except ValueError:
        return
    community = await TenantService.current_community_name()
    typing = {'n': 0, 'dirty': False}
    shown = {'ticked': _is_open(current['request'])}

    async def save() -> None:
        typing['dirty'] = False
        try:
            current['request'] = await service.set_my_request(
                user, shown['ticked'], details_input.value,
            )
        except ValueError as e:
            ui.notify(str(e), color='warning')
            return
        ui.notify('Saved', color='positive', timeout=1200)
        status_row.refresh()

    async def apply_toggle() -> None:
        typing['n'] += 1
        if not shown['ticked']:
            details_input.value = ''
        await save()
        details_block.set_visibility(shown['ticked'])

    def show(ticked: bool) -> None:
        # Setting the value fires on_change again: record what the box should show first so the echo recognises itself.
        shown['ticked'] = ticked
        requested_box.value = ticked

    async def withdraw() -> None:
        show(False)
        await apply_toggle()

    async def on_toggle(e: ValueChangeEventArguments) -> None:
        if e.value == shown['ticked']:
            return
        if e.value or not _is_open(current['request']):
            shown['ticked'] = e.value
            await apply_toggle()
            return
        show(True)
        ConfirmationDialog(
            title='Withdraw your request?',
            message="Your details will be cleared and staff will see the request as "
                    "withdrawn. You can ask again any time, but staff will review it "
                    "from the start.",
            confirm_text='Withdraw request',
            cancel_text='Keep request',
            on_confirm=withdraw,
        ).open()

    async def on_typing() -> None:
        typing['dirty'] = True
        typing['n'] += 1
        mine = typing['n']
        await asyncio.sleep(1.2)
        if mine == typing['n'] and typing['dirty']:
            await save()

    async def on_blur() -> None:
        if typing['dirty']:
            typing['n'] += 1
            await save()

    with ui.card().classes('card-full-width'):
        with ui.row().classes('items-center gap-1 no-wrap wiz-help-row'):
            ui.label('ADA accommodation').classes('section-title')
            await help_icon('ada-request', user=user)
        ui.label(
            "Let staff know you'd like an accommodation at this community's events."
        ).classes('text-muted text-caption')

        requested_box = ui.checkbox(
            "I'd like to request an ADA accommodation",
            value=shown['ticked'],
            on_change=on_toggle,
        )

        with ui.column().classes('w-full gap-2 q-mt-xs') as details_block:
            details_input = ui.textarea(
                'Details (optional)',
                value=(current['request'].details or '') if _is_open(current['request']) else '',
                placeholder='e.g. step-free access to the stage, a seat near an exit, '
                            'extra time between matches',
                on_change=on_typing,
            ).props(f'outlined autogrow stack-label counter maxlength={DETAILS_MAX_LENGTH}') \
                .classes('input-full-width')
            details_input.on('blur', on_blur)

            with ui.row().classes('items-start gap-2 no-wrap'):
                ui.icon('privacy_tip', size='sm').classes('text-warning')
                ui.label(privacy_disclaimer(community)).classes('text-caption text-warning col')

            @ui.refreshable
            def status_row() -> None:
                request = current['request']
                if not _is_open(request):
                    return
                with ui.row().classes('items-center gap-2'):
                    ui.label('Staff status:').classes('text-caption text-muted')
                    with ui.element('span').classes(f'wiz-chip {STATUS_CHIP[request.status]}'):
                        ui.label(STATUS_LABELS[request.status])
                if request.changed_since_arranged:
                    ui.label(
                        'Staff have been told about your change. What they arranged '
                        'stays in place while they check it.'
                    ).classes('text-caption text-muted')

            status_row()
        details_block.set_visibility(requested_box.value)
