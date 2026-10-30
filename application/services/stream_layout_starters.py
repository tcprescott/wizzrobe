"""Built-in starting points for a stream layout: a plain background with the
video holes and data boxes already placed, one per common kind of stream.

The SpeedGaming and FFR starters copy the box positions (not the art) of the
layouts SpeedGaming's and FFR's designers made for SGL; they share one cam and
comms placement, so a scene set up for one lines up with the others.

Pure data, all at 1920×1080. :meth:`StreamLayoutService.create_layout` turns a
starter into a layout (boxes through ``clean_boxes``, background through
:func:`plain_background`), so a starter is only ever a first draft the editor
changes like any other layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from PIL import Image

from application.services.stream_layout_rules import KIND_HOLE

W, H = 1920, 1080
_COLOUR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

# Hole names are OBS feed names: renaming one breaks the wiring layouts share.
P1_GAME, P2_GAME = 'Player 1 game', 'Player 2 game'
P1_CAM, P2_CAM, COMMS_CAM = 'Player 1 cam', 'Player 2 cam', 'Comms cam'

BACKGROUNDS: tuple[tuple[str, str], ...] = (
    ('Charcoal', '#16181D'),
    ('Navy', '#14213D'),
    ('Black', '#000000'),
)


@dataclass(frozen=True)
class Starter:
    key: str
    label: str
    blurb: str
    boxes: tuple[dict, ...]
    group: str


def _hole(x: int, y: int, w: int, h: int, label: str = '') -> dict:
    return {'kind': KIND_HOLE, 'x': x, 'y': y, 'w': w, 'h': h, 'radius': 0, 'label': label}


def _text(field: str, x: int, y: int, w: int, h: int, size: int, **extra) -> dict:
    return {'kind': 'field', 'field': field, 'x': x, 'y': y, 'w': w, 'h': h, 'size': size,
            'font': 'Inter', 'weight': 800, 'color': '#FFFFFF', 'align': 'center',
            'upper': False, 'shadow': True, **extra}


def _sg_top() -> list[dict]:
    """SpeedGaming's 2-player header: pronouns, name, tournament · round, name, pronouns."""
    return [
        _text('player_pronouns', 26, 34, 242, 54, 26, which=1, weight=600),
        _text('player_name', 282, 34, 462, 54, 36, which=1, upper=True),
        _text('tournament_round', 752, 34, 420, 54, 26, color='#F6B04A'),
        _text('player_name', 1182, 34, 462, 54, 36, which=2, upper=True),
        _text('player_pronouns', 1656, 34, 242, 54, 26, which=2, weight=600),
    ]


def _sg_tracker_row(tracker_w: int, tracker_h: int = 218) -> list[dict]:
    """Games over a bottom row of trackers, player cams and the comms cam (standard cam placement)."""
    return [
        *_sg_top(),
        _hole(26, 96, 846, 679, P1_GAME), _hole(1052, 96, 846, 679, P2_GAME),
        _hole(26, 784, tracker_w, tracker_h, 'Player 1 tracker'),
        _hole(1898 - tracker_w, 784, tracker_w, tracker_h, 'Player 2 tracker'),
        _hole(516, 784, 216, 218, P1_CAM), _hole(738, 784, 448, 218, COMMS_CAM), _hole(1192, 784, 216, 218, P2_CAM),
        _text('commentators', 738, 1008, 448, 54, 26, weight=600),
    ]


def _ffr_game_grid(players: int) -> list[dict]:
    games = [(318, 34, 576, 466), (1008, 34, 576, 466), (318, 528, 576, 468), (1008, 528, 576, 468)]
    names = [(10, 34), (1598, 34), (10, 942), (1598, 942)]
    boxes = []
    for i in range(players):
        boxes.append(_hole(*games[i], f'Player {i + 1} game'))
        boxes.append(_text('player_name', *names[i], 294, 52, 28, which=i + 1, upper=True))
    return [
        *boxes,
        _text('commentators', 318, 1008, 576, 54, 26, weight=600),
        _text('tournament_round', 1156, 1008, 294, 54, 24, weight=600),
    ]


def _ffr_coop() -> list[dict]:
    games = [(318, 38, 576, 466), (1008, 38, 576, 466), (318, 532, 576, 466), (1008, 532, 576, 466)]
    labels = [(10, 38), (1598, 38), (10, 880), (1598, 880)]
    boxes = []
    for i in range(4):
        x, y = labels[i]
        boxes += [
            _hole(*games[i], f'Player {i + 1} game'),
            _text('player_name', x, y, 294, 52, 28, which=i + 1, upper=True),
            _text('player_pronouns', x, y + 64, 294, 52, 24, which=i + 1, weight=600),
        ]
    return [*boxes, _text('commentators', 594, 1016, 714, 50, 26, weight=600)]


