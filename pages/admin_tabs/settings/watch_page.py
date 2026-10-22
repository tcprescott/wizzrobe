"""Settings → Watch page: how many people are on ``/live``, and live versus static.

The live page costs a websocket per reader, and the app admits about ten new
ones a second. Staff watch the arrivals here during the event and can set the
page static (one cached copy for everyone, updating itself every minute), or
leave the automatic switch to do it when a rush comes in faster than anyone
could react.
"""

from nicegui import ui

from application.services import SystemConfigService
from application.services.system_config_service import (
    KEY_WATCH_AUTO_STATIC,
    KEY_WATCH_AUTO_THRESHOLD,
    KEY_WATCH_MODE,
)
from application.tenant_context import require_tenant_id
from application.utils import live_audience
from application.utils.timezone import format_local_time
from pages.admin_tabs.settings._common import current_actor, field_hint
from pages.watch_data import forget_settings, watch_settings
from theme.banner import banner
from theme.notify import notify_error
from theme.save_footer import SaveFooter
from theme.section import section_panel

#: New live viewers the app can let in a minute, from docs/scaling-roadmap.md.
ADMISSIONS_PER_MINUTE = 600

_MODE_LABELS = {
    'live': 'Live: signed-in viewers see updates the moment a match changes, and each holds a connection. '
            'Signed-out visitors always get the static copy.',
    'static': 'Static: one shared copy that updates itself every minute. The player keeps playing.',
}


def _stat(value: str, label: str) -> None:
    with ui.column().classes('wiz-watch-stat'):
        ui.label(value).classes('wiz-watch-stat__num')
        ui.label(label).classes('wiz-watch-stat__label')


async def watch_page_settings_pane(can_edit: bool) -> None:
    tenant_id = require_tenant_id()
    settings = await watch_settings(tenant_id)

    now_card = await section_panel(
        'Right now', icon='visibility',
        subtitle='Who is on the Watch page at /live. Updates every few seconds.',
    )

    @ui.refreshable
    def right_now() -> None:
        snap = live_audience.snapshot(tenant_id)
        if snap.tripped:
            with banner(
                'It goes back to live after ten minutes without a rush.',
                title=f'Switched to static at {format_local_time(snap.tripped_at)}: '
                      'people arrived faster than the app lets them in.',
                tone='warning',
            ):
                if can_edit:
                    ui.button('Back to live now', on_click=back_to_live).props('outline no-caps')
        with ui.element('div').classes('wiz-watch-stats'):
            _stat(str(snap.live_viewers), 'Watching live')
            _stat(str(snap.static_readers), 'Reading the static page')
            _stat(f'{snap.arrivals_last_minute}', f'Signed in and arrived in the last minute (the app takes about {ADMISSIONS_PER_MINUTE})')
            peak = f' at {format_local_time(snap.peak_at)}' if snap.peak_at else ''
            _stat(str(snap.peak), f'Most at once{peak}')

    def back_to_live() -> None:
        live_audience.clear_trip(tenant_id)
        right_now.refresh()

    with now_card.body:
        right_now()
    ui.timer(5.0, right_now.refresh)

    card = await section_panel(
        'Mode', icon='tune',
        subtitle='Live suits a normal day. Static has no limit on how many people can read it.',
    )
    with card.body:
        mode_input = ui.radio(_MODE_LABELS, value=settings.mode).props('dense')
        ui.separator()
        with ui.column().classes('gap-1'):
            auto_input = ui.switch(
                'Switch to static on its own during a rush', value=settings.auto_static,
            ).props('dense')
            threshold_input = ui.number(
                'Arrivals a minute that count as a rush', value=settings.threshold, min=1, format='%d',
            ).classes('w-72')
            field_hint(
                'Past this many signed-in arrivals in a minute, new arrivals get the static page '
                'until ten minutes pass without one. People already watching stay live.'
            )

    async def save() -> bool:
        try:
            actor = await current_actor()
            await SystemConfigService.set_validated(KEY_WATCH_MODE, mode_input.value, actor)
            await SystemConfigService.set_validated(
                KEY_WATCH_AUTO_STATIC, 'true' if auto_input.value else 'false', actor,
            )
            await SystemConfigService.set_validated(
                KEY_WATCH_AUTO_THRESHOLD, str(int(threshold_input.value or 0)), actor,
            )
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        forget_settings(tenant_id)
        ui.notify('Watch page settings saved', color='positive')
        return True

    with card.panel:
        SaveFooter(save, enabled=can_edit).watch(mode_input, auto_input, threshold_input)
    if not can_edit:
        mode_input.disable()
        auto_input.disable()
        threshold_input.disable()
