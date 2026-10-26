"""Everything this community knows about one member, in one sheet.

Admin → Members opens this when a name is clicked: a side sheet on a desktop,
a bottom sheet on a phone, like the match and shift sheets, so staff can check
one person and move straight on to the next. It is mostly for troubleshooting
("why didn't they get told?"): how DMs reach them, what each message does for
them once the community's setting and their own choice are combined, and what
was actually sent.

The emergency contact is never drawn here. The sheet offers the same audited
"Emergency contact…" the row ⋮ does, so every look is still logged.
"""

from typing import Awaitable, Callable, Optional

from nicegui import app, ui

from application.services import MemberDetails, MemberDetailsService, get_user_from_session
from application.services.member_details_service import NoticeDetail
from application.utils.app_links import REPORTS, admin_url
from application.utils.timezone import format_local_date, format_local_display, timezone_label
from models import NotificationMode
from pages.admin_tabs.reports.messages import STATUS_CHIP
from theme.dialog._helpers import dialog_actions, dialog_header, dialog_more_menu
from theme.menu import menu_action
from theme.notify import notify_error

OFF, SILENT, PING = NotificationMode.OFF, NotificationMode.SILENT, NotificationMode.PING
_MODE_ICONS = {PING: 'notifications_active', SILENT: 'notifications_off', OFF: 'block'}
_MODE_LABELS = {PING: 'Ping', SILENT: 'Silent', OFF: 'Off'}

_JOINED_VIA = {
    'staff': 'added by staff',
    'join_request': 'by join request',
    'discord_auto_join': 'via Discord',
    'open': 'openly',
    'role_grant': 'by a role grant',
    'discord_role': 'by Discord role sync',
    'import': 'by SpeedGaming import',
    'discord_import': 'by Discord import',
}

_EMAIL = {'on': 'On', 'off': 'Off', 'unverified': 'Not verified', 'none': 'No address'}
_TELEGRAM = {'on': 'On', 'off': 'Off', 'none': 'Not linked'}

MemberAction = Callable[[int], Awaitable[None]]


def _mode_chip(mode: NotificationMode) -> None:
    with ui.element('span').classes(f'wiz-notify-cell wiz-notify-cell--{mode.value}'):
        ui.icon(_MODE_ICONS[mode]).props('aria-hidden=true')
        ui.label(_MODE_LABELS[mode])


def _channel(icon: str, name: str, state: str, tone: str) -> None:
    with ui.element('div').classes('member-sheet__channel'):
        with ui.row().classes('items-center no-wrap gap-2'):
            ui.icon(icon).classes(tone).props('aria-hidden=true')
            ui.label(name).classes('text-weight-bold')
        ui.label(state).classes('text-caption text-muted')


def _fact(label: str, value: str) -> None:
    ui.label(label).classes('text-muted')
    ui.label(value).style('overflow-wrap: anywhere')


def _filter_props(selected: bool) -> str:
    state = 'unelevated color=primary aria-pressed=true' if selected else 'outline color=primary aria-pressed=false'
    return f'dense no-caps rounded {state}'


def _section(title: str) -> None:
    ui.label(title).classes('match-sheet__section')


def _notice_row(notice: NoticeDetail) -> None:
    with ui.row().classes('member-sheet__row items-center no-wrap full-width'):
        with ui.column().classes('gap-0 col').style('min-width: 0'):
            ui.label(notice.title)
            ui.label(
                f'{notice.section} · community: {_MODE_LABELS[notice.community].lower()}, '
                f'theirs: {_MODE_LABELS[notice.theirs].lower()}'
            ).classes('text-caption text-muted')
            if notice.reason:
                ui.label(notice.reason).classes('text-caption member-sheet__reason')
        _mode_chip(notice.effective)


