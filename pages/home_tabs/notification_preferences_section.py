"""Profile → Notifications → What you hear about: presets first, then every message.

Layout C (Thomas, 2026-10-19): three presets showing how many messages each
would ping, send silently or leave off, then a Customize list with Off / Silent /
Ping on each message, grouped by what the reader is doing when it arrives. The
community's own setting is a ceiling, so a message it sends quietly can't be made
to ping and one it turned off shows as locked.
"""

from typing import Awaitable, Callable, Dict, Union

from nicegui import ui

from application.services import NotificationPreferenceService
from application.services.notification_preference_service import (
    SECTIONS,
    STAFF_RECIPIENTS,
    MyNotificationRow,
    MyNotifications,
    quieter,
)
from models import NotificationMode, NotificationPreset, User
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.notify import notify_error

OFF, SILENT, PING = NotificationMode.OFF, NotificationMode.SILENT, NotificationMode.PING
_ICONS = {PING: 'notifications_active', SILENT: 'notifications_off', OFF: 'block'}
_LABELS = {PING: 'Ping', SILENT: 'Silent', OFF: 'Off'}
_PRESETS = (
    (NotificationPreset.EVERYTHING, 'Everything',
     'Every message your community sends, the way it sends it.'),
    (NotificationPreset.NEEDS_ME, 'Only what needs me',
     'Pings when you have something to do: acknowledge a match, pick a time, '
     'take a shift. The rest arrives silently.'),
    (NotificationPreset.QUIET, 'Quiet',
     "Only what can't wait pings: stage calls, race rooms, shift starts. "
     'Nothing about matches you watch or follow.'),
)
_PRESET_NAMES = {p: name for p, name, _ in _PRESETS}


def _count_chips(counts: Dict[NotificationMode, int]) -> None:
    with ui.row().classes('items-center gap-1'):
        for mode in (PING, SILENT, OFF):
            if counts[mode]:
                with ui.element('span').classes(f'wiz-notify-cell wiz-notify-cell--{mode.value} wiz-notify-count'):
                    ui.icon(_ICONS[mode]).props('aria-hidden=true')
                    ui.label(f'{counts[mode]} {_LABELS[mode].lower()}')


def _row_title(row: MyNotificationRow) -> str:
    if row.target.recipient in STAFF_RECIPIENTS:
        return f'{row.notice.title} ({row.target.recipient.label.lower()})'
    return row.notice.title


