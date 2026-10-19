"""The live Watch page's body.

Renders a :class:`theme.watch_view.WatchView`, the same content the static
rendering draws: the player, a tile per stage led by its Twitch still, then
"Coming up" beside the tournaments.

:meth:`LiveWatch.render` repaints everything but the player, so a change to the
schedule never restarts the video.
"""

from typing import Dict, FrozenSet, Optional

from nicegui import ui

from application.utils.safe_url import http_url_or_empty
from pages.admin_tabs.links import SCHEDULE, admin_url
from theme.menu import menu_action, more_menu
from theme.twitch_theater import NowPlaying, TwitchTheater
from theme.watch_view import (
    TAP_HINT,
    TILES_JS,
    WatchMatch,
    WatchStage,
    WatchView,
    bracket_href,
    live_chip_text,
    since_epoch,
    tile_badge,
    tile_meta,
)


def _now_playing(stage: WatchStage) -> NowPlaying:
    now = stage.on_now
    return NowPlaying(
        stage_id=stage.id,
        stage_name=stage.name,
        channel_name=stage.channel_name or '',
        channel=stage.channel or '',
        stream_url=stage.stream_url,
        time=now.time if now else '',
        players=now.players if now else '',
        tournament=now.tournament if now else '',
    )


def _open_in_schedule(match: WatchMatch, board_scope: Optional[FrozenSet[int]]) -> None:
    """The ⋮ for whoever runs the board, only where their board can show this match.

    ``board_scope`` is :meth:`AuthService.schedule_board_scope`: ``None`` is
    every match, a set narrows it to a TA's or CC's tournaments.
    """
    if not match.match_id:
        return
    if board_scope is not None and match.tournament_id not in board_scope:
        return
    with more_menu(match.players):
        menu_action(
            'calendar_month', 'Open in Schedule',
            lambda m=match.match_id: ui.navigate.to(admin_url(SCHEDULE, match_id=m)),
        )


def _span(text: str, classes: str) -> ui.label:
    label = ui.label(text).classes(classes)
    label.tag = 'span'
    return label


def _thumb(stage: WatchStage) -> None:
    """The still, its LIVE pill and caption: the static tile's markup, element by element."""
    live, glass = tile_badge(stage)
    _span('Watch ', 'wiz-sr-only')
    ui.element('img').classes('wiz-tile__img').props(
        f'src="{stage.preview}" data-thumb="{stage.preview}" alt="" '
        'loading="lazy" referrerpolicy="no-referrer"'
    )
    ui.element('span').classes('wiz-tile__shade')
    with ui.element('span').classes('wiz-tile__badges'):
        if live:
            with ui.element('span').classes('wiz-onair'):
                ui.element('span').classes('wiz-onair__dot')
                _span(live, '')
        badge = _span(glass, 'wiz-tile__glass')
        since = since_epoch(stage.on_now.started_at) if live and stage.on_now else ''
        if since:
            badge.props(f'data-since="{since}"')
    with ui.element('span').classes('wiz-tile__caption'):
        _span(stage.name, 'wiz-tile__stage')
        if stage.channel_name:
            _span(f'twitch.tv/{stage.channel_name}', 'wiz-tile__channel')
    with ui.element('span').classes('wiz-tile__play'):
        ui.icon('play_arrow')
    _span('Watching', 'wiz-tile__watching')


