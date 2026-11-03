"""The ALTTP item tracker: the crew's board and the OBS overlay it feeds.

``/tracker/<match id>`` is for the person tracking a runner for the stream (an
approved Tracker on the match, Staff, or a Broadcast Tech), never the runner.
Layout C (Thomas, 2026-10-26): the board, an "On stream" preview of what viewers
see after the delay, and a change log where any line can be undone. Plain items
toggle on a tap; items with levels and dungeons open a picker (a bottom sheet
on a phone).

``/tracker/overlay/<token>`` is the transparent page an OBS browser source
loads: one runner's board as the stream should show it, no chrome, no
background. It authorizes on the board's token, since OBS has no session.
"""

from typing import Callable, Optional

from nicegui import app, ui

from application.errors import NotFoundError
from application.services import AuthService, ItemTrackerService, get_user_from_session
from application.services.item_tracker_catalog import (
    DUNGEON_ITEM_LABELS,
    MEDALLION_SPRITES,
    MEDALLIONS,
    PRIZE_SPRITES,
    PRIZES,
    DungeonSpec,
    ItemSpec,
    chest_total,
)
from application.services.item_tracker_service import MAX_DELAY_SECONDS, RunnerBoard, TrackerView
from application.utils.app_links import ITEM_TRACKER, ITEM_TRACKER_OVERLAY
from application.utils.timezone import format_local_datetime
from middleware.auth import protected_page, public_page
from models import FeatureFlag, User
from theme.assets import asset_url
from theme.banner import banner
from theme.base import BaseLayout
from theme.clipboard import copy_to_clipboard
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet
from theme.dialog.confirmation_dialog import ConfirmationDialog
from theme.empty_state import empty_state
from theme.item_tracker_board import BoardView, sprite
from theme.menu import menu_action
from theme.notify import notify_error
from theme.page_header import page_header
from theme.section import section_panel
from theme.timer_teardown import PageTimer
from theme.url_state import bind_url, url_state

__all__ = ['create']

_POLL_SECONDS = 1.0


def _stylesheet() -> None:
    ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/item_tracker.css")}">')


