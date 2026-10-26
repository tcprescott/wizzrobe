"""Admin → Stream Layouts: design OBS overlays and hand each stage its link.

The tab opens on two lists: the event's stages, each with the layout it shows,
what is on it now and its OBS link; and the event's layouts. Editing a layout
swaps the tab for the editor (Thomas picked layout B): the art at full width
with its boxes, the box list under it, and the selected box's settings in a
side sheet (a bottom sheet on a phone). Dragging is done in the browser by
static/js/stream-layout-editor.js, which reports each finished move back here.

The editor holds an unsaved copy of the boxes; Save writes it, refusing when
someone else saved the same layout in the meantime.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from nicegui import app, events, ui

from application.services import StreamLayoutService, TenantService, get_user_from_session
from application.services.stream_layout_rules import (
    ALIGNS,
    FIELDS,
    FIELDS_BY_KEY,
    FONTS,
    KIND_FIELD,
    KIND_HOLE,
    MAX_PLAYERS,
    WEIGHTS,
    box_label,
)
from application.services.stream_layout_service import ART_ACCEPT, MAX_ART_BYTES, art_url, overlay_path
from application.tenant_context import require_tenant_id
from application.utils.tenant_urls import tenant_url
from application.utils.timezone import format_local_time
from models import Match, StreamLayout, User
from theme.assets import asset_url
from theme.busy import busy_click
from theme.clipboard import copy_to_clipboard
from theme.dialog._helpers import dialog_actions, dialog_header, form_dialog, submit_on_enter
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.help import help_icon
from theme.links import app_href
from theme.menu import menu_action, menu_link, more_menu
from theme.notify import notify_error
from theme.page_header import page_header
from theme.section import section_panel
from theme.steady import steady_refreshable

_ICON = {f.key: f.icon for f in FIELDS}


def _match_label(match: Match) -> str:
    names = [p.user.preferred_name for p in sorted(match.players, key=lambda p: p.id)]
    who = ' vs '.join(names) if 0 < len(names) <= 2 else (match.title or f'{len(names)} players')
    when = format_local_time(match.scheduled_at) if match.scheduled_at else ''
    tournament = match.tournament.short_label if match.tournament else ''
    return ' · '.join(x for x in (when, who, tournament) if x)


async def _actor() -> User:
    user = await get_user_from_session(app.storage.user)
    if user is None:
        raise PermissionError('Your session has ended. Sign in again.')
    return user


async def _obs_url(token: str) -> str:
    # Absolute: it is pasted into OBS on another machine.
    tenant = await TenantService.get_by_id(require_tenant_id())
    return tenant_url(tenant, overlay_path(token))


async def admin_stream_layouts_page() -> None:
    ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/stream-layouts.css")}">')
    ui.add_head_html(f'<script defer src="{asset_url("js/stream-layout-editor.js")}"></script>')
    service = StreamLayoutService()
    state: dict[str, Any] = {'editor': None}

    def on_box_changed(e: events.GenericEventArguments) -> None:
        if state['editor'] is not None:
            state['editor'].moved(e.args)

    def on_box_selected(e: events.GenericEventArguments) -> None:
        if state['editor'] is not None:
            state['editor'].select(e.args.get('id'))

    ui.on('sl_box_changed', on_box_changed)
    ui.on('sl_box_selected', on_box_selected)

    container = ui.column().classes('page-container w-full')

    async def show_list() -> None:
        state['editor'] = None
        container.clear()
        with container:
            await _render_overview(service, open_editor)

    async def open_editor(layout_id: int) -> None:
        try:
            layout = await service.get_layout(await _actor(), layout_id)
        except (ValueError, PermissionError) as err:
            notify_error(err)
            return
        container.clear()
        with container:
            editor = LayoutEditor(service, layout, show_list, open_editor)
            state['editor'] = editor
            await editor.build()

    await show_list()


async def _render_overview(service: StreamLayoutService, open_editor) -> None:
    actor = await _actor()
    with page_header(
        'Stream Layouts',
        'Overlays OBS puts on top of the video. Each stage has its own link, '
        'and it follows whatever match is on that stage.',
    ):
        await help_icon('stream-layouts', user=actor)
        ui.space()
        ui.button('New layout', icon='add', on_click=lambda: _new_layout_dialog(service, open_editor)) \
            .props('color=primary')

    @steady_refreshable
    async def stages() -> None:
        try:
            rows = await service.stage_overlays(await _actor())
            layouts = await service.list_layouts(await _actor())
        except (ValueError, PermissionError) as err:
            notify_error(err)
            return
        if not rows:
            ui.label('No stages yet. Add one under Admin → Stages and it shows up here.') \
                .classes('italic-note')
            return
        options = {0: 'No layout'} | {lay.id: lay.name for lay in layouts}
        for row in rows:
            _stage_row(service, row, options, stages.refresh)

    @steady_refreshable
    async def layout_list() -> None:
        try:
            layouts = await service.list_layouts(await _actor())
        except (ValueError, PermissionError) as err:
            notify_error(err)
            return
        if not layouts:
            ui.label('No layouts for this event yet. Press New layout and upload your art to start.') \
                .classes('italic-note')
            return
        with ui.element('div').classes('sl-list w-full'):
            for layout in layouts:
                _layout_row(service, layout, open_editor, layout_list.refresh, stages.refresh)

    panel = await section_panel('Stages', icon='tv', subtitle=(
        'Paste a stage\'s OBS link into a Browser source sized to the layout, above the '
        'video sources. Pin a match from its match sheet, or here, to hold it on screen.'
    ), user=actor)
    with panel.body:
        await stages()
    panel = await section_panel('Layouts', icon='dashboard', user=actor)
    with panel.body:
        await layout_list()


def _stage_row(service: StreamLayoutService, row, options: dict, refresh) -> None:
    stage, overlay = row.stage, row.overlay
    if row.showing is None:
        showing = 'Nothing on this stage right now'
    else:
        showing = ('Pinned: ' if row.pinned else 'Showing: ') + _match_label(row.showing)

    async def set_layout(e) -> None:
        try:
            await service.set_stage_layout(await _actor(), stage.id, e.value or None)
            ui.notify(f'{stage.name} now uses {options[e.value]}.' if e.value else
                      f'{stage.name} has no layout; its overlay is blank.', color='positive')
        except (ValueError, PermissionError) as err:
            notify_error(err)

    async def copy_link() -> None:
        copy_to_clipboard(await _obs_url(overlay.token), f'{stage.name} OBS link')

    async def unpin() -> None:
        try:
            await service.unpin(await _actor(), stage.id)
            ui.notify(f'{stage.name} follows its matches again.', color='positive')
        except (ValueError, PermissionError) as err:
            notify_error(err)
        await refresh()

    async def renew() -> None:
        try:
            await service.renew_link(await _actor(), stage.id)
            ui.notify(f'New link for {stage.name}. Paste it into OBS; the old one has stopped working.',
                      color='positive')
        except (ValueError, PermissionError) as err:
            notify_error(err)
        await refresh()

    with ui.element('div').classes('sl-stage w-full'):
        with ui.column().classes('gap-0 min-w-0'):
            ui.label(stage.name).classes('text-weight-bold')
            ui.label(showing).classes('text-caption text-muted ellipsis')
        ui.select(options, value=overlay.layout_id or 0, label='Layout', on_change=set_layout) \
            .props('outlined dense options-dense').classes('sl-stage__layout')
        with ui.row().classes('items-center no-wrap gap-1'):
            ui.button('Copy OBS link', icon='content_copy', on_click=copy_link) \
                .props('outline color=primary no-caps')
            with more_menu(stage.name):
                menu_action('push_pin', 'Pin a match…', lambda: _pin_dialog(service, stage, refresh))
                if row.pinned:
                    menu_action('sync', 'Back to automatic', unpin)
                menu_link('open_in_new', 'Open overlay', overlay_path(overlay.token))
                ui.separator()
                menu_action('autorenew', 'New link…', lambda: ConfirmationDialog(
                    f'Retire {stage.name}\'s overlay link and make a new one? OBS shows nothing '
                    'until the new link is pasted into its browser source.',
                    on_confirm=renew, confirm_text='Make new link', title='New overlay link?',
                ).open(), negative=True)


async def _pin_dialog(service: StreamLayoutService, stage, refresh) -> None:
    try:
        matches = await service.stage_matches(await _actor(), stage.id)
    except (ValueError, PermissionError) as err:
        notify_error(err)
        return
    if not matches:
        ui.notify(f'No matches are booked on {stage.name} around now.', color='info')
        return
    with form_dialog(f'Pin a match to {stage.name}') as dialog:
        ui.label('The overlay shows this match until you set it back to automatic.') \
            .classes('text-caption text-muted')
        choice = ui.select({m.id: _match_label(m) for m in matches}, label='Match') \
            .props('outlined').classes('w-full')

        async def submit() -> None:
            if not choice.value:
                notify_error(ValueError('Pick a match.'))
                return
            try:
                await service.pin_match(await _actor(), choice.value)
            except (ValueError, PermissionError) as err:
                notify_error(err)
                return
            dialog.close()
            ui.notify(f'Pinned to {stage.name}.', color='positive')
            await refresh()

        with dialog_actions().classes('justify-end'):
            ui.button('Cancel', on_click=dialog.close).props('flat')
            ui.button('Pin', on_click=submit).props('color=primary')
        submit_on_enter(dialog, submit)
    dialog.open()


def _layout_row(service: StreamLayoutService, layout: StreamLayout, open_editor, refresh, refresh_stages) -> None:
    boxes = layout.boxes or []
    detail = f'{layout.width}×{layout.height} · {len(boxes)} box' + ('' if len(boxes) == 1 else 'es')
    if not layout.art_sha256:
        detail += ' · no art yet'

    async def duplicate() -> None:
        try:
            copy_ = await service.duplicate_layout(await _actor(), layout.id)
            ui.notify(f'Made “{copy_.name}”.', color='positive')
        except (ValueError, PermissionError) as err:
            notify_error(err)
        await refresh()
        await refresh_stages()

    async def delete() -> None:
        try:
            await service.delete_layout(await _actor(), layout.id)
            ui.notify(f'Deleted “{layout.name}”.', color='positive')
        except (ValueError, PermissionError) as err:
            notify_error(err)
        await refresh()
        await refresh_stages()

    with ui.element('div').classes('sl-list__row').style('cursor: default'):
        ui.icon('dashboard').classes('text-muted')
        with ui.column().classes('gap-0 min-w-0'):
            ui.label(layout.name).classes('text-weight-bold')
            ui.label(detail).classes('sl-list__sample')
        with ui.row().classes('items-center no-wrap gap-1'):
            ui.button('Edit', icon='edit', on_click=lambda: open_editor(layout.id)) \
                .props('outline color=primary no-caps')
            with more_menu(layout.name):
                menu_action('drive_file_rename_outline', 'Rename…',
                            lambda: _rename_dialog(service, layout, refresh))
                menu_action('content_copy', 'Duplicate', duplicate)
                ui.separator()
                menu_action('delete', 'Delete layout…', lambda: ConfirmationDialog(
                    f'Delete “{layout.name}”? Any stage using it shows a blank overlay until you pick another.',
                    on_confirm=delete, confirm_text='Delete', title='Delete layout?',
                ).open(), negative=True)


def _new_layout_dialog(service: StreamLayoutService, open_editor) -> None:
    with form_dialog('New layout') as dialog:
        name = ui.input('Name', placeholder='2-player race').props('outlined autofocus').classes('w-full')
        ui.label('Upload the art next. The canvas takes the art\'s size, so export it at your '
                 'stream resolution, with transparent holes where the video goes.') \
            .classes('text-caption text-muted')

        async def submit() -> None:
            try:
                layout = await service.create_layout(await _actor(), name.value)
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


def _rename_dialog(service: StreamLayoutService, layout: StreamLayout, after) -> None:
    with form_dialog('Rename layout') as dialog:
        name = ui.input('Name', value=layout.name).props('outlined autofocus').classes('w-full')

        async def submit() -> None:
            try:
                await service.rename_layout(await _actor(), layout.id, name.value)
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


class LayoutEditor:
    """One layout open for editing: art, boxes, box list and the box sheet."""

    def __init__(self, service: StreamLayoutService, layout: StreamLayout, back, reopen) -> None:
        self.service = service
        self.layout = layout
        self.back = back
        self.reopen = reopen
        self.boxes: list[dict] = copy.deepcopy(list(layout.boxes or []))
        self.saved_at = layout.updated_at
        self.selected: Optional[str] = None
        self.dirty = False
        self.preview_match: Optional[int] = None
        self.values: dict[str, str] = {}
        self.preview_options: dict = {0: 'Sample text'}

    # ── build ──────────────────────────────────────────────────────────

    async def build(self) -> None:
        actor = await _actor()
        try:
            self.preview_options |= {m.id: _match_label(m) for m in await self.service.preview_matches(actor)}
        except (ValueError, PermissionError) as err:
            notify_error(err)
        await self._load_values()

        ui.button('Layouts', icon='arrow_back', on_click=self._leave) \
            .props('flat no-caps color=primary').classes('self-start')
        with page_header(self.layout.name, self._subtitle()) as head:
            self.subtitle_row = head
            await help_icon('stream-layouts', user=actor)
            ui.space()
            ui.select(self.preview_options, value=0, label='Preview', on_change=self._preview_changed) \
                .props('outlined dense options-dense').classes('sl-preview-select').style('min-width: 220px')
            self.save_button = ui.button('Save', icon='save', on_click=busy_click('Saving…', self._save)) \
                .props('color=primary')
            with more_menu(self.layout.name):
                menu_action('upload', 'Replace art…' if self.layout.art_sha256 else 'Upload art…',
                            lambda: self.uploader.run_method('pickFiles'))
                menu_action('add_box', 'Add box…', self._add_dialog)
                menu_action('drive_file_rename_outline', 'Rename…',
                            lambda: _rename_dialog(self.service, self.layout, self._renamed))
                ui.separator()
                if self.layout.art_sha256:
                    menu_action('hide_image', 'Remove art…', lambda: ConfirmationDialog(
                        'Remove this layout\'s art? The boxes stay where they are.',
                        on_confirm=self._remove_art, confirm_text='Remove', title='Remove art?',
                    ).open(), negative=True)
                menu_action('delete', 'Delete layout…', lambda: ConfirmationDialog(
                    f'Delete “{self.layout.name}”? Any stage using it shows a blank overlay.',
                    on_confirm=self._delete, confirm_text='Delete', title='Delete layout?',
                ).open(), negative=True)

        # Quasar's uploader card fails axe, so it stays hidden behind the menu item.
        self.uploader = ui.upload(
            auto_upload=True, max_file_size=MAX_ART_BYTES, on_upload=self._uploaded,
            on_rejected=lambda: ui.notify('Layout art can be up to 10 MB, as a PNG, JPEG or WebP.',
                                          color='warning'),
        ).props(f'accept="{ART_ACCEPT}"').classes('hidden')

        if not self.layout.art_sha256:
            with ui.row().classes('items-center gap-2'):
                ui.label('No art yet. Upload a PNG with transparent holes where the video goes, '
                         'or add Video hole boxes to cut them.').classes('text-caption text-muted')
                ui.button('Upload art', icon='upload', on_click=lambda: self.uploader.run_method('pickFiles')) \
                    .props('outline color=primary no-caps')

        with ui.card().classes('w-full q-pa-sm'):
            self.canvas = ui.refreshable(self._render_canvas)
            self.canvas()
        with ui.row().classes('items-center w-full'):
            self.status = ui.label('').classes('text-caption text-muted')
            ui.space()
            ui.button('Add box…', icon='add', on_click=self._add_dialog).props('outline color=primary no-caps')
        self.box_list = ui.refreshable(self._render_list)
        self.box_list()

        with ui.dialog().props(
            ''':position="Quasar.Screen.lt.sm ? 'bottom' : 'right'" :seamless="!Quasar.Screen.lt.sm"'''
        ) as self.sheet, ui.card().classes('dialog-card match-sheet'):
            dialog_header('Box', self.sheet)
            self.sheet_body = ui.refreshable(self._render_sheet)
            self.sheet_body()
        self.sheet.on('hide', self._sheet_closed)
        self._mark()

    def _subtitle(self) -> str:
        return f'{self.layout.width}×{self.layout.height}'

    # ── canvas ─────────────────────────────────────────────────────────

    def _render_canvas(self) -> None:
        W, H = self.layout.width, self.layout.height
        canvas = ui.element('div').classes('sl-canvas').style(f'aspect-ratio: {W} / {H}')
        canvas.props(f'data-w={W} data-h={H}')
        with canvas:
            if self.layout.art_sha256:
                ui.element('img').classes('sl-canvas__art').props(
                    f'src="{app_href(art_url("", self.layout.id, self.layout.art_sha256))}" alt=""')
            for box in self.boxes:
                self._render_box(box)

    def _render_box(self, box: dict) -> None:
        W, H = self.layout.width, self.layout.height
        classes = 'sl-box'
        if box['kind'] == KIND_HOLE:
            classes += ' sl-box--hole'
        if box['id'] == self.selected:
            classes += ' sl-box--on'
        style = (f"left:{box['x'] / W * 100}%;top:{box['y'] / H * 100}%;"
                 f"width:{box['w'] / W * 100}%;height:{box['h'] / H * 100}%;")
        if box['kind'] == KIND_FIELD:
            if box['h'] < box['size'] * 2.2:
                classes += ' sl-box--one'
            style += (f"color:{box['color']};font-family:{FONTS[box['font']]};font-weight:{box['weight']};"
                      f"font-size:{box['size'] / W * 100}cqw;text-align:{box['align']};"
                      f"text-transform:{'uppercase' if box['upper'] else 'none'};"
                      f"text-shadow:{'0 2px 4px rgba(0,0,0,.85)' if box['shadow'] else 'none'};")
            if box['align'] != 'left':
                style += f"justify-content:{'center' if box['align'] == 'center' else 'flex-end'};"
        el = ui.element('div').classes(classes).style(style)
        el.props(f"data-id={box['id']} data-x={box['x']} data-y={box['y']} data-w={box['w']} data-h={box['h']} "
                 f'tabindex=0 role=button')
        el.props['aria-label'] = f'{box_label(box)}. Drag to move, arrow keys to nudge, Enter for settings.'
        with el:
            if box['kind'] == KIND_FIELD:
                with ui.element('span'):
                    ui.label(self.values.get(box['id'], '')).classes('inline')
            else:
                ui.label('Video')
            if box['id'] == self.selected:
                ui.element('div').classes('sl-handle').props('aria-hidden=true')

    # ── list ───────────────────────────────────────────────────────────

    def _render_list(self) -> None:
        ui.label(f'Boxes ({len(self.boxes)})').classes('wiz-panel__title q-mt-sm')
        if not self.boxes:
            ui.label('No boxes yet. Add one for each name, label or video feed on the art.') \
                .classes('italic-note')
            return
        with ui.element('div').classes('sl-list w-full'):
            for box in self.boxes:
                icon = 'crop_free' if box['kind'] == KIND_HOLE else _ICON.get(box.get('field', ''), 'text_fields')
                sample = 'Transparent; OBS video shows through' if box['kind'] == KIND_HOLE \
                    else (self.values.get(box['id']) or '(empty for this match)')
                row = ui.element('button').classes(
                    'sl-list__row' + (' sl-list__row--on' if box['id'] == self.selected else ''))
                row.props('type=button')
                row.on('click', lambda _, b=box['id']: self.select(b))
                with row:
                    ui.icon(icon).classes('text-muted')
                    with ui.element('span').classes('min-w-0'):
                        ui.label(box_label(box)).classes('text-weight-bold')
                        ui.label(sample).classes('sl-list__sample')
                    ui.label(f"{box['x']}, {box['y']} · {box['w']}×{box['h']}").classes('sl-list__pos')

    # ── sheet ──────────────────────────────────────────────────────────

    def _box(self) -> Optional[dict]:
        return next((b for b in self.boxes if b['id'] == self.selected), None)

    def _render_sheet(self) -> None:
        box = self._box()
        if box is None:
            return
        with ui.column().classes('match-sheet__body full-width gap-3'):
            ui.label(box_label(box)).classes('match-sheet__title')
            if box['kind'] == KIND_FIELD:
                self._field_controls(box)
            else:
                ui.label('OBS video shows through this rectangle; the art inside it is cut away.') \
                    .classes('text-caption text-muted')
                ui.number('Corner radius', value=box.get('radius', 0), min=0, step=1,
                          on_change=lambda e: self._set(box, radius=int(e.value or 0))) \
                    .props('outlined dense suffix=px')
            with ui.row().classes('w-full gap-2 no-wrap'):
                for key, label in (('x', 'X'), ('y', 'Y'), ('w', 'Width'), ('h', 'Height')):
                    ui.number(label, value=box[key], min=0, step=1,
                              on_change=lambda e, k=key: self._set(box, **{k: int(e.value or 0)})) \
                        .props('outlined dense').classes('col')
        with dialog_actions().classes('justify-end'):
            ui.button('Delete box', icon='delete', on_click=lambda: self._delete_box(box['id'])) \
                .props('flat color=negative no-caps')
            ui.space()
            ui.button('Duplicate', icon='content_copy', on_click=lambda: self._duplicate_box(box['id'])) \
                .props('outline color=primary no-caps')

    def _field_controls(self, box: dict) -> None:
        options = {f.key: f'{f.group} · {f.label}' for f in FIELDS}
        ui.select(options, value=box['field'], label='Shows',
                  on_change=lambda e: self._set(box, field=e.value, refresh_sheet=True)) \
            .props('outlined dense options-dense').classes('w-full')
        field = FIELDS_BY_KEY[box['field']]
        if field.per_player:
            # url-state: exempt — a form input on the box being edited, not a view
            ui.toggle({i: f'Player {i}' for i in range(1, MAX_PLAYERS + 1)}, value=box['which'],
                      on_change=lambda e: self._set(box, which=e.value)) \
                .props('no-caps dense unelevated no-wrap').classes('wiz-segmented')
        if field.key == 'custom_text':
            ui.input('Text', value=box.get('text', ''),
                     on_change=lambda e: self._set(box, text=e.value or '')) \
                .props('outlined dense maxlength=200 counter').classes('w-full')
        with ui.row().classes('w-full gap-2 no-wrap'):
            ui.select(list(FONTS), value=box['font'], label='Font',
                      on_change=lambda e: self._set(box, font=e.value)).props('outlined dense').classes('col')
            ui.number('Size', value=box['size'], min=6, max=400, step=1,
                      on_change=lambda e: self._set(box, size=int(e.value or 6))) \
                .props('outlined dense suffix=px').classes('col')
        with ui.row().classes('w-full gap-2 items-center no-wrap'):
            ui.color_input('Colour', value=box['color'],
                           on_change=lambda e: self._set_colour(box, e.value)) \
                .props('outlined dense').classes('col')
            # url-state: exempt — a form input on the box being edited, not a view
            ui.toggle({w: {400: 'Regular', 600: 'Semibold', 800: 'Bold'}[w] for w in WEIGHTS}, value=box['weight'],
                      on_change=lambda e: self._set(box, weight=e.value)) \
                .props('no-caps dense unelevated no-wrap').classes('wiz-segmented')
        with ui.row().classes('w-full gap-2 items-center'):
            # url-state: exempt — a form input on the box being edited, not a view
            ui.toggle({a: a.capitalize() for a in ALIGNS}, value=box['align'],
                      on_change=lambda e: self._set(box, align=e.value)) \
                .props('no-caps dense unelevated no-wrap').classes('wiz-segmented')
            ui.checkbox('Uppercase', value=box['upper'], on_change=lambda e: self._set(box, upper=e.value))
            ui.checkbox('Shadow', value=box['shadow'], on_change=lambda e: self._set(box, shadow=e.value))

    def _set_colour(self, box: dict, value: Optional[str]) -> None:
        value = (value or '').strip()
        if len(value) == 7 and value.startswith('#'):
            self._set(box, color=value.upper())

    def _sheet_closed(self) -> None:
        self.selected = None
        self._repaint()

    # ── changes ────────────────────────────────────────────────────────

    def select(self, box_id: Optional[str]) -> None:
        if box_id is None or not any(b['id'] == box_id for b in self.boxes):
            return
        self.selected = box_id
        self._repaint()
        self.sheet_body.refresh()
        self.sheet.open()

    def moved(self, args: dict) -> None:
        box = next((b for b in self.boxes if b['id'] == args.get('id')), None)
        if box is None:
            return
        W, H = self.layout.width, self.layout.height
        try:
            w = max(4, min(W, int(args['w'])))
            h = max(4, min(H, int(args['h'])))
            box.update(x=max(0, min(W - w, int(args['x']))), y=max(0, min(H - h, int(args['y']))), w=w, h=h)
        except (KeyError, TypeError, ValueError):
            return
        self.selected = box['id']
        self.dirty = True
        self._repaint()
        if self.sheet.value:
            self.sheet_body.refresh()

    def _set(self, box: dict, *, refresh_sheet: bool = False, **changes) -> None:
        box.update(changes)
        W, H = self.layout.width, self.layout.height
        box['w'], box['h'] = max(4, min(W, box['w'])), max(4, min(H, box['h']))
        box['x'], box['y'] = max(0, min(W - box['w'], box['x'])), max(0, min(H - box['h'], box['y']))
        self.dirty = True
        if 'field' in changes or 'which' in changes or 'text' in changes:
            self._refresh_values()
        self._repaint()
        if refresh_sheet:
            self.sheet_body.refresh()

    def _add_dialog(self) -> None:
        with form_dialog('Add box') as dialog:
            def add(kind: str, key: Optional[str] = None) -> None:
                box = self.service.new_box(self.layout, kind, key)
                self.boxes.append(box)
                dialog.close()
                self.dirty = True
                self._refresh_values()
                self.select(box['id'])

            with ui.column().classes('gap-0 w-full'):
                ui.label('Video').classes('match-sheet__section')
                with ui.element('button').classes('sl-list__row').props('type=button') as hole:
                    ui.icon('crop_free').classes('text-muted')
                    with ui.element('span'):
                        ui.label('Video hole').classes('text-weight-bold')
                        ui.label('Cuts the art away so OBS video shows through').classes('sl-list__sample')
                    ui.label('')
                hole.on('click', lambda: add(KIND_HOLE))
                group = None
                for f in FIELDS:
                    if f.group != group:
                        group = f.group
                        ui.label(group).classes('match-sheet__section q-mt-sm')
                    with ui.element('button').classes('sl-list__row').props('type=button') as item:
                        ui.icon(f.icon).classes('text-muted')
                        with ui.element('span'):
                            ui.label(f.label).classes('text-weight-bold')
                            ui.label(f.sample).classes('sl-list__sample')
                        ui.label('')
                    item.on('click', lambda _, k=f.key: add(KIND_FIELD, k))
        dialog.open()

    def _delete_box(self, box_id: str) -> None:
        self.boxes = [b for b in self.boxes if b['id'] != box_id]
        self.dirty = True
        self.sheet.close()
        self._repaint()

    def _duplicate_box(self, box_id: str) -> None:
        source = next((b for b in self.boxes if b['id'] == box_id), None)
        if source is None:
            return
        twin = copy.deepcopy(source)
        twin['id'] = self.service.new_box(self.layout, KIND_HOLE)['id']
        twin['x'] = min(self.layout.width - twin['w'], twin['x'] + 16)
        twin['y'] = min(self.layout.height - twin['h'], twin['y'] + 16)
        self.boxes.append(twin)
        self.dirty = True
        self._refresh_values()
        self.select(twin['id'])

    # ── preview text ───────────────────────────────────────────────────

    async def _load_values(self) -> None:
        try:
            self.values = await self.service.preview_values(
                await _actor(), self.layout.id, self.preview_match, self.boxes)
        except (ValueError, PermissionError) as err:
            notify_error(err)

    def _refresh_values(self) -> None:
        async def go() -> None:
            await self._load_values()
            self._repaint()
        ui.timer(0, go, once=True)

    async def _preview_changed(self, e) -> None:
        self.preview_match = e.value or None
        await self._load_values()
        self._repaint()

    # ── save and art ───────────────────────────────────────────────────

    def _repaint(self) -> None:
        self.canvas.refresh()
        self.box_list.refresh()
        self._mark()

    def _mark(self) -> None:
        self.status.text = 'Unsaved changes' if self.dirty else 'All changes saved'

    async def _save(self) -> None:
        try:
            layout = await self.service.save_boxes(await _actor(), self.layout.id, self.boxes, self.saved_at)
        except (ValueError, PermissionError) as err:
            notify_error(err)
            return
        self.layout = layout
        self.saved_at = layout.updated_at
        self.boxes = copy.deepcopy(list(layout.boxes or []))
        self.dirty = False
        ui.notify('Layout saved. Overlays pick it up within a few seconds.', color='positive')
        self._repaint()

    async def _uploaded(self, e: events.UploadEventArguments) -> None:
        data = await e.file.read()
        try:
            if self.dirty:
                await self._save()
            layout, transparent = await self.service.set_art(await _actor(), self.layout.id, data)
        except (ValueError, PermissionError) as err:
            notify_error(err)
            self.uploader.reset()
            return
        self.uploader.reset()
        if not transparent and not any(b['kind'] == KIND_HOLE for b in layout.boxes or []):
            ui.notify('Art saved. It has no transparent areas, so add Video hole boxes where the feeds go.',
                      color='info')
        else:
            ui.notify('Art saved.', color='positive')
        await self.reopen(layout.id)

    async def _remove_art(self) -> None:
        try:
            await self.service.remove_art(await _actor(), self.layout.id)
        except (ValueError, PermissionError) as err:
            notify_error(err)
            return
        await self.reopen(self.layout.id)

    async def _delete(self) -> None:
        try:
            await self.service.delete_layout(await _actor(), self.layout.id)
        except (ValueError, PermissionError) as err:
            notify_error(err)
            return
        ui.notify(f'Deleted “{self.layout.name}”.', color='positive')
        await self.back()

    async def _renamed(self) -> None:
        await self.reopen(self.layout.id)

    async def _leave(self) -> None:
        if self.dirty:
            ConfirmationDialog(
                'Leave without saving? Your box changes will be lost.',
                on_confirm=self.back, confirm_text='Leave', title='Unsaved changes',
            ).open()
            return
        await self.back()