class MemberSheet:
    def __init__(
        self, *, on_edit: MemberAction, on_remove: MemberAction,
        on_emergency_contact: Optional[MemberAction] = None,
    ) -> None:
        self.on_edit = on_edit
        self.on_remove = on_remove
        self.on_emergency_contact = on_emergency_contact
        self.details: Optional[MemberDetails] = None
        self.show_all = False
        with ui.dialog().props(''':position="Quasar.Screen.lt.sm ? 'bottom' : 'right'"''') as self.dialog, \
                ui.card().classes('dialog-card match-sheet member-sheet'):
            dialog_header('Member details', self.dialog)
            self._content = ui.refreshable(self._render)
            self._content()

    async def open(self, member_id: int) -> None:
        actor = await get_user_from_session(app.storage.user)
        try:
            self.details = await MemberDetailsService().get(member_id, actor=actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        self.show_all = False
        with self.dialog:
            self._content.refresh()
            self.dialog.open()

    def _toggle_all(self, value: bool) -> None:
        self.show_all = value
        self._content.refresh()

    async def _act(self, action: MemberAction) -> None:
        if self.details is None:
            return
        member_id = self.details.user.id
        if action is self.on_remove:
            self.dialog.close()
        await action(member_id)

    def _render(self) -> None:
        d = self.details
        if d is None:
            return
        user = d.user
        with ui.column().classes('match-sheet__body full-width'):
            self._identity(d)
            self._channels(d)
            self._notifications(d)
            self._messages(d)
            self._community(d)
            if self.on_emergency_contact is not None:
                with ui.row().classes('member-sheet__contact items-center no-wrap full-width'):
                    ui.icon('contact_phone').props('aria-hidden=true')
                    ui.label('Emergency contact').classes('col')
                    ui.button(
                        'Show…', on_click=lambda: self._act(self.on_emergency_contact),
                    ).props('flat dense no-caps color=primary') \
                        .tooltip('Opening it is recorded in the audit log')

        with dialog_actions():
            with dialog_more_menu():
                if self.on_emergency_contact is not None:
                    menu_action('contact_phone', 'Emergency contact…',
                                lambda: self._act(self.on_emergency_contact))
                    ui.separator()
                menu_action('person_remove', 'Remove from community…',
                            lambda: self._act(self.on_remove), negative=True)
            ui.space()
            ui.button('Edit member', icon='edit', on_click=lambda: self._act(self.on_edit)) \
                .props('no-caps color=primary')
        self.dialog.props(f'aria-label="Details for {user.preferred_name}"')

    def _identity(self, d: MemberDetails) -> None:
        user = d.user
        with ui.row().classes('items-center no-wrap gap-3 full-width'):
            if d.avatar_url:
                ui.image(d.avatar_url).props('width=48px height=48px fit=cover alt=""') \
                    .classes('member-sheet__avatar')
            else:
                ui.icon('account_circle', size='48px').classes('text-muted').props('aria-hidden=true')
            with ui.column().classes('gap-0 col').style('min-width: 0'):
                ui.label(user.preferred_name).classes('match-sheet__title').style('overflow-wrap: anywhere')
                bits = [f'@{user.username}']
                if user.pronouns:
                    bits.append(user.pronouns)
                if d.joined_at:
                    via = _JOINED_VIA.get(d.joined_via.value, '') if d.joined_via else ''
                    bits.append(f'Joined {format_local_date(d.joined_at)} {via}'.strip())
                ui.label(' · '.join(bits)).classes('text-caption text-muted')
        with ui.row().classes('gap-1'):
            if not user.is_active:
                ui.label('Deactivated').classes('wiz-chip wiz-chip--cancelled')
            for role in d.roles:
                ui.label(role.label).classes('wiz-chip wiz-chip--neutral')
            if d.tournament_admin_of:
                ui.label('Tournament admin').classes('wiz-chip wiz-chip--neutral')
            if d.crew_coordinator_of:
                ui.label('Crew coordinator').classes('wiz-chip wiz-chip--neutral')
            if d.entered:
                n = len(d.entered)
                ui.label(f"Player · {n} tournament{'s' if n != 1 else ''}").classes('wiz-chip wiz-chip--neutral')
            if d.check_in and d.check_in.startswith('Checked in'):
                ui.label('Checked in').classes('wiz-chip wiz-chip--ok')

    def _channels(self, d: MemberDetails) -> None:
        c = d.channels
        _section('How messages reach them')
        with ui.element('div').classes('member-sheet__channels'):
            if not c.has_discord:
                _channel('forum', 'Discord DM', 'No Discord account', 'text-muted')
            elif not c.discord_dms:
                _channel('forum', 'Discord DM', 'Turned off in their Profile', 'st-cancelled')
            else:
                last = format_local_display(c.last_delivered_at) if c.last_delivered_at else 'nothing delivered yet'
                _channel('forum', 'Discord DM', f'On · last delivered {last}', 'st-ok')
            devices = c.push_devices
            _channel('smartphone', 'Phone push',
                     f"{devices} device{'s' if devices != 1 else ''}" if devices else 'No devices',
                     'st-ok' if devices else 'text-muted')
            if c.email is not None:
                _channel('mail', 'Email', _EMAIL[c.email], 'st-ok' if c.email == 'on' else 'text-muted')
            if c.telegram is not None:
                _channel('send', 'Telegram', _TELEGRAM[c.telegram],
                         'st-ok' if c.telegram == 'on' else 'text-muted')

    def _notifications(self, d: MemberDetails) -> None:
        _section('Notifications')
        quieter = d.quieter_than_community
        summary = f'Preset: {d.preset_label}'
        if d.changed_from_preset:
            n = d.changed_from_preset
            summary += f" · {n} message{'s' if n != 1 else ''} changed from it"
        ui.label(summary).classes('text-caption')
        with ui.row().classes('gap-2'):
            ui.button(
                f'Quieter than the community ({len(quieter)})',
                on_click=lambda: self._toggle_all(False),
            ).props(_filter_props(not self.show_all))
            ui.button(
                f'All messages ({len(d.notices)})', on_click=lambda: self._toggle_all(True),
            ).props(_filter_props(self.show_all))
        rows = d.notices if self.show_all else quieter
        if not rows:
            ui.label(
                'Nothing. Every message reaches them the way the community sends it.'
            ).classes('italic-note')
        for notice in rows:
            _notice_row(notice)

    def _messages(self, d: MemberDetails) -> None:
        _section('Recent messages')
        if not d.recent_messages:
            ui.label('Nothing sent to them here yet.').classes('italic-note')
        for m in d.recent_messages:
            chip_cls, chip_icon, chip_label = STATUS_CHIP[m.status]
            if m.status.value == 'delivered' and m.silent:
                chip_label = 'Delivered silently'
            with ui.row().classes('member-sheet__row items-center no-wrap full-width'):
                ui.label(format_local_display(m.created_at)).classes('text-caption text-muted member-sheet__time')
                ui.label(m.title or m.body).classes('col ellipsis')
                with ui.element('span').classes(f'wiz-chip {chip_cls}'):
                    ui.icon(chip_icon).props('aria-hidden=true')
                    ui.label(chip_label)
        ui.button(
            'Open in Sent messages',
            on_click=lambda: ui.navigate.to(admin_url(REPORTS, report='messages', user_id=d.user.id)),
        ).props('flat dense no-caps color=primary icon-right=chevron_right').classes('self-start')

    def _community(self, d: MemberDetails) -> None:
        _section('In this community')
        with ui.element('div').classes('member-sheet__facts'):
            if d.entered:
                _fact('Tournaments', ', '.join(d.entered))
            if d.tournament_admin_of:
                _fact('Admin of', ', '.join(d.tournament_admin_of))
            if d.crew_coordinator_of:
                _fact('Crew coordinator of', ', '.join(d.crew_coordinator_of))
            matches = '; '.join(
                f"{format_local_display(m.scheduled_at)} {m.tournament}"
                + (f" vs {', '.join(m.opponents)}" if m.opponents else '')
                for m in d.upcoming_matches
            )
            _fact('Next matches', matches or 'None booked')
            if d.upcoming_shifts is not None:
                shifts = '; '.join(
                    f'{format_local_display(s.starts_at)} {s.position}'
                    + (' (called off)' if s.called_off else ' (checked in)' if s.checked_in else '')
                    for s in d.upcoming_shifts
                )
                _fact('Next shifts', shifts or 'None')
            if d.equipment_out is not None:
                _fact('Equipment out', ', '.join(d.equipment_out) or 'None')
            if d.check_in is not None:
                _fact('Event check-in', d.check_in)
            if d.linked_accounts:
                _fact('Accounts', ', '.join(f'{a.service} ({a.name})' for a in d.linked_accounts))
            _fact('Time zone', d.user.timezone or f'Not set (shown in {timezone_label()})')