class TrackerPage:
    """One match's tracker for one viewer: which runner is open, and the repaint loop."""

    def __init__(self, view: TrackerView, user: User) -> None:
        self.view = view
        self.user = user
        self.service = ItemTrackerService()
        self.runner: RunnerBoard = view.runners[0]
        self.versions = {r.board.id: r.board.version for r in view.runners}
        self.board_view: Optional[BoardView] = None
        self.preview_view: Optional[BoardView] = None
        self.preview_state: dict = {}
        self._tags: Optional[ui.element] = None
        self._log: Optional[ui.element] = None
        self._picker = ui.dialog()
        mobile_sheet(self._picker)

    async def render(self) -> None:
        with page_header('Item tracker', subtitle=self.view.title):
            ui.space()
            ui.button('Undo', icon='undo', on_click=self._undo_last).props('outline no-caps color=primary')
            with ui.button('Actions').props('flat no-caps color=primary icon-right=expand_more'):
                with ui.menu():
                    menu_action('link', 'Copy OBS link', self._copy_link)
                    menu_action('timer', 'Stream delay…', self._open_delay)
                    menu_action('key', 'Dungeon items…', self._open_dungeon_items)
                    ui.separator()
                    menu_action('restart_alt', 'Reset board…', self._confirm_reset, negative=True)

        if len(self.view.runners) > 1:
            options = {str(r.runner.id): r.name for r in self.view.runners}
            asked = url_state().requested('runner')
            first = asked if asked in options else next(iter(options))
            toggle = ui.toggle(options, value=first, on_change=lambda e: self._switch(e.value)) \
                .props('no-caps dense unelevated').classes('wiz-segmented')
            bind_url('runner', toggle, default=next(iter(options)), label=lambda v: options.get(v))
            self.runner = next(r for r in self.view.runners if str(r.runner.id) == first)

        self._tags = ui.element('div').classes('itk-tags')
        self._paint_tags()

        with ui.element('div').classes('itk-layout'):
            board = await section_panel('Board', icon='grid_view')
            with board.body:
                self.board_view = BoardView(on_item=self._tap_item, on_dungeon=self._open_dungeon)
                ui.label('Tap an item to mark it found or not. Items with levels and dungeons open a picker.') \
                    .classes('itk-hint')
            with ui.element('div').classes('itk-side'):
                preview = await section_panel(
                    'On stream', icon='live_tv', subtitle='What viewers see, after the delay.',
                )
                with preview.body:
                    with ui.element('div').classes('itk-preview full-width'):
                        self.preview_view = BoardView(bare=True)
                changes = await section_panel('Changes', icon='history')
                with changes.body:
                    self._log = ui.element('div').classes('itk-log')
        self._rebuild()
        await self._refresh_preview()
        PageTimer(_POLL_SECONDS, self._poll)

    def _rebuild(self) -> None:
        assert self.board_view is not None and self.preview_view is not None
        self.board_view.build(self.view.items)
        self.board_view.paint(self.runner.board.state)
        self.preview_view.build(self.view.items)
        self.preview_view.paint(self.preview_state)
        self._paint_log()

    def _paint_tags(self) -> None:
        assert self._tags is not None
        self._tags.clear()
        source = {'seed': 'from the seed', 'override': 'set here', 'default': 'no seed yet'}[self.view.items_source]
        with self._tags:
            for tag in self.view.seed_tags:
                ui.badge(tag, color='grey')
            ui.badge(f'{self.view.items.label} · {source}', color='grey')
            delay = self.view.delay_seconds
            ui.badge(f'On stream · {delay}s delay' if delay else 'On stream · no delay', color='positive')

    def _paint_log(self) -> None:
        assert self._log is not None
        self._log.clear()
        with self._log:
            if not self.runner.log:
                empty_state('Nothing marked yet.')
                return
            for entry in self.runner.log:
                with ui.element('div').classes('itk-log__row'):
                    ui.label(format_local_datetime(entry.at, '%H:%M:%S')).classes('itk-log__time')
                    ui.label(entry.text).classes('itk-log__text')
                    if entry.can_undo:
                        ui.button('Undo', on_click=lambda c=entry.id: self._undo(c)) \
                            .props(f'flat dense no-caps color=primary aria-label="Undo {entry.text}"')

    async def _poll(self) -> None:
        try:
            versions = await self.service.versions(self.view.match.id, self.user)
        except ValueError:
            return
        if versions.get(self.runner.board.id) != self.versions.get(self.runner.board.id):
            await self._reload()
        self.versions = versions
        await self._refresh_preview()

    async def _reload(self, *, whole: bool = False) -> None:
        if whole:
            self.view = await self.service.open_match(self.view.match.id, self.user)
            self.runner = next(r for r in self.view.runners if r.board.id == self.runner.board.id)
            self._paint_tags()
            self._rebuild()
            return
        self.runner = await self.service.board(self.runner.board.id, self.user)
        for i, r in enumerate(self.view.runners):
            if r.board.id == self.runner.board.id:
                self.view.runners[i] = self.runner
        self.versions[self.runner.board.id] = self.runner.board.version
        assert self.board_view is not None
        if self.runner.board.dungeon_items != self.view.runners[0].board.dungeon_items or \
                self.runner.board.delay_seconds != self.view.delay_seconds:
            await self._reload(whole=True)
            return
        self.board_view.paint(self.runner.board.state)
        self._paint_log()

    async def _refresh_preview(self) -> None:
        try:
            state = await self.service.preview(self.runner.board.id, self.user)
        except ValueError:
            return
        if state != self.preview_state:
            self.preview_state = state
            assert self.preview_view is not None
            self.preview_view.paint(state)

    async def _switch(self, runner_id: str) -> None:
        self.runner = next(r for r in self.view.runners if str(r.runner.id) == runner_id)
        self.preview_state = {}
        await self._reload()
        await self._refresh_preview()

    async def _write(self, action: Callable) -> None:
        try:
            await action()
        except ValueError as e:
            notify_error(e)
            return
        await self._reload()
        await self._refresh_preview()

    async def _set(self, key: str, level: int) -> None:
        await self._write(lambda: self.service.set_level(self.runner.board.id, key, level, self.user))

    async def _tap_item(self, item: ItemSpec) -> None:
        level = int(self.runner.board.state.get(item.key, 0))
        if item.picker:
            self._open_item_picker(item, level)
        else:
            await self._set(item.key, 0 if level else 1)

    def _choice(self, label: str, stem: Optional[str], current: bool, on_pick: Callable) -> None:
        tile = ui.element('button').classes('itk-tile itk-choice' + (' itk-tile--open' if current else '')) \
            .props(f'type=button aria-pressed={"true" if current else "false"}')
        tile.on('click', on_pick)
        with tile:
            if stem:
                ui.element('img').props(f'src="{sprite(stem)}" alt=""')
            else:
                ui.icon('block')
            ui.label(label).classes('itk-choice__label')

    def _picker_shell(self, title: str):
        self._picker.clear()
        with self._picker, ui.card().classes('dialog-card dialog-card--sm'):
            dialog_header(title, self._picker)
            body = ui.column().classes('full-width gap-3')
        return body

    def _pick(self, key: str, level: int, reopen: Optional[Callable[[], None]] = None) -> Callable:
        """Set ``key`` from a picker; ``reopen`` keeps the sheet up and redraws it."""
        async def go() -> None:
            if reopen is None:
                self._picker.close()
            await self._set(key, level)
            if reopen is not None and self._picker.value:
                reopen()
        return go

    def _open_item_picker(self, item: ItemSpec, current: int) -> None:
        assert item.levels is not None
        with self._picker_shell(item.label):
            with ui.element('div').classes('itk-choices'):
                for level, name in enumerate(item.levels):
                    stem = item.sprites[level] if level or item.starts_owned else None
                    self._choice(name, stem, level == current, self._pick(item.key, level))
        self._picker.open()

    def _stepper(self, label: str, key: str, value: int, top: int, shown: Callable[[int], str],
                 reopen: Callable[[], None]) -> None:
        with ui.row().classes('items-center justify-between full-width no-wrap'):
            ui.label(label).classes('text-weight-bold')
            with ui.element('div').classes('itk-stepper'):
                ui.button(icon='remove', on_click=self._pick(key, value - 1, reopen)) \
                    .props(f'round outline color=primary aria-label="One less: {label}"') \
                    .set_enabled(value > 0)
                ui.label(shown(value)).classes('itk-stepper__value')
                ui.button(icon='add', on_click=self._pick(key, value + 1, reopen)) \
                    .props(f'round outline color=primary aria-label="One more: {label}"') \
                    .set_enabled(value < top)

    def _open_dungeon(self, dungeon: DungeonSpec) -> None:
        state = self.runner.board.state
        items = self.view.items
        k = dungeon.key

        def again() -> None:
            self._open_dungeon(dungeon)

        with self._picker_shell(dungeon.name):
            if dungeon.boss is not None:
                beaten = bool(state.get(f'{k}.boss'))
                ui.button('Boss beaten' if not beaten else 'Mark boss alive',
                          icon='check_circle' if not beaten else 'undo',
                          on_click=self._pick(f'{k}.boss', 0 if beaten else 1, again)) \
                    .props('unelevated no-caps color=primary' if not beaten else 'outline no-caps color=primary') \
                    .classes('full-width')
            total = chest_total(dungeon, items)
            if total:
                found = int(state.get(f'{k}.chests', 0))
                self._stepper('Items left', f'{k}.chests', found, total, lambda f: f'{total - f} of {total}', again)
            if items.small_keys and dungeon.small_keys:
                self._stepper('Small keys', f'{k}.keys', int(state.get(f'{k}.keys', 0)), dungeon.small_keys,
                              lambda v: f'{v} of {dungeon.small_keys}', again)
            if items.big_keys and dungeon.big_key:
                has = bool(state.get(f'{k}.big_key'))
                with ui.element('div').classes('itk-choices'):
                    self._choice('No big key', 'bigkey0', not has, self._pick(f'{k}.big_key', 0, again))
                    self._choice('Big key', 'bigkey1', has, self._pick(f'{k}.big_key', 1, again))
            if dungeon.prize:
                ui.label('Prize').classes('text-weight-bold')
                current = int(state.get(f'{k}.prize', 0))
                with ui.element('div').classes('itk-choices'):
                    for level, name in enumerate(PRIZES):
                        self._choice(name, PRIZE_SPRITES[level], level == current, self._pick(f'{k}.prize', level, again))
            if dungeon.medallion:
                ui.label('Medallion to enter').classes('text-weight-bold')
                current = int(state.get(f'{k}.medallion', 0))
                with ui.element('div').classes('itk-choices'):
                    for level, name in enumerate(MEDALLIONS):
                        self._choice(name, MEDALLION_SPRITES[level], level == current,
                                     self._pick(f'{k}.medallion', level, again))
        if not self._picker.value:
            self._picker.open()

    async def _undo(self, change_id: int) -> None:
        await self._write(lambda: self.service.undo(self.runner.board.id, change_id, self.user))

    async def _undo_last(self) -> None:
        async def go() -> None:
            if await self.service.undo_last(self.runner.board.id, self.user) is None:
                ui.notify('Nothing to undo.', color='info')
        await self._write(go)

    async def _copy_link(self) -> None:
        try:
            link = await self.service.overlay_link(self.runner.board.id, self.user)
        except ValueError as e:
            notify_error(e)
            return
        copy_to_clipboard(link, f'OBS link for {self.runner.name}')

    def _confirm_reset(self) -> None:
        async def reset() -> None:
            await self._write(lambda: self.service.reset(self.runner.board.id, self.user))

        ConfirmationDialog(
            f"This clears everything marked for {self.runner.name} and the change log, "
            'and the stream shows an empty board once the delay passes.',
            on_confirm=reset, confirm_text='Reset board', title=f'Reset {self.runner.name}?',
        ).open()

    def _open_delay(self) -> None:
        with self._picker_shell('Stream delay'):
            ui.label('How far the stream copy runs behind the board, for every runner on this match. Match it '
                     'to the stream delay so viewers never see an item before the runner picks it up.') \
                .classes('text-muted')
            seconds = ui.number('Delay in seconds', value=self.view.delay_seconds, min=0, max=MAX_DELAY_SECONDS,
                                step=5, format='%d').props('outlined dense').classes('full-width')
            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=self._picker.close).props('flat no-caps color=primary')
                ui.button('Save', on_click=lambda: self._set_delay(int(seconds.value or 0))()) \
                    .props('unelevated no-caps color=primary')
        self._picker.open()

    def _set_delay(self, seconds: int) -> Callable:
        async def go() -> None:
            self._picker.close()
            try:
                await self.service.set_delay(self.view.match.id, seconds, self.user)
            except ValueError as e:
                notify_error(e)
                return
            await self._reload(whole=True)
        return go

    def _open_dungeon_items(self) -> None:
        current = self.runner.board.dungeon_items
        with self._picker_shell('Dungeon items'):
            ui.label("Which dungeon items are shuffled. Normally read from the match's seed; set it here for a "
                     'seed rolled outside Wizzrobe.').classes('text-muted')
            options = {'': 'From the seed', **DUNGEON_ITEM_LABELS}
            pick = ui.radio(options, value=current or '').classes('full-width')
            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=self._picker.close).props('flat no-caps color=primary')
                ui.button('Save', on_click=lambda: self._save_mode(pick.value or None)) \
                    .props('unelevated no-caps color=primary')
        self._picker.open()

    async def _save_mode(self, mode: Optional[str]) -> None:
        self._picker.close()
        try:
            await self.service.set_dungeon_items(self.view.match.id, mode, self.user)
        except ValueError as e:
            notify_error(e)
            return
        await self._reload(whole=True)


