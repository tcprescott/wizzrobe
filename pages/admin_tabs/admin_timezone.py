"""Admin Timezone Page.

Lets a community's STAFF decide which clock its members read times on: one fixed
zone for everybody, or each member's own. Reads/writes go through
``TimezoneService``; saving reloads the page so the choice takes effect
immediately (the zone is resolved once, at page build).

The two modes answer genuinely different situations, so the copy leads with the
situation rather than the setting: an on-site event wants venue time on every
screen, an online tournament wants each player's own.
"""

from nicegui import app, ui

from application.services import AuthService, TimezoneService, get_user_from_session
from application.services.timezone_service import COMMON_TIMEZONES, MODE_PINNED, MODE_USER
from application.utils.timezone import format_local_display, now_local
from theme.page_header import page_header

_MODE_LABELS = {
    MODE_USER: 'Follow each person’s own timezone',
    MODE_PINNED: 'Show one timezone to everybody',
}


async def admin_timezone_page() -> None:
    actor = await get_user_from_session(app.storage.user)
    can_edit = await AuthService.is_staff(actor)

    settings = await TimezoneService.community_settings()

    with ui.column().classes('page-container-narrow'):
        page_header(
            'Timezone',
            'Times are always stored precisely; this only controls the clock they '
            'are shown on. Pick one fixed timezone when everyone is in the same '
            'place — an on-site event, where “14:30” means 14:30 at the venue. '
            'Follow each person’s timezone when they are not — an online '
            'tournament, where a player in Berlin and a player in Los Angeles '
            'should each see their own local time for the same match.',
        )

        mode_toggle = ui.radio(
            _MODE_LABELS, value=settings['mode'],
        ).classes('mt-2')

        # Include whatever is stored, or saving silently drops a zone outside the shortlist.
        options = list(COMMON_TIMEZONES)
        if settings['name'] not in options:
            options.insert(0, settings['name'])

        zone_select = ui.select(
            options, value=settings['name'], label='Timezone',
            with_input=True,
        ).classes('control-width').props('dense')

        zone_caption = ui.label('').classes('text-caption text-grey')

        def describe() -> None:
            """Explain what the current selection means, and preview the clock."""
            pinned = mode_toggle.value == MODE_PINNED
            zone = zone_select.value or settings['name']
            try:
                preview = format_local_display(now_local(zone), tz=zone)
            except Exception:
                preview = ''
            if pinned:
                zone_select.props(remove='hint')
                zone_caption.text = (
                    f'Everyone sees times in {zone}. Right now that reads '
                    f'{preview}.'
                )
            else:
                zone_caption.text = (
                    f'Everyone sees their own device’s timezone, or the one they '
                    f'choose on their profile. {zone} is the fallback for anyone '
                    f'whose timezone cannot be detected — and for shared pages '
                    f'like public brackets, which are the same for every viewer. '
                    f'Right now it reads {preview}.'
                )

        describe()
        mode_toggle.on_value_change(lambda _: describe())
        zone_select.on_value_change(lambda _: describe())

        if not can_edit:
            mode_toggle.disable()
            zone_select.disable()
            ui.label('Only Staff can change this.').classes('text-caption text-grey')
            return

        async def save():
            actor = await get_user_from_session(app.storage.user)
            try:
                await TimezoneService.set_settings(
                    actor, mode_toggle.value, zone_select.value,
                )
            except ValueError as e:
                ui.notify(str(e), color='warning')
                return
            except PermissionError as e:
                ui.notify(str(e), color='negative')
                return
            ui.notify('Timezone settings saved', color='positive')
            ui.navigate.reload()

        with ui.row().classes('gap-2 mt-2'):
            ui.button('Save', icon='save', on_click=save).props('color=primary')
