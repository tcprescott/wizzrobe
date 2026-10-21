"""Messages sent to you: your own notification history, one community at a time.

Layout C (Thomas, 2026-10-21): a list of what Wizzrobe sent you, filterable by
kind, where tapping a message opens it as it was sent (a side sheet on a
desktop, a bottom sheet on a phone) with an Off / Silent / Ping switch for that
kind of message underneath. Every catalogued DM's footnote links here with
``?message=<id>``, so the switch is one tap from the message itself.

Members only, and only your own messages: the rows come from the staff Sent
messages log through :meth:`SentMessageService.list_mine`, which filters to the
viewer.
"""

from typing import Dict, List, Optional

from nicegui import app, ui

from application.services import (
    AuthService,
    NotificationPreferenceService,
    SentMessageService,
    TenantService,
    get_user_from_session,
)
from application.services.notification_catalog import CATALOG
from application.services.notification_preference_service import MyNotificationRow, quieter
from application.utils.app_links import MY_MESSAGES
from application.utils.dm_text import readable_dm_text
from application.utils.timezone import format_local_datetime, format_local_time, to_local, today_local
from middleware.auth import protected_page
from models import NotificationMode, SentMessage, SentMessageStatus, User
from pages.home_tabs.notification_preferences_section import PRESET_NAMES
from theme.base import BaseLayout
from theme.dialog._helpers import dialog_header
from theme.empty_state import empty_panel
from theme.notify import notify_error
from theme.page_header import page_header
from theme.section import section_panel
from theme.steady import steady_refreshable

__all__ = ['create']

PAGE_SIZE = 30
_ALL = 'all'
OFF, SILENT, PING = NotificationMode.OFF, NotificationMode.SILENT, NotificationMode.PING
_LABELS = {OFF: 'Off', SILENT: 'Silent', PING: 'Ping'}
_ICONS = {PING: 'notifications_active', SILENT: 'notifications_off', OFF: 'block'}
_GROUP_ICONS = {
    'Matches': 'sports_esports',
    'Brackets': 'account_tree',
    'Reschedule requests': 'event_repeat',
    'Crew': 'mic',
    'Volunteer shifts': 'badge',
    'Community': 'groups',
    'Event check-in': 'how_to_reg',
    'Async qualifiers': 'timer',
}
_EMBED_SPACER = '​'


def _when(m: SentMessage) -> str:
    local = to_local(m.created_at)
    if local and local.date() == today_local():
        return f'Today {format_local_time(m.created_at)}'
    return f"{format_local_datetime(m.created_at, '%a %d %b')} {format_local_time(m.created_at)}"


def _channels(m: SentMessage) -> str:
    reached = []
    if m.status is SentMessageStatus.DELIVERED:
        reached.append('Discord')
    if m.push_devices:
        reached.append('Phone')
    if m.email_sent:
        reached.append('Email')
    if m.telegram_sent:
        reached.append('Telegram')
    return ' · '.join(reached) if reached else 'Not delivered'


def _title(m: SentMessage) -> str:
    return readable_dm_text(m.title or '').strip() or 'Message'


def _preview(m: SentMessage) -> str:
    text = readable_dm_text(m.body).strip()
    title = _title(m)
    if text.startswith(title):
        text = text[len(title):].lstrip(' :.\n')
    return text.splitlines()[0][:160] if text else ''


def _row_for(m: SentMessage, rows: Dict[tuple, MyNotificationRow]) -> Optional[MyNotificationRow]:
    if not m.notice_key or not m.audience:
        return None
    return rows.get((m.notice_key, m.audience))


def _mode_chip(row: Optional[MyNotificationRow]) -> None:
    if row is None:
        return
    mode = row.effective
    with ui.element('span').classes(f'wiz-notify-cell wiz-notify-cell--{mode.value} wiz-inbox__mode'):
        ui.icon(_ICONS[mode]).props('aria-hidden=true')
        ui.label(_LABELS[mode])