class LiveWatch:
    def __init__(
        self,
        root_path: str = '',
        board_scope: Optional[FrozenSet[int]] = frozenset(),
    ) -> None:
        self.root_path = root_path
        self.board_scope = board_scope
        self._buttons: Dict[int, ui.element] = {}
        ui.add_body_html(TILES_JS)
        with ui.column().classes('wiz-watch-main'):
            self.theater = TwitchTheater(on_change=self._paint_buttons)
            self.content = ui.column().classes('wiz-watch w-full')

    def render(self, view: WatchView) -> None:
        self._buttons.clear()
        self.content.clear()
        with self.content:
            self._stages(view)
            with ui.element('div').classes('wiz-watch-two'):
                self._coming(view)
                self._tournaments(view)
        watched = next(
            (s for s in view.stages if self.theater.is_watching(s.id) and s.channel), None,
        )
        if watched:
            self.theater.refresh(_now_playing(watched))
        self._paint_buttons()

    def _stages(self, view: WatchView) -> None:
        with ui.element('div').classes('wiz-tiles-head'):
            with ui.element('h2').classes('wiz-watch__h2'):
                ui.label('On stream now').tag = 'span'
                chip = live_chip_text(view)
                if chip:
                    with ui.element('span').classes('wiz-onair-chip'):
                        ui.element('span').classes('wiz-onair__dot')
                        _span(chip, '')
            if self.theater.available and any(s.channel for s in view.stages):
                _span(TAP_HINT, 'wiz-tiles-hint')
        if view.stages:
            with ui.element('div').classes('wiz-tiles'):
                for stage in view.stages:
                    self._stage(stage)
        else:
            ui.label('Nothing is on a stream right now. Check back later.').classes('empty-state')

    def _stage(self, stage: WatchStage) -> None:
        with ui.element('article').classes('wiz-tile'):
            if stage.channel and self.theater.available:
                thumb = ui.element('button').classes('wiz-tile__thumb').props('type=button aria-pressed=false')
                thumb.on('click', lambda s=stage: self.theater.watch(_now_playing(s)))
                self._buttons[stage.id] = thumb
                with thumb:
                    _thumb(stage)
            elif stage.channel and stage.stream_url:
                link = ui.link(target=stage.stream_url, new_tab=True).classes('wiz-tile__thumb')
                with link:
                    _thumb(stage)
            else:
                with ui.element('div').classes('wiz-tile__plain'):
                    with ui.column().classes('stage-title'):
                        ui.label(stage.name).classes('stage-name')
                    if stage.stream_url:
                        with ui.link(target=stage.stream_url, new_tab=True).classes('stage-link'):
                            ui.label('Open stream')
                            ui.icon('open_in_new', size='16px')
            with ui.element('div').classes('wiz-tile__now'):
                now = stage.on_now
                if now:
                    with ui.element('div').classes('wiz-tile__now-top'):
                        ui.label(now.players).classes('wiz-tile__players')
                        _open_in_schedule(now, self.board_scope)
                    ui.label(tile_meta(now)).classes('wiz-tile__meta')
                    for label, names in (('Commentary', now.commentators), ('Tracking', now.trackers)):
                        if names:
                            with ui.element('div').classes('wiz-tile__crew'):
                                ui.icon('mic').props(f'aria-label="{label}"')
                                ui.label(f'{label}: {names}')
                else:
                    ui.label('Nothing on this stage right now.').classes('wiz-tile__meta')
            if stage.up_next:
                ui.label('Up next').classes('wiz-tile__label')
                for match in stage.up_next:
                    with ui.element('div').classes('wiz-tile__next'):
                        _span(match.time, 'wiz-tile__next-time')
                        _span(match.players, 'wiz-tile__next-players')
                        _span(match.tournament, 'wiz-tile__next-tourn')
                        _open_in_schedule(match, self.board_scope)

    def _coming(self, view: WatchView) -> None:
        with ui.element('section').classes('wiz-panel'):
            with ui.element('div').classes('wiz-panel__body'):
                ui.label('Coming up on stream').classes('wiz-watch__h2').tag = 'h2'
                if not view.coming:
                    ui.label('Nothing else is scheduled on stream yet.').classes('text-muted')
                for day in view.coming:
                    ui.label(day.label).classes('wiz-watch-day')
                    for match in day.matches:
                        with ui.element('div').classes('wiz-watch-row'):
                            ui.label(match.time).classes('match-time')
                            with ui.element('div').classes('wiz-watch-row__main'):
                                ui.label(match.players).classes('wiz-watch-row__players')
                                if match.tournament:
                                    ui.label(match.tournament).classes('text-muted')
                            ui.label(match.stage).classes('wiz-watch-tag')
                            _open_in_schedule(match, self.board_scope)

    def _tournaments(self, view: WatchView) -> None:
        with ui.element('section').classes('wiz-panel'):
            with ui.element('div').classes('wiz-panel__body'):
                ui.label('Tournaments').classes('wiz-watch__h2').tag = 'h2'
                if not view.tournaments:
                    ui.label('No brackets are up yet.').classes('text-muted')
                for t in view.tournaments:
                    with ui.element('div').classes('wiz-watch-tourn'):
                        with ui.element('div').classes('wiz-watch-tourn__line'):
                            ui.label(t.name).classes('wiz-watch-row__players')
                            if t.status:
                                ui.label(t.status).classes(f'wiz-chip wiz-chip--{t.tone}')
                        if t.format:
                            ui.label(t.format).classes('text-muted')
                        with ui.element('div').classes('wiz-watch-links'):
                            href = bracket_href(t.bracket_url, self.root_path)
                            if href:
                                ui.link('Bracket ↗', href, new_tab=True)
                            rules = http_url_or_empty(t.rules_url)
                            if rules:
                                ui.link('Rules ↗', rules, new_tab=True)

    def _paint_buttons(self) -> None:
        for stage_id, button in self._buttons.items():
            on = self.theater.is_watching(stage_id)
            button.props(f'aria-pressed={"true" if on else "false"}')