async def render_notification_preferences(
    user: User, *, on_saving: Callable[[], None], on_saved: Callable[[], None],
    on_error: Callable[[str], None],
) -> None:
    state = {'mine': await NotificationPreferenceService.get_mine(user)}
    open_sections: Dict[str, bool] = {}
    customize_open = {'v': False}

    async def run(action: Callable[[], Awaitable[Union[MyNotifications, MyNotificationRow]]]) -> None:
        on_saving()
        try:
            result = await action()
        except (ValueError, PermissionError) as e:
            on_error(str(e))
            notify_error(e)
            return
        state['mine'] = result if isinstance(result, MyNotifications) \
            else await NotificationPreferenceService.get_mine(user)
        on_saved()
        body.refresh()

    async def pick_preset(preset: NotificationPreset) -> None:
        if preset is not state['mine'].preset:
            await run(lambda: NotificationPreferenceService.set_preset(user, preset))

    async def pick_mode(row: MyNotificationRow, mode: Union[NotificationMode, None]) -> None:
        await run(lambda: NotificationPreferenceService.set_mode(
            user, row.target.key, row.target.recipient, mode,
        ))

    async def reset() -> None:
        await run(lambda: NotificationPreferenceService.reset(user))

    def preset_card(preset: NotificationPreset, title: str, blurb: str) -> None:
        chosen = preset is state['mine'].preset
        card = ui.element('div').classes(
            'wiz-notify-preset' + (' wiz-notify-preset--on' if chosen else ''),
        ).props(f'role=radio aria-checked={"true" if chosen else "false"} tabindex=0')
        for event in ('click', 'keydown.enter', 'keydown.space.prevent'):
            card.on(event, lambda p=preset: pick_preset(p))
        with card, ui.row().classes('items-start no-wrap gap-2 w-full'):
            ui.icon('radio_button_checked' if chosen else 'radio_button_unchecked') \
                .classes('wiz-notify-preset__radio').props('aria-hidden=true')
            with ui.column().classes('gap-1 col min-w-0'):
                ui.label(title).classes('wiz-notify-item__name')
                ui.label(blurb).classes('text-caption text-muted')
                _count_chips(state['mine'].counts(preset))

    def mode_button(row: MyNotificationRow) -> None:
        if row.community is OFF:
            with ui.element('span').classes('wiz-notify-cell wiz-notify-cell--off wiz-notify-locked'):
                ui.icon('lock').props('aria-hidden=true')
                ui.label('Off here')
                ui.tooltip('Your community turned this one off for everyone.')
            return
        mode = row.effective
        state_text = _LABELS[mode] + (', changed by you' if row.changed else '')
        button = ui.button(_LABELS[mode], icon=_ICONS[mode], color=None).props(
            f'flat dense no-caps icon-right=expand_more aria-label="{_row_title(row)}: {state_text}"'
        ).classes(
            f'wiz-notify-cell wiz-notify-cell--{mode.value}'
            + (' wiz-notify-cell--changed' if row.changed else '')
        )
        with button, ui.menu():
            for option in (OFF, SILENT, PING):
                if option is PING and row.community is not PING:
                    with ui.item().props('disable'):
                        with ui.item_section().props('avatar'):
                            ui.icon('lock')
                        ui.item_section('Ping (your community sends this one quietly)')
                    continue
                with ui.item(on_click=lambda o=option: pick_mode(row, o)).props('clickable v-close-popup'):
                    with ui.item_section().props('avatar'):
                        ui.icon(_ICONS[option])
                    ui.item_section(_LABELS[option] + (' (current)' if option is mode else ''))
            if row.changed:
                ui.separator()
                back = quieter(row.community, row.preset)
                preset_name = _PRESET_NAMES[state['mine'].preset]
                with ui.item(on_click=lambda: pick_mode(row, None)).props('clickable v-close-popup'):
                    with ui.item_section().props('avatar'):
                        ui.icon('undo')
                    ui.item_section(f'Back to {preset_name}: {_LABELS[back]}')

    def confirm_reset() -> None:
        mine = state['mine']
        ConfirmationDialog(
            message=(
                f'Your {mine.changed} change{"" if mine.changed == 1 else "s"} go back to '
                f'"{_PRESET_NAMES[mine.preset]}". Your preset stays.'
            ),
            on_confirm=reset, confirm_text='Reset', title='Reset to your preset',
        ).open()

    @ui.refreshable
    def body() -> None:
        current = state['mine']
        with ui.column().classes('wiz-notify-item gap-2'):
            with ui.column().classes('gap-0'):
                ui.label('How much you hear').classes('wiz-notify-item__name')
                ui.label(
                    'Start from a preset, then change single messages under Customize. '
                    'Your community decides what gets sent; you can make anything quieter.'
                ).classes('text-muted text-caption')
            with ui.element('div').classes('wiz-notify-presets') \
                    .props('role=radiogroup aria-label="How much you hear"'):
                for preset, title, blurb in _PRESETS:
                    preset_card(preset, title, blurb)

        changed = current.changed
        summary = (
            f'{changed} message{"" if changed == 1 else "s"} changed from "{_PRESET_NAMES[current.preset]}"'
            if changed else 'Choose Off, Silent or Ping for any single message'
        )
        with ui.expansion(value=customize_open['v']).classes('wiz-notify-item wiz-notify-customize w-full') \
                .on_value_change(lambda e: customize_open.update(v=e.value)) as customize:
            with customize.add_slot('header'):
                with ui.row().classes('items-center no-wrap gap-2 w-full'):
                    with ui.column().classes('gap-0 col min-w-0'):
                        ui.label('Customize').classes('wiz-notify-item__name')
                        ui.label(summary).classes('text-caption text-muted')
            if changed:
                with ui.row().classes('w-full justify-end'):
                    ui.button('Reset to preset…', icon='restart_alt', on_click=confirm_reset, color=None) \
                        .props('flat dense no-caps')
            for title, blurb in SECTIONS:
                rows = [r for r in current.rows if r.section == title]
                if not rows:
                    continue
                counts = dict.fromkeys((PING, SILENT, OFF), 0)
                for r in rows:
                    counts[r.effective] += 1
                mine_changed = sum(1 for r in rows if r.changed)
                with ui.expansion(value=open_sections.get(title, False)) \
                        .classes('wiz-notify-section w-full') \
                        .on_value_change(lambda e, t=title: open_sections.update({t: e.value})) as section:
                    with section.add_slot('header'):
                        with ui.column().classes('gap-1 col min-w-0'):
                            ui.label(title).classes('wiz-notify-item__name')
                            ui.label(blurb).classes('text-caption text-muted')
                            with ui.row().classes('items-center gap-1'):
                                _count_chips(counts)
                                if mine_changed:
                                    ui.label(f'{mine_changed} changed').classes('text-caption text-muted')
                    with ui.column().classes('gap-0 w-full'):
                        for row in rows:
                            with ui.row().classes('wiz-notify-message items-center no-wrap w-full gap-2'):
                                with ui.column().classes('gap-0 col min-w-0'):
                                    ui.label(_row_title(row)).classes('text-weight-medium')
                                    ui.label(row.notice.when).classes('text-caption text-muted')
                                mode_button(row)

    body()