def _message_card(m: SentMessage) -> None:
    """The message the way the reader got it: the card's title, text and fields."""
    embed = m.embed or {}
    with ui.column().classes('wiz-inbox__dm gap-2 full-width'):
        description = embed.get('description') if embed else None
        fields = [f for f in (embed.get('fields') or []) if f.get('name') != _EMBED_SPACER]
        if not embed:
            ui.label(readable_dm_text(m.body).strip()).classes('wiz-inbox__text')
        if description:
            ui.label(readable_dm_text(description).strip()).classes('wiz-inbox__text')
        for field in fields:
            with ui.column().classes('gap-0'):
                ui.label(readable_dm_text(field.get('name') or '')).classes('wiz-inbox__field-name')
                ui.label(readable_dm_text(field.get('value') or '')).classes('wiz-inbox__text')


class MessageSheet:
    """One message, opened from the list or straight from a DM's footnote."""

    def __init__(self, user: User, on_changed) -> None:
        self.user = user
        self.on_changed = on_changed
        self.message: Optional[SentMessage] = None
        self.row: Optional[MyNotificationRow] = None
        self.preset_name = ''
        with ui.dialog().props(''':position="Quasar.Screen.lt.sm ? 'bottom' : 'right'"''') as self.dialog, \
                ui.card().classes('dialog-card match-sheet wiz-inbox-sheet'):
            dialog_header('Message', self.dialog)
            self._content = ui.refreshable(self._render)
            self._content()

    def open(self, message: SentMessage, row: Optional[MyNotificationRow], preset_name: str) -> None:
        self.message, self.row, self.preset_name = message, row, preset_name
        with self.dialog:
            self._content.refresh()
            self.dialog.open()

    async def _pick(self, mode: Optional[NotificationMode]) -> None:
        row = self.row
        if row is None:
            return
        try:
            await NotificationPreferenceService.set_mode(self.user, row.target.key, row.target.recipient, mode)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        self.row = await self.on_changed(row)
        ui.notify('Saved', color='positive', timeout=1200)
        self._content.refresh()

    def _render(self) -> None:
        m = self.message
        if m is None:
            return
        with ui.column().classes('match-sheet__body full-width gap-3'):
            with ui.column().classes('gap-0'):
                ui.label(_title(m)).classes('wiz-inbox__sheet-title')
                ui.label(f'{_when(m)} · {_channels(m)}').classes('text-caption text-muted')
            _message_card(m)
            if m.link_url:
                link = ui.button(m.link_label or 'Open', icon='open_in_new').props(
                    'no-caps color=primary target=_blank rel=noopener',
                )
                link.props['href'] = m.link_url
            self._switch(m)

    def _switch(self, m: SentMessage) -> None:
        row = self.row
        notice = CATALOG.get(m.notice_key or '')
        with ui.column().classes('wiz-inbox__switch gap-1 full-width'):
            if notice is None or row is None:
                ui.label("This message can't be turned off: it's sent to everyone it's about.") \
                    .classes('text-caption text-muted')
                return
            ui.label(f'How you get “{notice.title}”').classes('wiz-notify-item__name')
            if row.community is OFF:
                ui.label('Your community turned this one off for everyone.').classes('text-caption text-muted')
                return
            can_ping = row.community is PING
            options = {OFF.value: 'Off', SILENT.value: 'Silent'}
            if can_ping:
                options[PING.value] = 'Ping'
            toggle = ui.toggle(
                options, value=row.effective.value,
                on_change=lambda e: self._pick(NotificationMode(e.value)),
            ).props('no-caps dense unelevated').classes('wiz-segmented')
            toggle.props['aria-label'] = f'How you get {notice.title}'
            ui.label(
                'You can make it quieter. It takes effect from the next one.' if can_ping
                else "Your community sends this one quietly, so it can't ping."
            ).classes('text-caption text-muted')
            if row.changed:
                back = quieter(row.community, row.preset)
                ui.button(
                    f'Back to {self.preset_name}: {_LABELS[back]}', icon='undo',
                    on_click=lambda: self._pick(None), color=None,
                ).props('flat dense no-caps')


