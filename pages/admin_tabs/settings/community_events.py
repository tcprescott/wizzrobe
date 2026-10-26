"""Settings → Community Events: whether staff review submissions, and when subscribers hear about an event starting."""

from nicegui import ui

from application.services import SystemConfigService
from application.services.system_config_service import (
    KEY_COMMUNITY_EVENT_REMINDER_LEAD_MINUTES,
    KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL,
)
from pages.admin_tabs.settings._common import current_actor, field_hint, positive_int_or_blank
from theme.notify import notify_error
from theme.save_footer import SaveFooter
from theme.section import section_panel


async def community_events_settings_pane(can_edit: bool) -> None:
    require_approval = await SystemConfigService.community_events_require_approval()
    lead = await SystemConfigService.get_int(KEY_COMMUNITY_EVENT_REMINDER_LEAD_MINUTES)

    card = await section_panel(
        'Meetups', icon='groups',
        subtitle='Who decides what gets listed, and how early subscribers get a reminder.',
    )
    with card.body:
        with ui.column().classes('gap-1'):
            approval_input = ui.switch('Require staff approval', value=require_approval).props('dense')
            field_hint(
                "When on, an attendee's event waits for staff before it's listed. When off, "
                "it's listed straight away and staff can still take it down."
            )
        ui.separator()
        with ui.column().classes('gap-1'):
            lead_input = ui.number('Reminder lead (minutes)', value=lead, min=1, format='%d').classes('w-56')
            field_hint('How long before an event its subscribers get a DM. Blank uses 30.')

    async def save() -> bool:
        try:
            actor = await current_actor()
            raw = positive_int_or_blank(lead_input.value, 'The reminder lead')
            await SystemConfigService.set_raw(
                KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL, 'true' if approval_input.value else 'false', actor,
            )
            await SystemConfigService.set_raw(KEY_COMMUNITY_EVENT_REMINDER_LEAD_MINUTES, raw, actor)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        ui.notify('Meetup settings saved', color='positive')
        return True

    with card.panel:
        SaveFooter(save, enabled=can_edit).watch(approval_input, lead_input)
    if not can_edit:
        approval_input.disable()
        lead_input.disable()
