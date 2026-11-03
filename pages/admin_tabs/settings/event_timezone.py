"""Event settings → Timezone: the community's clock, or one of the event's own.

An online community running one on-site weekend pins the venue's clock for that
event alone, and its other events keep following each viewer.
"""

from nicegui import ui

from application.services import TimezoneService
from application.services.timezone_service import COMMON_TIMEZONES, MODE_PINNED, MODE_USER
from pages.admin_tabs.settings._common import current_actor, field_hint
from theme.notify import notify_error
from theme.save_footer import SaveFooter
from theme.section import section_panel

_FOLLOW = 'community'

_MODE_LABELS = {
    MODE_USER: 'Follow each person’s own timezone',
    MODE_PINNED: 'Show one timezone to everybody',
}


async def event_timezone_pane(can_edit: bool) -> None:
    community = await TimezoneService.community_settings()
    override = await TimezoneService.event_override()
    community_text = (
        f'one timezone for everybody, {community["name"]}' if community['mode'] == MODE_PINNED
        else f'each person’s own timezone, falling back to {community["name"]}'
    )

    card = await section_panel(
        'Timezone', icon='schedule',
        subtitle='The clock this event’s times are shown on. Times are always stored precisely.',
    )
    with card.body:
        choice = ui.radio(
            {_FOLLOW: 'Same as the community', 'own': 'Its own timezone'},
            value='own' if override else _FOLLOW,
        )
        field_hint(f'The community shows {community_text}.')
        own = ui.column().classes('gap-2 q-mt-sm')
        with own:
            current = override or community
            mode_input = ui.radio(_MODE_LABELS, value=current['mode'])
            options = list(COMMON_TIMEZONES)
            if current['name'] not in options:
                options.insert(0, current['name'])
            zone_input = ui.select(options, value=current['name'], label='Timezone', with_input=True) \
                .classes('control-width').props('dense')
        own.bind_visibility_from(choice, 'value', backward=lambda v: v == 'own')
        if not can_edit:
            for element in (choice, mode_input, zone_input):
                element.disable()

    async def save() -> bool:
        try:
            if choice.value == _FOLLOW:
                await TimezoneService.set_event_override(await current_actor(), None)
            else:
                await TimezoneService.set_event_override(await current_actor(), mode_input.value, zone_input.value)
        except (ValueError, PermissionError) as e:
            notify_error(e)
            return False
        ui.notify('Timezone saved', color='positive')
        # The clock is resolved once per page build.
        ui.navigate.reload()
        return True

    with card.panel:
        SaveFooter(save, enabled=can_edit).watch(choice, mode_input, zone_input)
