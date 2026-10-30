"""Device notification (Web Push) rows for the profile's "Where it goes" group.

One switch turns notifications on or off for the browser the page is open in,
and every other subscribed device gets a row with Remove. Subscribing must
happen inside the click gesture (Safari refuses permission prompts outside
one), so the switch runs ``static/js/web-push.js`` client-side via
``js_handler`` and reports back through ``emitEvent``; the server side then
persists via :class:`WebPushService`. Renders nothing when VAPID keys are not
configured.
"""

import json
from typing import Callable, Optional

from nicegui import context, ui

from application.services import WebPushService
from application.utils.timezone import format_local_date
from models import User
from theme.assets import asset_url
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.steady import steady_refreshable


def _device_label(user_agent: str | None) -> str:
    ua = user_agent or ''
    if 'iPhone' in ua:
        device = 'iPhone'
    elif 'iPad' in ua:
        device = 'iPad'
    elif 'Android' in ua:
        device = 'Android'
    elif 'Macintosh' in ua:
        device = 'Mac'
    elif 'Windows' in ua:
        device = 'Windows'
    elif 'Linux' in ua:
        device = 'Linux'
    else:
        device = 'Unknown device'
    if 'Edg/' in ua or 'EdgiOS/' in ua:
        browser = 'Edge'
    elif 'Firefox/' in ua or 'FxiOS/' in ua:
        browser = 'Firefox'
    elif 'Chrome/' in ua or 'CriOS/' in ua:
        browser = 'Chrome'
    elif 'Safari/' in ua:
        browser = 'Safari'
    else:
        browser = ''
    return f'{device} · {browser}' if browser else device


def _this_device_name(user_agent: str | None) -> str:
    ua = user_agent or ''
    if 'iPhone' in ua or ('Android' in ua and 'Mobile' in ua):
        return 'This phone'
    if 'iPad' in ua or 'Android' in ua:
        return 'This tablet'
    return 'This browser'


def _device_caption(sub) -> str:
    added = f'Added {format_local_date(sub.created_at)}'
    if sub.last_used_at:
        added += f' · last notified {format_local_date(sub.last_used_at)}'
    return added


