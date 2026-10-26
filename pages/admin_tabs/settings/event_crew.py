"""Event settings → Crew: whether this event's matches take broadcasters."""

from nicegui import ui

from application.services import SystemConfigService
from application.services.system_config_service import KEY_BROADCASTERS
from pages.admin_tabs.settings._common import current_actor, field_hint
from theme.notify import notify_error
from theme.save_footer import SaveFooter
from theme.section import section_panel


async def event_crew_pane(can_edit: bool) -> None:
    broadcasters = await SystemConfigService.get_bool(KEY_BROADCASTERS)

    card = await section_panel(
        'Broadcasters', icon='podcasts',
        subtitle='A third crew role next to commentators and trackers.',
    )
    with card.body:
        with ui.column().classes('gap-1'):
            switch = ui.switch('Broadcasters on matches', value=broadcasters).props('dense')
            field_hint(
                "When on, anyone in the community can sign up to broadcast a match, and staff "
                "approve them like commentators. Each tournament sets how many it needs "
                "(1 unless you change it, 0 for none). Only this event changes."
            )

    async def save() -> bool:
        try:
            await SystemConfigService.set_raw(
                KEY_BROADCASTERS, 'true' if switch.value else 'false', await current_actor(),
            )
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        ui.notify('Crew settings saved', color='positive')
        return True

    with card.panel:
        SaveFooter(save, enabled=can_edit).watch(switch)
    if not can_edit:
        switch.disable()
