"""The live Watch page's body.

Renders a :class:`theme.watch_view.WatchView`, the same content the static
rendering draws: the player, the stage columns, then "Coming up" beside the
tournaments.

:meth:`LiveWatch.render` repaints everything but the player, so a change to the
schedule never restarts the video.
"""

from typing import Dict, FrozenSet, Optional

from nicegui import ui

from application.utils.clients.sglive_client import SGLIVE_URL
from application.utils.money import format_money
from application.utils.safe_url import http_url_or_empty
from pages.admin_tabs.links import SCHEDULE, admin_url
from theme.menu import menu_action, more_menu
from theme.twitch_theater import NowPlaying, TwitchTheater
from theme.watch_view import (
    WatchMatch,
    WatchStage,
    WatchView,
    bracket_href,
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


def _channel_label(stage: WatchStage) -> None:
    if stage.channel_name:
        ui.label(f'twitch.tv/{stage.channel_name}').classes('stage-channel')


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


def _match_card(match: WatchMatch, *, live: bool, board_scope: Optional[FrozenSet[int]]) -> None:
    with ui.card().classes('match-card'):
        with ui.row().classes('match-card-top items-center no-wrap'):
            ui.label(match.time).classes('match-time')
            if live:
                ui.label('On now').classes('wiz-chip wiz-chip--live')
            else:
                ui.label('Scheduled').classes('wiz-chip wiz-chip--neutral')
            ui.space()
            _open_in_schedule(match, board_scope)
        ui.label(match.players).classes('match-players')
        if match.tournament:
            ui.label(match.tournament).classes('match-tournament')
        for icon, label, names in (
            ('mic', 'Commentary', match.commentators),
            ('track_changes', 'Tracking', match.trackers),
        ):
            if names:
                with ui.row().classes('match-crew no-wrap'):
                    ui.icon(icon).props(f'aria-label="{label}"')
                    ui.label(names)


class LiveWatch:
    def __init__(
        self,
        root_path: str = '',
        board_scope: Optional[FrozenSet[int]] = frozenset(),
    ) -> None:
        self.root_path = root_path
        self.board_scope = board_scope
        self._buttons: Dict[int, ui.button] = {}
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
        ui.label('On stream today').classes('wiz-watch__h2').tag = 'h2'
        if view.stages:
            with ui.element('div').classes('timeline-container'):
                for stage in view.stages:
                    self._stage(stage)
        else:
            ui.label('Nothing is on a stream right now. Check back later.').classes('empty-state')

    def _stage(self, stage: WatchStage) -> None:
        with ui.card().classes('card-full-width'):
            with ui.row().classes('stage-header no-wrap'):
                with ui.column().classes('stage-title'):
                    ui.label(stage.name).classes('stage-name')
                    _channel_label(stage)
                if stage.channel and self.theater.available:
                    self._buttons[stage.id] = ui.button(
                        'Watch', icon='play_arrow',
                        on_click=lambda s=stage: self.theater.watch(_now_playing(s)),
                    ).props('outline no-caps').classes('stage-watch')
                elif stage.stream_url:
                    with ui.link(target=stage.stream_url, new_tab=True).classes('stage-link'):
                        ui.label('Open stream')
                        ui.icon('open_in_new', size='16px')
            with ui.column().classes('column-spacing'):
                if stage.on_now:
                    _match_card(stage.on_now, live=True, board_scope=self.board_scope)
                for match in stage.up_next:
                    _match_card(match, live=False, board_scope=self.board_scope)
                if not stage.on_now and not stage.up_next:
                    ui.label('Nothing else on this stage today.').classes('text-muted')

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
                            with ui.element('span').classes('wiz-watch-tourn__title'):
                                ui.label(t.name).classes('wiz-watch-row__players')
                                if t.prize_total is not None:
                                    with ui.element('span').classes('wiz-chip wiz-chip--ok'):
                                        ui.icon('payments').classes('text-sm')
                                        ui.label(f'{format_money(t.prize_total)} prize pool')
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
                if any(t.prize_synced for t in view.tournaments):
                    with ui.element('p').classes('wiz-watch-tourn__foot'):
                        ui.label("Prize pools include SpeedGaming's bonus and update from")
                        ui.link('sglive.speedgaming.org', SGLIVE_URL, new_tab=True)
                        ui.label('every 15 minutes.')

    def _paint_buttons(self) -> None:
        for stage_id, button in self._buttons.items():
            on = self.theater.is_watching(stage_id)
            button.text = 'Watching' if on else 'Watch'
            button.props(f'aria-pressed={"true" if on else "false"}')
            if on:
                button.props(remove='outline', add='unelevated')
            else:
                button.props(remove='unelevated', add='outline')