async def render_device_rows(
    user: User, on_count_change: Optional[Callable[[int], None]] = None,
) -> bool:
    """Render the device rows; returns False (and renders nothing) without VAPID.

    ``on_count_change`` hears the number of subscribed devices whenever it
    changes, so the caller can say when every channel is off.
    """
    service = WebPushService()
    if not service.is_configured():
        return False

    request = context.client.request
    user_agent = request.headers.get('user-agent') if request else None
    public_key = service.get_public_key()
    state: dict = {'endpoint': None}

    with ui.row().classes('wiz-notify-item no-wrap'):
        ui.icon('devices', size='sm').classes('wiz-notify-item__icon')
        with ui.column().classes('gap-0 col min-w-0'):
            ui.label(_this_device_name(user_agent)).classes('wiz-notify-item__name')
            this_caption = ui.label(_device_label(user_agent)).classes('text-muted text-caption')
        this_switch = ui.switch(value=False).props(
            f'disable aria-label="Notifications on {_this_device_name(user_agent).lower()}"'
        )
        this_switch.on(
            'update:model-value',
            js_handler=(
                'async (on) => {'
                'if (on) {'
                f'const result = await window.wizWebPush.subscribe({json.dumps(public_key)});'
                "emitEvent('wiz_web_push_subscribed', result);"
                '} else {'
                'const result = await window.wizWebPush.unsubscribe();'
                "emitEvent('wiz_web_push_unsubscribed', result);"
                '}'
                '}'
            ),
        )

    @steady_refreshable
    async def other_devices() -> None:
        subscriptions = await service.list_subscriptions(user)
        if on_count_change is not None:
            on_count_change(len(subscriptions))
        for sub in subscriptions:
            if sub.endpoint == state['endpoint']:
                continue
            with ui.row().classes('wiz-notify-item no-wrap'):
                phone = any(k in (sub.user_agent or '') for k in ('iPhone', 'iPad', 'Android'))
                ui.icon('smartphone' if phone else 'computer', size='sm').classes('wiz-notify-item__icon')
                with ui.column().classes('gap-0 col min-w-0'):
                    ui.label(_device_label(sub.user_agent)).classes('wiz-notify-item__name')
                    ui.label(_device_caption(sub)).classes('text-muted text-caption')
                ui.button('Remove', on_click=lambda _, sid=sub.id: remove(sid)) \
                    .props('flat dense no-caps color=negative')

    def set_this_switch(on: bool) -> None:
        this_switch.set_value(on)
        this_caption.set_text(
            _device_label(user_agent) if on else f'{_device_label(user_agent)} · off'
        )

    def remove(subscription_id: int) -> None:
        async def do_remove() -> None:
            try:
                await service.remove_subscription(user, subscription_id)
                ui.notify('Device removed.', color='positive', icon='check_circle')
            except ValueError as e:
                ui.notify(str(e), color='warning')
            other_devices.refresh()

        ConfirmationDialog(
            message='Remove this device? It will stop getting notifications.',
            on_confirm=do_remove, confirm_text='Remove',
        ).open()

    async def on_status(e) -> None:
        args = e.args or {}
        this_switch.props(remove='disable')
        if not args.get('supported'):
            this_switch.props('disable')
            this_caption.set_text(
                'Add Wizzrobe to your Home Screen first, then turn this on from there.'
                if args.get('ios') else "This browser can't show notifications."
            )
            return
        endpoint = args.get('endpoint')
        subscribed = {s.endpoint for s in await service.list_subscriptions(user)}
        state['endpoint'] = endpoint if endpoint in subscribed else None
        set_this_switch(state['endpoint'] is not None)
        other_devices.refresh()

    async def on_subscribed(e) -> None:
        args = e.args or {}
        error = args.get('error')
        if error:
            set_this_switch(False)
            if error == 'ios_needs_install':
                ui.notify(
                    'On iPhone or iPad, add Wizzrobe to your Home Screen first '
                    '(Share, then Add to Home Screen), then turn this on from the installed app.',
                    color='warning', multi_line=True,
                )
            elif error == 'permission_denied':
                ui.notify('Notifications are blocked for this site in your browser settings.', color='warning')
            elif error == 'permission_dismissed':
                ui.notify('Notifications weren\'t allowed. Turn it on again and choose Allow.', color='warning')
            elif error == 'unsupported':
                ui.notify("This browser can't show notifications.", color='warning')
            else:
                ui.notify(f'Could not turn on notifications: {error}', color='warning')
            return
        subscription = args.get('subscription') or {}
        keys = subscription.get('keys') or {}
        try:
            await service.subscribe(
                user,
                endpoint=subscription.get('endpoint') or '',
                p256dh=keys.get('p256dh') or '',
                auth=keys.get('auth') or '',
                user_agent=args.get('userAgent'),
            )
        except ValueError as err:
            set_this_switch(False)
            ui.notify(str(err), color='warning')
            return
        state['endpoint'] = subscription.get('endpoint')
        set_this_switch(True)
        ui.notify('Notifications on for this device.', color='positive', icon='notifications_active')
        other_devices.refresh()

    async def on_unsubscribed(e) -> None:
        args = e.args or {}
        if args.get('error'):
            set_this_switch(True)
            ui.notify(f"Could not turn off notifications: {args['error']}", color='warning')
            return
        endpoint = args.get('endpoint')
        if endpoint:
            await service.unsubscribe(user, endpoint)
        state['endpoint'] = None
        set_this_switch(False)
        ui.notify('Notifications off for this device.', color='positive', icon='notifications_off')
        other_devices.refresh()

    ui.on('wiz_web_push_status', on_status)
    ui.on('wiz_web_push_subscribed', on_subscribed)
    ui.on('wiz_web_push_unsubscribed', on_unsubscribed)

    await other_devices()

    # Append the scripts as elements: built inside an event handler, add_head_html falls back to
    # insertAdjacentHTML, and a <script> inserted that way never runs.
    scripts = json.dumps([asset_url('js/web-push-common.js'), asset_url('js/web-push.js')])

    async def ask_status() -> None:
        await context.client.connected()
        ui.run_javascript(
            f'Promise.all({scripts}.map((src) => {{'
            'const loaded = (window.__wizScripts = window.__wizScripts || {});'
            'return loaded[src] || (loaded[src] = new Promise((resolve, reject) => {'
            "const el = document.createElement('script');"
            'el.src = src; el.async = false; el.onload = resolve;'
            'el.onerror = () => { delete loaded[src]; reject(new Error(src)); };'
            'document.head.appendChild(el);'
            '}));'
            '}))'
            '.then(() => window.wizWebPush.status())'
            '.catch(() => ({ supported: false }))'
            '.then((s) => emitEvent("wiz_web_push_status", s));'
        )

    ui.timer(0, ask_status, once=True)
    return True
