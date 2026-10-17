"""Email and Telegram rows for the profile's "Where it goes" group, plus "Send a test…".

Each channel is a row with a switch once it is set up, and an Add/Connect
button until then. Setting one up happens in a sheet (a bottom sheet on a
phone, a dialog on desktop). The rows only render when the community has
``FeatureFlag.NOTIFICATION_CHANNELS`` on; the caller checks that. "Send a test…"
shows everywhere, offering email and Telegram only alongside the rows.
"""

from typing import Callable, Optional

from nicegui import ui

from application.services import NotificationChannelService, UserService, WebPushService
from application.services.notification_channel_service import (
    TELEGRAM_LINK_TTL,
    TEST_WINDOW_SECONDS,
    TESTS_PER_WINDOW,
    Channel,
    email_available,
    telegram_available,
)
from models import User
from pages.discord_email_oauth import LINK_ROUTE as DISCORD_EMAIL_ROUTE
from theme.busy import busy, busy_click, is_busy
from theme.dialog._helpers import dialog_actions, dialog_more_menu, form_dialog, submit_on_enter
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.menu import menu_action

_TELEGRAM_POLL_SECONDS = 3


class ChannelRows:
    """The email and Telegram rows; ``any_on()`` feeds the "everything's off" note."""

    def __init__(self, user: User, on_change: Optional[Callable[[], object]] = None) -> None:
        self.user = user
        self.on_change = on_change or (lambda: None)
        self.service = NotificationChannelService()
        self.show_email = email_available()
        self.show_telegram = telegram_available()

    def any_on(self) -> bool:
        return bool(
            (self.show_email and self.user.email and self.user.email_notifications)
            or (self.show_telegram and self.user.telegram_chat_id and self.user.telegram_notifications)
        )

    async def _reload(self) -> None:
        fresh = await UserService().get_user_by_id(self.user.id)
        if fresh is not None:
            self.user = fresh
        self.rows.refresh()
        self.on_change()

    def render(self) -> None:
        self.rows()

    @ui.refreshable_method
    def rows(self) -> None:
        if self.show_email:
            self._email_row()
        if self.show_telegram:
            self._telegram_row()

    # ------------------------------------------------------------ email

    def _email_row(self) -> None:
        user = self.user
        with ui.row().classes('wiz-notify-item no-wrap'):
            ui.icon('mail', size='sm').classes('wiz-notify-item__icon')
            with ui.column().classes('gap-0 col min-w-0'):
                ui.label('Email').classes('wiz-notify-item__name')
                with ui.row().classes('items-center gap-1 no-wrap min-w-0 w-full'):
                    if user.email:
                        ui.label(user.email).classes('text-muted text-caption wiz-notify-item__addr')
                        ui.label('·').classes('text-muted text-caption')
                        ui.button('Change', on_click=self.open_email_sheet) \
                            .props('flat dense no-caps').classes('wiz-notify-item__link')
                    else:
                        ui.label('Not set up').classes('text-muted text-caption')
            if user.email:
                ui.switch(value=user.email_notifications, on_change=self._toggle_email) \
                    .props('aria-label="Email notifications"')
            else:
                ui.button('Add email', on_click=self.open_email_sheet).props('outline no-caps')

    async def _toggle_email(self, e) -> None:
        try:
            await self.service.set_email_enabled(self.user, e.value)
        except ValueError as err:
            ui.notify(str(err), color='warning')
        await self._reload()

    def open_email_sheet(self) -> None:
        user = self.user
        state = {'sent_to': None}

        with form_dialog('Email notifications') as dialog:
            with ui.column().classes('w-full gap-3'):
                if user.discord_id:
                    ui.button(
                        'Use my Discord email', icon='alternate_email',
                        on_click=lambda: ui.navigate.to(DISCORD_EMAIL_ROUTE),
                    ).props('outline no-caps').classes('w-full')
                    ui.label('or use a different address').classes('text-muted text-caption self-center')
                address = ui.input(
                    'Email address', value='' if user.email is None else user.email,
                ).props('outlined type=email autocomplete=email inputmode=email maxlength=254') \
                    .classes('w-full')
                send_button = ui.button('Send code', on_click=lambda: send_code()) \
                    .props('outline no-caps').classes('self-start')

                with ui.column().classes('w-full gap-1') as code_block:
                    sent_label = ui.label('').classes('text-caption')
                    code = ui.input('6-digit code').props(
                        'outlined inputmode=numeric autocomplete=one-time-code maxlength=8',
                    ).classes('w-full')
                    ui.button('Send a new code', on_click=lambda: send_code()) \
                        .props('flat dense no-caps').classes('wiz-notify-item__link self-start')
                code_block.set_visibility(False)

                ui.label(
                    "You'll get an email for the same things you'd get as a Discord DM. "
                    'Every email has a one-click unsubscribe link.'
                ).classes('text-muted text-caption')

            with dialog_actions():
                if user.email:
                    with dialog_more_menu():
                        menu_action('delete', 'Remove email…', lambda: confirm_remove(), negative=True)
                ui.space()
                ui.button('Cancel', on_click=dialog.close).props('flat')
                confirm_button = ui.button('Confirm', on_click=lambda: confirm(), color='primary')
                confirm_button.disable()
            submit_on_enter(dialog, lambda: confirm() if state['sent_to'] else send_code())

        async def send_code() -> None:
            if is_busy(send_button):
                return
            try:
                async with busy(send_button, 'Sending…'):
                    sent_to = await self.service.start_email_verification(user, address.value or '')
            except ValueError as err:
                ui.notify(str(err), color='warning')
                return
            state['sent_to'] = sent_to
            sent_label.set_text(f'We sent a 6-digit code to {sent_to}. Type it here to confirm.')
            code_block.set_visibility(True)
            send_button.set_visibility(False)
            confirm_button.enable()
            code.run_method('focus')

        async def confirm() -> None:
            try:
                email = await self.service.confirm_email(user, code.value or '')
            except ValueError as err:
                ui.notify(str(err), color='warning')
                return
            dialog.close()
            ui.notify(f'Email notifications on for {email}.', color='positive', icon='mark_email_read')
            await self._reload()

        def confirm_remove() -> None:
            async def do_remove() -> None:
                try:
                    await self.service.remove_email(user)
                except ValueError as err:
                    ui.notify(str(err), color='warning')
                    return
                dialog.close()
                ui.notify('Email removed.', color='positive', icon='check_circle')
                await self._reload()

            ConfirmationDialog(
                message='Remove your email? You\'ll stop getting notifications there.',
                on_confirm=do_remove, confirm_text='Remove',
            ).open()

        dialog.open()

    # --------------------------------------------------------- telegram

    def _telegram_row(self) -> None:
        user = self.user
        with ui.row().classes('wiz-notify-item no-wrap'):
            ui.icon('send', size='sm').classes('wiz-notify-item__icon')
            with ui.column().classes('gap-0 col min-w-0'):
                ui.label('Telegram').classes('wiz-notify-item__name')
                with ui.row().classes('items-center gap-1 no-wrap min-w-0 w-full'):
                    if user.telegram_chat_id is not None:
                        who = f'Connected as @{user.telegram_username}' if user.telegram_username else 'Connected'
                        ui.label(who).classes('text-muted text-caption wiz-notify-item__addr')
                        ui.label('·').classes('text-muted text-caption')
                        ui.button('Disconnect', on_click=self._confirm_disconnect) \
                            .props('flat dense no-caps').classes('wiz-notify-item__link')
                    else:
                        ui.label('Not connected').classes('text-muted text-caption')
            if user.telegram_chat_id is not None:
                ui.switch(value=user.telegram_notifications, on_change=self._toggle_telegram) \
                    .props('aria-label="Telegram notifications"')
            else:
                ui.button('Connect', on_click=busy_click('Connecting…', self.open_telegram_sheet)).props('outline no-caps')

    async def _toggle_telegram(self, e) -> None:
        try:
            await self.service.set_telegram_enabled(self.user, e.value)
        except ValueError as err:
            ui.notify(str(err), color='warning')
        await self._reload()

    def _confirm_disconnect(self) -> None:
        async def do_disconnect() -> None:
            try:
                await self.service.disconnect_telegram(self.user)
            except ValueError as err:
                ui.notify(str(err), color='warning')
                return
            ui.notify('Telegram disconnected.', color='positive', icon='check_circle')
            await self._reload()

        ConfirmationDialog(
            message="Disconnect Telegram? You'll stop getting notifications there.",
            on_confirm=do_disconnect, confirm_text='Disconnect',
        ).open()

    async def open_telegram_sheet(self) -> None:
        try:
            link = await self.service.create_telegram_link(self.user)
        except ValueError as err:
            ui.notify(str(err), color='warning')
            return
        if link is None:
            ui.notify('Telegram connected (mock).', color='positive', icon='check_circle')
            await self._reload()
            return

        with form_dialog('Connect Telegram') as dialog:
            with ui.column().classes('w-full gap-3'):
                ui.label(
                    "Open Telegram and press Start. This page updates by itself once you're connected."
                )
                for n, step in enumerate(('Tap Open Telegram below', 'Press Start in the chat with the Wizzrobe bot'), 1):
                    with ui.row().classes('items-center gap-2 no-wrap'):
                        ui.badge(str(n)).props('rounded color=primary')
                        ui.label(step)
                minutes = int(TELEGRAM_LINK_TTL.total_seconds() // 60)
                ui.label(
                    'No phone number needed. The link only works for your account and expires in '
                    f'{minutes} minutes. Send /stop in the chat any time to turn it off.'
                ).classes('text-muted text-caption')
            with dialog_actions():
                ui.space()
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Open Telegram', icon='open_in_new', color='primary') \
                    .props(f'href="{link}" target=_blank rel=noopener no-caps')

        polls = {'left': int(TELEGRAM_LINK_TTL.total_seconds() // _TELEGRAM_POLL_SECONDS)}

        async def check() -> None:
            polls['left'] -= 1
            fresh = await UserService().get_user_by_id(self.user.id)
            if fresh is not None and fresh.telegram_chat_id is not None:
                timer.deactivate()
                dialog.close()
                ui.notify('Telegram connected.', color='positive', icon='check_circle')
                await self._reload()
            elif polls['left'] <= 0:
                timer.deactivate()

        timer = ui.timer(_TELEGRAM_POLL_SECONDS, check)
        dialog.on('hide', lambda: timer.deactivate())
        dialog.open()


async def render_test_button(
    user: User, rows: Optional[ChannelRows], device_count: Callable[[], int],
) -> None:
    """The "Send a test…" line under the channels, opening a pick-where sheet."""
    with ui.row().classes('wiz-notify-item items-center no-wrap'):
        ui.label('Check it works: we send a test to the places you pick.') \
            .classes('text-muted text-caption col')
        ui.button('Send a test…', icon='send', on_click=lambda: _open_test_sheet(user, rows, device_count())) \
            .props('outline no-caps')


def _open_test_sheet(user: User, rows: Optional[ChannelRows], devices: int) -> None:
    if rows is not None:
        user = rows.user
    options: list[tuple[Channel, str, bool, bool]] = []
    if user.discord_id:
        options.append((Channel.DISCORD, 'Discord DMs', True, user.dm_notifications))
    if WebPushService().is_configured():
        label = 'Your devices' if devices else 'Your devices (none turned on)'
        options.append((Channel.DEVICES, label, devices > 0, devices > 0))
    if rows is not None and rows.show_email:
        ready = bool(user.email)
        options.append((Channel.EMAIL, 'Email' if ready else 'Email (not set up)', ready,
                        ready and user.email_notifications))
    if rows is not None and rows.show_telegram:
        ready = user.telegram_chat_id is not None
        options.append((Channel.TELEGRAM, 'Telegram' if ready else 'Telegram (not connected)', ready,
                        ready and user.telegram_notifications))

    boxes: dict[Channel, ui.checkbox] = {}
    with form_dialog('Send a test to', card_classes='dialog-card dialog-card--sm') as dialog:
        with ui.column().classes('w-full gap-1'):
            for channel, label, enabled, checked in options:
                box = ui.checkbox(label, value=checked)
                if not enabled:
                    box.disable()
                boxes[channel] = box
            minutes = TEST_WINDOW_SECONDS // 60
            ui.label(f'Up to {TESTS_PER_WINDOW} tests every {minutes} minutes.') \
                .classes('text-muted text-caption q-mt-sm')
        with dialog_actions():
            ui.space()
            ui.button('Cancel', on_click=dialog.close).props('flat')
            send = ui.button('Send test', on_click=lambda: do_send(), color='primary')

    names = {channel: label.split(' (')[0] for channel, label, _, _ in options}

    async def do_send() -> None:
        chosen = [c for c, box in boxes.items() if box.value]
        if is_busy(send):
            return
        try:
            async with busy(send, 'Sending…'):
                results = await NotificationChannelService().send_test(user, chosen)
        except ValueError as err:
            ui.notify(str(err), color='warning')
            return
        dialog.close()
        sent = [names[c] for c, ok in results.items() if ok]
        failed = [names[c] for c, ok in results.items() if not ok]
        if sent:
            ui.notify(f'Test sent to {_join(sent)}.', color='positive', icon='check_circle')
        if failed:
            ui.notify(f"Couldn't reach {_join(failed)}. Check that it's set up and turned on.",
                      color='warning', multi_line=True)

    dialog.open()


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return ''.join(items)
    return ', '.join(items[:-1]) + ' and ' + items[-1]