def create() -> None:
    @protected_page(MY_MESSAGES)
    async def my_messages(message: Optional[str] = None) -> None:
        user = await get_user_from_session(app.storage.user)
        if user is None:
            ui.label('Your account could not be found.').classes('text-error')
            return
        community = await TenantService.current_community_name() or 'Wizzrobe'
        ui.page_title(f'Messages sent to you — {community}')
        await BaseLayout(user=user, show_admin=await AuthService.can_open_admin(user)).render()

        service = SentMessageService()
        mine = await NotificationPreferenceService.get_mine(user)
        state: dict = {
            'group': _ALL,
            'limit': PAGE_SIZE,
            'rows': {(r.target.key, r.target.recipient.value): r for r in mine.rows},
            'preset': mine.preset,
        }
        groups = await service.my_groups(actor=user)
        async def changed(row: MyNotificationRow) -> Optional[MyNotificationRow]:
            fresh = await NotificationPreferenceService.get_mine(user)
            state['rows'] = {(r.target.key, r.target.recipient.value): r for r in fresh.rows}
            await listing.refresh()
            return state['rows'].get((row.target.key, row.target.recipient.value))

        sheet = MessageSheet(user, changed)

        def open_message(m: SentMessage) -> None:
            sheet.open(m, _row_for(m, state['rows']), PRESET_NAMES[state['preset']])

        with ui.column().classes('page-container-narrow w-full gap-3'):
            ui.link('← Notification settings', '/home/profile?focus=notifications').classes('text-caption')
            page_header('Messages sent to you')
            panel = await section_panel(
                'Messages', icon='mail',
                subtitle=f'What Wizzrobe sent you in {community}. Open one to change how you get it.',
            )
            with panel.body:
                if groups:
                    options = {_ALL: 'All messages', **{g: g for g in groups}}
                    ui.select(
                        options, value=_ALL, label='Show',
                        on_change=lambda e: pick_group(e.value),
                    ).props('outlined dense').classes('wiz-inbox__filter')

                @steady_refreshable
                async def listing() -> None:
                    group = None if state['group'] == _ALL else state['group']
                    messages: List[SentMessage] = await service.list_mine(
                        actor=user, group=group, limit=state['limit'],
                    )
                    total = await service.count_mine(actor=user, group=group)
                    if not messages:
                        empty_panel("Nothing's been sent to you here yet.", icon='mail')
                        return
                    with ui.column().classes('wiz-inbox gap-0 full-width'):
                        for m in messages:
                            _message_row(m, _row_for(m, state['rows']), open_message)
                    if total > len(messages):
                        async def more() -> None:
                            state['limit'] += PAGE_SIZE
                            await listing.refresh()
                        ui.button('Show older', icon='expand_more', on_click=more, color=None) \
                            .props('flat no-caps').classes('wiz-inbox__more')

                async def pick_group(value: str) -> None:
                    state['group'] = value
                    state['limit'] = PAGE_SIZE
                    await listing.refresh()

                await listing()

        if message:
            target = None
            if message.isdigit():
                target = await service.get_mine(int(message), actor=user)
            if target is None:
                ui.notify(
                    "That message isn't in your history any more. Here's everything else you were sent.",
                    color='info', multi_line=True,
                )
            else:
                open_message(target)


def _message_row(m: SentMessage, row: Optional[MyNotificationRow], on_open) -> None:
    notice = CATALOG.get(m.notice_key or '')
    icon = _GROUP_ICONS.get(notice.group, 'mail') if notice else 'mail'
    item = ui.element('div').classes('wiz-inbox__row').props('role=button tabindex=0')
    item.props['aria-label'] = f'{_title(m)}, {_when(m)}. Open message'
    for event in ('click', 'keydown.enter', 'keydown.space.prevent'):
        item.on(event, lambda _=None, msg=m: on_open(msg))
    with item:
        with ui.element('span').classes('wiz-inbox__icon'):
            ui.icon(icon).props('aria-hidden=true')
        with ui.column().classes('gap-0 col min-w-0'):
            ui.label(_title(m)).classes('wiz-inbox__title ellipsis')
            preview = _preview(m)
            if preview:
                ui.label(preview).classes('wiz-inbox__preview ellipsis')
            ui.label(f'{_when(m)} · {_channels(m)}').classes('text-caption text-muted')
        _mode_chip(row)
