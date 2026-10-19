"""The Watch page theater: one Twitch player above the stage columns.

No player loads until a viewer taps a stage's tile, so the page sets no Twitch
cookies for people who only read the schedule (the tiles' stills are plain
images from Twitch's CDN). One player
at a time: watching another stage swaps the channel rather than stacking a
second stream.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from nicegui import context, ui

from application.utils.hostname import effective_request_host
from application.utils.twitch_embed import parent_hostname, player_url


@dataclass
class NowPlaying:
    """What the theater's side panel says about the watched stage."""

    stage_id: int
    stage_name: str
    channel: str
    stream_url: str
    time: str = ''
    players: str = ''
    tournament: str = ''
    channel_name: str = ''


class TwitchTheater:
    def __init__(self, on_change: Optional[Callable[[], None]] = None) -> None:
        self.parent = parent_hostname(effective_request_host(context.client.request.headers))
        self.watching: Optional[NowPlaying] = None
        self._on_change = on_change
        self._channel: Optional[str] = None
        with ui.element('section').classes('twitch-theater').props('aria-label="Live stream"') as self.root:
            self.frame = ui.element('div').classes('twitch-theater__player')
            with ui.element('aside').classes('twitch-theater__panel'):
                self.eyebrow = ui.label().classes('twitch-theater__eyebrow')
                with ui.row().classes('twitch-theater__now items-center no-wrap'):
                    self.time = ui.label().classes('match-time')
                    self.chip = ui.label('On now').classes('match-badge wiz-chip wiz-chip--live')
                self.players = ui.label().classes('twitch-theater__players')
                self.tournament = ui.label().classes('twitch-theater__tournament')
                with ui.row().classes('twitch-theater__actions items-center'):
                    self.link = ui.link('Open on Twitch', '', new_tab=True).classes('text-link')
                    ui.button('Close player', on_click=self.close).props('outline no-caps').classes(
                        'twitch-theater__close')
        self.root.set_visibility(False)

    @property
    def available(self) -> bool:
        return bool(self.parent)

    def is_watching(self, stage_id: int) -> bool:
        return self.watching is not None and self.watching.stage_id == stage_id

    def watch(self, now: NowPlaying) -> None:
        if not self.parent:
            return
        self.watching = now
        if now.channel != self._channel:
            self._channel = now.channel
            self.frame.clear()
            with self.frame:
                frame = ui.element('iframe')
                frame._props.update({
                    'src': player_url(now.channel, self.parent),
                    'title': f'{now.stage_name} live stream',
                    'allow': 'autoplay; fullscreen; picture-in-picture',
                    'allowfullscreen': True,
                })
        self._paint(now)
        self.root.set_visibility(True)
        if self._on_change:
            self._on_change()

    def refresh(self, now: NowPlaying) -> None:
        """Repaint the panel after the schedule reloads, without restarting the video."""
        if not self.is_watching(now.stage_id):
            return
        self.watching = now
        self._paint(now)

    def close(self) -> None:
        self.watching = None
        self._channel = None
        self.frame.clear()
        self.root.set_visibility(False)
        if self._on_change:
            self._on_change()

    def _paint(self, now: NowPlaying) -> None:
        self.eyebrow.text = f'Watching {now.stage_name}' + (
            f' · twitch.tv/{now.channel_name}' if now.channel_name else '')
        self.time.text = now.time
        self.time.set_visibility(bool(now.time))
        self.chip.set_visibility(bool(now.players))
        self.players.text = now.players or 'Nothing on right now'
        self.tournament.text = now.tournament
        self.tournament.set_visibility(bool(now.tournament))
        self.link._props['href'] = now.stream_url
        self.link.update()
