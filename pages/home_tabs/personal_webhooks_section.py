"""The Webhooks card on a member's profile.

A member's own webhooks for this community: up to three, each sending the
events they pick to a Discord channel or their own app. Rows show where each one
goes, whether it is limited to things about them, and which areas it covers;
the ⋮ holds Edit, Send test, Pause/Resume, Change secret (JSON only) and Delete.

The Add/Edit dialog picks whole areas, with single events behind "Choose single
events…". The stored URL never comes back to the page (a Discord webhook URL is
itself a credential), so editing leaves the URL blank to keep it.

Presentation only: service calls, no ORM.
"""

from typing import Dict, List, Optional, Set

from nicegui import ui

from application.services import PersonalWebhookService
from application.services.personal_webhook_service import (
    EVENTS,
    MAX_PER_USER,
    NAME_MAX,
    WebhookArea,
    is_discord_url,
    masked_url,
)
from application.utils.timezone import format_local_display
from models import PersonalWebhook, User
from theme.dialog._helpers import dialog_actions, form_dialog
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.dialog.secret_dialog import show_secret_dialog
from theme.help import help_icon
from theme.menu import menu_action, more_menu
from theme.notify import notify_error
from theme.steady import steady_refreshable


def _areas_of(webhook: PersonalWebhook, areas: List[WebhookArea]) -> List[str]:
    chosen = set(webhook.event_types or [])
    labels = []
    for area in areas:
        if any(e.area == area.key and e.event_type in chosen for e in EVENTS):
            labels.append(area.label)
    return labels


def _status_line(webhook: PersonalWebhook) -> str:
    if webhook.last_delivery_at is None:
        return 'Nothing sent yet'
    when = format_local_display(webhook.last_delivery_at)
    if webhook.consecutive_failures:
        return f'Last try {when} failed: {webhook.last_error or "no answer"}'
    return f'Last sent {when} · {webhook.delivered_count} sent'


