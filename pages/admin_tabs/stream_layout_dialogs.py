"""Admin → Stream Layouts dialogs: New layout (the starter gallery), Rename and Plain background."""

from __future__ import annotations

from typing import Any, Optional

from nicegui import app, ui

from application.services import StreamLayoutService, TenantThemeService, get_user_from_session
from application.services.stream_layout_rules import KIND_HOLE, box_label
from application.services.stream_layout_starters import (
    BACKGROUNDS,
    DEFAULT_STARTER,
    GROUPS,
    STARTERS,
    STARTERS_BY_KEY,
    Starter,
)
from application.services.stream_layout_starters import H as STARTER_H
from application.services.stream_layout_starters import W as STARTER_W
from models import StreamLayout, User
from theme.dialog._helpers import dialog_actions, form_dialog, submit_on_enter
from theme.notify import notify_error


async def current_actor() -> User:
    user = await get_user_from_session(app.storage.user)
    if user is None:
        raise PermissionError('Your session has ended. Sign in again.')
    return user



def _thumb(starter: Starter, colour: str) -> None:
    """A starter drawn small: its background colour, holes checkered, boxes outlined."""
    with ui.element('div').classes('sl-thumb').style(f'background-color:{colour}'):
        for box in starter.boxes:
            pos = (f"left:{box['x'] / STARTER_W:.2%};top:{box['y'] / STARTER_H:.2%};"
                   f"width:{box['w'] / STARTER_W:.2%};height:{box['h'] / STARTER_H:.2%}")
            if box['kind'] == KIND_HOLE:
                ui.element('div').classes('sl-thumb__hole').style(pos)
            else:
                ui.label(box_label(box)).classes('sl-thumb__box').style(pos)


async def new_layout_dialog(service: StreamLayoutService, open_editor) -> None:
    community = (await TenantThemeService.get_current_theme()).get('header') or BACKGROUNDS[0][1]
    swatches = [('Community colour', community.upper()), *BACKGROUNDS]
    picked: dict[str, Any] = {'starter': DEFAULT_STARTER, 'colour': swatches[0][1], 'named': ''}

    with form_dialog('New layout', card_classes='dialog-card dialog-card--lg') as dialog:
        name = ui.input('Name', value=STARTERS_BY_KEY[DEFAULT_STARTER].label) \
            .props('outlined autofocus').classes('w-full')
        picked['named'] = name.value

        @ui.refreshable
        def gallery() -> None:
            for group in GROUPS:
                ui.label(group).classes('sl-gallery__group')
                with ui.element('div').classes('sl-gallery'):
                    for starter in (s for s in STARTERS if s.group == group):
                        gallery_tile(starter)

        def gallery_tile(starter: Starter) -> None:
            selected = starter.key == picked['starter']
            tile = ui.element('button').classes('sl-gallery__tile' + (' sl-gallery__tile--on' if selected else '')) \
                .props(f'type=button aria-pressed={"true" if selected else "false"}') \
                .on('click', lambda _, k=starter.key: choose(k))
            with tile:
                _thumb(starter, picked['colour'])
                ui.label(starter.label).classes('sl-gallery__name')
                ui.label(starter.blurb).classes('sl-gallery__blurb')

        def choose(key: str) -> None:
            if name.value == picked['named']:
                name.value = STARTERS_BY_KEY[key].label
                picked['named'] = name.value
            picked['starter'] = key
            gallery.refresh()

        def set_colour(value: Optional[str]) -> None:
            value = (value or '').strip()
            if len(value) == 7 and value.startswith('#'):
                picked['colour'] = value.upper()
                gallery.refresh()
                swatch_row.refresh()

        gallery()
        ui.label('Background').classes('text-subtitle2 q-mt-sm')

        @ui.refreshable
        def swatch_row() -> None:
            with ui.row().classes('items-center gap-2'):
                for label, colour in swatches:
                    on = colour.upper() == picked['colour']
                    ui.element('button').props(
                        f'type=button aria-label="{label}" title="{label}" aria-pressed={"true" if on else "false"}',
                    ).classes('sl-swatch' + (' sl-swatch--on' if on else '')) \
                        .style(f'background-color:{colour}').on('click', lambda _, c=colour: set_colour(c))
                ui.color_input('Or a colour', value=picked['colour'], on_change=lambda e: set_colour(e.value)) \
                    .props('outlined dense').classes('sl-swatch-input')

        swatch_row()
        ui.label('Upload your own art any time from the layout\'s ⋮ menu; it replaces the colour.') \
            .classes('text-caption text-muted')

        async def submit() -> None:
            try:
                layout = await service.create_layout(
                    await current_actor(), name.value, starter=picked['starter'], background=picked['colour'],
                )
            except (ValueError, PermissionError) as err:
                notify_error(err)
                return
            dialog.close()
            await open_editor(layout.id)

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Create', on_click=submit).props('color=primary')
        submit_on_enter(dialog, submit)
    dialog.open()


def rename_dialog(service: StreamLayoutService, layout: StreamLayout, after) -> None:
    with form_dialog('Rename layout') as dialog:
        name = ui.input('Name', value=layout.name).props('outlined autofocus').classes('w-full')

        async def submit() -> None:
            try:
                await service.rename_layout(await current_actor(), layout.id, name.value)
            except (ValueError, PermissionError) as err:
                notify_error(err)
                return
            dialog.close()
            await after()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Save', on_click=submit).props('color=primary')
        submit_on_enter(dialog, submit)
    dialog.open()


async def background_dialog(service: StreamLayoutService, layout: StreamLayout, after) -> None:
    community = (await TenantThemeService.get_current_theme()).get('header') or BACKGROUNDS[0][1]
    with form_dialog('Plain background') as dialog:
        ui.label('Swaps the art for one colour. Boxes, video holes and the art\'s transparent areas stay where they are.') \
            .classes('text-caption text-muted')
        colour = ui.color_input('Colour', value=community.upper()).props('outlined').classes('w-full')
        with ui.row().classes('items-center gap-2'):
            for label, value in [('Community colour', community.upper()), *BACKGROUNDS]:
                ui.element('button').props(f'type=button aria-label="{label}" title="{label}"') \
                    .classes('sl-swatch').style(f'background-color:{value}') \
                    .on('click', lambda _, v=value: colour.set_value(v))

        async def submit() -> None:
            try:
                await service.set_background(await current_actor(), layout.id, colour.value)
            except (ValueError, PermissionError) as err:
                notify_error(err)
                return
            dialog.close()
            await after(layout.id)

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Apply', on_click=submit).props('color=primary')
        submit_on_enter(dialog, submit)
    dialog.open()


async def download_obs_scenes(service: StreamLayoutService, stage_id: Optional[int] = None) -> None:
    try:
        filename, data = await service.obs_scene_collection(await current_actor(), stage_id)
    except (ValueError, PermissionError) as err:
        notify_error(err)
        return
    ui.download(data, filename, media_type='application/json')
    ui.notify('In OBS: Scene Collection → Import, then put each capture into its "Feed ·" scene.',
              color='positive', timeout=8000)