STARTERS: tuple[Starter, ...] = (
    Starter('sg_2p_trackers', 'SG 2P 4:3, trackers + cams', 'Standard trackers, player and comms cams',
            tuple(_sg_tracker_row(482)), 'SpeedGaming'),
    Starter('sg_2p_small_trackers', 'SG 2P 4:3, small trackers + cams', 'Small trackers, player and comms cams',
            tuple(_sg_tracker_row(266, 212)), 'SpeedGaming'),
    Starter('sg_2p_dk_trackers', 'SG 2P 4:3, DK trackers + cams', 'DK-size trackers, player and comms cams',
            tuple(_sg_tracker_row(278)), 'SpeedGaming'),
    Starter('sg_2p_cams', 'SG 2P 4:3 + cams', 'No trackers; player and comms cams', (
        *_sg_top(),
        _hole(24, 96, 886, 704, P1_GAME), _hole(1012, 96, 886, 704, P2_GAME),
        _hole(516, 814, 214, 214, P1_CAM), _hole(738, 814, 448, 214, COMMS_CAM), _hole(1192, 814, 216, 214, P2_CAM),
        _text('commentators', 738, 1030, 448, 46, 24, weight=600),
    ), 'SpeedGaming'),
    Starter('sg_z1r', 'SG Zelda 1 Randomizer', 'Centre column, trackers both sides, cams below', (
        _text('player_pronouns', 18, 30, 244, 54, 26, which=1, weight=600),
        _text('player_name', 274, 30, 464, 54, 36, which=1, upper=True),
        _text('player_name', 1186, 30, 462, 54, 36, which=2, upper=True),
        _text('player_pronouns', 1660, 30, 242, 54, 26, which=2, weight=600),
        _hole(18, 94, 848, 678, P1_GAME), _hole(1056, 94, 846, 678, P2_GAME), _hole(876, 8, 170, 820, 'Centre column'),
        _hole(18, 780, 220, 218, 'Player 1 tracker'), _hole(246, 780, 466, 64, 'Player 1 strip'),
        _hole(1210, 780, 466, 64, 'Player 2 strip'), _hole(1684, 780, 218, 218, 'Player 2 tracker'),
        _hole(514, 856, 216, 218, P1_CAM), _hole(738, 856, 448, 218, COMMS_CAM), _hole(1192, 856, 216, 218, P2_CAM),
        _text('commentators', 66, 1022, 420, 52, 24, weight=600),
    ), 'SpeedGaming'),
    Starter('ffr_1p', 'FFR 1P', 'One big game, name top right', (
        _hole(16, 34, 1146, 964, P1_GAME),
        _text('player_name', 1172, 34, 736, 52, 32, upper=True),
        _text('commentators', 1262, 826, 574, 52, 26, weight=600),
        _text('tournament_round', 1402, 890, 294, 50, 24, weight=600),
    ), 'FFR'),
    Starter('ffr_2p', 'FFR 2P', 'Two games, names above', (
        _text('player_name', 26, 36, 632, 52, 32, which=1, upper=True),
        _text('player_name', 1266, 36, 632, 52, 32, which=2, upper=True),
        _hole(26, 98, 886, 702, P1_GAME), _hole(1012, 98, 886, 702, P2_GAME),
        _text('commentators', 320, 826, 574, 50, 26, weight=600),
        _text('tournament_round', 1156, 826, 294, 50, 24, weight=600),
    ), 'FFR'),
    Starter('ffr_3p', 'FFR 3P', 'Three games in a grid', tuple(_ffr_game_grid(3)), 'FFR'),
    Starter('ffr_4p', 'FFR 4P', 'Four games in a grid', tuple(_ffr_game_grid(4)), 'FFR'),
    Starter('ffr_4p_coop', 'FFR 4P co-op', 'Four games, two lines per player', tuple(_ffr_coop()), 'FFR'),
    Starter('blank', 'Blank', 'Background only', (), 'Basic'),
    Starter('commentary', 'Commentary / interview', 'Big cam, names below', (
        _hole(240, 40, 1440, 810, COMMS_CAM),
        _text('commentators', 240, 880, 1440, 80, 48),
        _text('tournament_round', 240, 970, 1440, 60, 32, weight=600),
    ), 'Basic'),
    Starter('break', 'Starting soon / break', 'No video, just info', (
        _text('event_name', 260, 300, 1400, 130, 88, upper=True),
        _text('custom_text', 260, 450, 1400, 80, 48, weight=600, text='Starting soon'),
        _text('up_next', 260, 590, 1400, 64, 36, weight=600),
        _text('channel', 260, 680, 1400, 54, 30, weight=400),
    ), 'Basic'),
)
GROUPS: tuple[str, ...] = ('SpeedGaming', 'FFR', 'Basic')
DEFAULT_STARTER = 'sg_2p_trackers'
STARTERS_BY_KEY: dict[str, Starter] = {s.key: s for s in STARTERS}


def clean_background(colour: Optional[str]) -> str:
    if not isinstance(colour, str) or not _COLOUR_RE.match(colour.strip()):
        raise ValueError('Pick a background colour, or type one as six-digit hex like #16181D.')
    return colour.strip().upper()


def plain_background(colour: str, width: int = W, height: int = H, keep_alpha_of: Optional[bytes] = None) -> bytes:
    """A solid PNG of ``colour``, transparent wherever ``keep_alpha_of`` (old art) was."""
    rgb = tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
    img = Image.new('RGBA', (width, height), (*rgb, 255))
    if keep_alpha_of:
        with Image.open(BytesIO(keep_alpha_of)) as old:
            if old.size == (width, height) and old.mode == 'RGBA':
                img.putalpha(old.getchannel('A'))
    out = BytesIO()
    img.save(out, 'PNG', optimize=True)
    return out.getvalue()