async def _overlay_board(token: str, show_dungeons: bool) -> None:
    service = ItemTrackerService()
    view = await service.overlay(token)
    if view is None:
        raise NotFoundError('Tracker board')
    ui.add_head_html(f'<link rel="stylesheet" href="{asset_url("css/styles.css")}">')
    ui.query('body').classes('itk-overlay')
    ui.dark_mode(None)
    with ui.element('div').classes('itk-overlay__board'):
        board = BoardView(bare=True, show_dungeons=show_dungeons)
    board.build(view.items)
    board.paint(view.state)
    seen = {'mode': view.items.mode}

    async def repaint() -> None:
        try:
            fresh = await service.overlay(token)
        except ValueError:
            return
        if fresh is None:
            return
        if fresh.items.mode != seen['mode']:
            seen['mode'] = fresh.items.mode
            board.build(fresh.items)
        board.paint(fresh.state)

    PageTimer(_POLL_SECONDS, repaint)


def create() -> None:
    @protected_page(ITEM_TRACKER, feature=FeatureFlag.ITEM_TRACKER, telemetry_path='/tracker')
    async def item_tracker(match_id: int) -> None:
        user = await get_user_from_session(app.storage.user)
        if user is None:
            ui.label('Your account could not be found.').classes('text-error')
            return
        ui.page_title('Item tracker')
        _stylesheet()
        await BaseLayout(user=user, show_admin=await AuthService.can_open_admin(user)).render()
        try:
            view = await ItemTrackerService().open_match(match_id, user)
        except NotFoundError:
            from theme.error_page import render_not_found
            await render_not_found(user=user)
            return
        except ValueError as e:
            with ui.column().classes('page-container w-full gap-3'):
                page_header('Item tracker')
                with banner(str(e), tone='warning'):
                    pass
            return
        with ui.column().classes('page-container-wide w-full gap-3'):
            await TrackerPage(view, user).render()

    @public_page(ITEM_TRACKER_OVERLAY, feature=FeatureFlag.ITEM_TRACKER, telemetry_path='/tracker/overlay')
    async def item_tracker_overlay(token: str, dungeons: int = 1) -> None:
        ui.page_title('Item tracker overlay')
        _stylesheet()
        try:
            await _overlay_board(token, bool(dungeons))
        except NotFoundError:
            from theme.error_page import render_not_found
            await render_not_found(user=None)