async def render_personal_webhooks_section(user: User) -> None:
    service = PersonalWebhookService()
    try:
        await service.list_mine(user)
    except ValueError:
        return
    areas = await service.available_areas()
    area_keys = {a.key for a in areas}
    events = [e for e in EVENTS if e.area in area_keys]

    @steady_refreshable
    async def webhook_list() -> None:
        hooks = await service.list_mine(user)
        if not hooks:
            ui.label('No webhooks yet.').classes('text-muted')
        for i, webhook in enumerate(hooks):
            dot = 'off' if not webhook.is_active else ('warn' if webhook.consecutive_failures else 'on')
            with ui.element('div').classes('wiz-pwebhook-row' + (' wiz-pwebhook-row--first' if i == 0 else '')):
                ui.element('span').classes(f'wiz-pwebhook-row__dot wiz-pwebhook-row__dot--{dot}')
                with ui.column().classes('gap-0 col min-w-0'):
                    ui.label(webhook.name).classes('text-weight-medium')
                    ui.label(masked_url(webhook.url)).classes('wiz-pwebhook-row__url')
                    with ui.row().classes('items-center gap-1 q-mt-xs'):
                        if is_discord_url(webhook.url):
                            ui.badge('Discord').props('outline color=primary')
                        else:
                            ui.badge('JSON').props('outline color=grey-8')
                        ui.badge(
                            'Only about me' if webhook.only_mine else 'Everything I can see'
                        ).props('outline color=grey-8')
                        for label in _areas_of(webhook, areas):
                            ui.badge(label).props('outline color=grey-8')
                    if not webhook.is_active:
                        ui.label(webhook.paused_reason or 'Paused').classes('text-caption st-pending')
                    else:
                        ui.label(_status_line(webhook)).classes('text-caption text-muted') \
                            .style('overflow-wrap: anywhere')
                with more_menu(webhook.name):
                    menu_action('edit', 'Edit…', lambda w=webhook: open_dialog(w))
                    menu_action('send', 'Send test', lambda w=webhook: send_test(w))
                    if webhook.is_active:
                        menu_action('pause_circle', 'Pause', lambda w=webhook: set_active(w, False))
                    else:
                        menu_action('play_circle', 'Resume', lambda w=webhook: set_active(w, True))
                    if not is_discord_url(webhook.url):
                        menu_action('vpn_key', 'Change secret…', lambda w=webhook: confirm_secret(w))
                    menu_action('delete', 'Delete…', lambda w=webhook: confirm_delete(w), negative=True)
        ui.label(f'{len(hooks)} of {MAX_PER_USER} webhooks used').classes('text-caption text-muted q-mt-sm')
        add_button.set_enabled(len(hooks) < MAX_PER_USER)

    async def send_test(webhook: PersonalWebhook) -> None:
        try:
            await service.send_test(user, webhook.id)
        except (ValueError, PermissionError) as e:
            notify_error(e)
        else:
            ui.notify('Test sent. Check where it should arrive.', color='positive')
        webhook_list.refresh()

    async def set_active(webhook: PersonalWebhook, active: bool) -> None:
        try:
            await service.set_active(user, webhook.id, active)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return
        ui.notify('Resumed' if active else 'Paused', color='positive')
        webhook_list.refresh()

    def confirm_delete(webhook: PersonalWebhook) -> None:
        async def do_delete() -> None:
            try:
                await service.delete(user, webhook.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            ui.notify('Webhook deleted', color='positive')
            webhook_list.refresh()

        ConfirmationDialog(
            title=f'Delete {webhook.name}?',
            message='Nothing more is sent to it. This can’t be undone.',
            confirm_text='Delete',
            on_confirm=do_delete,
        ).open()

    def confirm_secret(webhook: PersonalWebhook) -> None:
        async def do_rotate() -> None:
            try:
                secret = await service.regenerate_secret(user, webhook.id)
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            show_secret_dialog('Your new signing secret', secret)

        ConfirmationDialog(
            title='Change the signing secret?',
            message='Your app has to switch to the new secret, or it will reject every message from now on.',
            confirm_text='Change secret',
            on_confirm=do_rotate,
        ).open()

    def open_dialog(webhook: Optional[PersonalWebhook] = None) -> None:
        chosen: Set[str] = set(webhook.event_types or []) if webhook else {
            e.event_type for e in events if e.area == 'matches'
        }
        area_boxes: Dict[str, ui.checkbox] = {}
        event_boxes: Dict[str, ui.checkbox] = {}
        syncing = {'on': False}

        def area_state(key: str) -> Optional[bool]:
            mine = [e.event_type for e in events if e.area == key]
            picked = sum(1 for t in mine if t in chosen)
            if picked == 0:
                return False
            return True if picked == len(mine) else None

        def sync_boxes() -> None:
            syncing['on'] = True
            for key, box in area_boxes.items():
                box.value = area_state(key)
            for event_type, box in event_boxes.items():
                box.value = event_type in chosen
            syncing['on'] = False

        def on_area(key: str, value: Optional[bool]) -> None:
            if syncing['on']:
                return
            for e in events:
                if e.area == key:
                    (chosen.add if value else chosen.discard)(e.event_type)
            sync_boxes()

        def on_event(event_type: str, value: bool) -> None:
            if syncing['on']:
                return
            (chosen.add if value else chosen.discard)(event_type)
            sync_boxes()

        def url_hint(value: str) -> None:
            text = (value or '').strip()
            if not text:
                hint.set_text('Leave blank to keep the current URL.' if webhook else '')
                hint.classes(replace='text-caption text-muted')
            elif is_discord_url(text):
                hint.set_text('Discord webhook. Messages arrive as Discord posts.')
                hint.classes(replace='text-caption st-ok')
            else:
                hint.set_text("Your own app. It gets signed JSON, and you'll see the secret once.")
                hint.classes(replace='text-caption text-muted')

        async def save() -> None:
            url = (url_input.value or '').strip()
            try:
                if webhook is None:
                    _, secret = await service.create(
                        user, name=name_input.value or '', url=url,
                        event_types=sorted(chosen), only_mine=bool(mine_switch.value),
                    )
                else:
                    secret = None
                    await service.update(
                        user, webhook.id, name=name_input.value or '', url=url or None,
                        event_types=sorted(chosen), only_mine=bool(mine_switch.value),
                    )
            except (ValueError, PermissionError) as e:
                notify_error(e)
                return
            dialog.close()
            webhook_list.refresh()
            ui.notify('Webhook saved', color='positive')
            if secret:
                show_secret_dialog('Your signing secret', secret)

        title = 'Edit webhook' if webhook else 'Add webhook'
        with form_dialog(title, card_classes='dialog-card dialog-card--sm') as dialog:
            with ui.column().classes('w-full gap-3'):
                name_input = ui.input(
                    'Name', value=webhook.name if webhook else '', placeholder='e.g. My match pings',
                ).props(f'outlined stack-label maxlength={NAME_MAX}').classes('input-full-width')
                url_input = ui.input(
                    'URL', placeholder=masked_url(webhook.url) if webhook else 'https://discord.com/api/webhooks/…',
                    on_change=lambda e: url_hint(e.value),
                ).props('outlined stack-label autocomplete=off').classes('input-full-width')
                hint = ui.label('').classes('text-caption text-muted').style('margin-top: -8px')
                url_hint('')
                with ui.row().classes('items-start no-wrap gap-2 w-full'):
                    mine_switch = ui.switch(value=webhook.only_mine if webhook else True) \
                        .props('aria-label="Only things about me"')
                    with ui.column().classes('gap-0 col min-w-0'):
                        ui.label('Only things about me').classes('text-weight-medium')
                        ui.label(
                            "Your matches, crew signups, shifts and qualifier runs. Turn off to "
                            "also get other people's public matches."
                        ).classes('text-caption text-muted')
                ui.label('Send these').classes('text-weight-medium')
                with ui.element('div').classes('wiz-pwebhook-areas w-full'):
                    for area in areas:
                        with ui.row().classes('items-start no-wrap gap-1'):
                            box = ui.checkbox(
                                value=area_state(area.key),
                                on_change=lambda e, k=area.key: on_area(k, e.value),
                            ).props(f'dense aria-label="{area.label}"')
                            area_boxes[area.key] = box
                            with ui.column().classes('gap-0'):
                                ui.label(area.label)
                                ui.label(area.hint).classes('text-caption text-muted')
                with ui.expansion('Choose single events…').classes('w-full') \
                        .props('dense header-class="text-primary text-weight-medium q-px-none"'):
                    for area in areas:
                        ui.label(area.label).classes('text-caption text-weight-bold q-mt-sm')
                        for e in events:
                            if e.area != area.key:
                                continue
                            event_boxes[e.event_type] = ui.checkbox(
                                e.label, value=e.event_type in chosen,
                                on_change=lambda ev, t=e.event_type: on_event(t, bool(ev.value)),
                            ).props('dense')
                with ui.row().classes('items-start no-wrap gap-2 wiz-pwebhook-lock'):
                    ui.icon('lock', size='xs').classes('text-muted')
                    ui.label(
                        "Staff notes, emergency contacts and anything else you can't see in the "
                        'app are never sent. If you lose access to something, its events stop.'
                    ).classes('text-caption text-muted col')
            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Save' if webhook else 'Add webhook', on_click=save).props('color=primary')
        dialog.open()

    with ui.card().classes('card-full-width'):
        with ui.row().classes('items-center gap-2 no-wrap w-full'):
            with ui.row().classes('items-center gap-1 no-wrap col min-w-0 wiz-help-row'):
                ui.icon('webhook').classes('text-muted')
                ui.label('Webhooks').classes('section-title')
                await help_icon('personal-webhooks', user=user)
            add_button = ui.button('Add webhook', icon='add', on_click=lambda: open_dialog()) \
                .props('color=primary dense no-caps').classes('q-px-sm')
        ui.label(
            'Send what happens here to a Discord channel or your own app. '
            'You only get what you can already see in Wizzrobe.'
        ).classes('text-muted text-caption')
        await webhook_list()
