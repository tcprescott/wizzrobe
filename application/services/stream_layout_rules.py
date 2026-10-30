"""What a stream layout's boxes may hold, and how a stored box is checked.

Pure: no database, no tenant. :class:`StreamLayoutService` validates every
write through :func:`clean_boxes`, the editor lists :data:`FIELDS` for its
"Add box" picker, and the overlay renderer reads boxes that already passed
here, so nothing a staff member typed reaches the page unescaped or unbounded.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any, Optional

MAX_BOXES = 60
MAX_TEXT = 200
MAX_LABEL = 40
MAX_PLAYERS = 4
MIN_SIDE = 4
MAX_CANVAS = 3840

KIND_FIELD = 'field'
KIND_HOLE = 'hole'
KINDS = (KIND_FIELD, KIND_HOLE)

ALIGNS = ('left', 'center', 'right')
WEIGHTS = (400, 600, 800)
FONTS: dict[str, str] = {
    'Inter': "Inter, 'Segoe UI', system-ui, sans-serif",
    'Arial': 'Arial, Helvetica, sans-serif',
    'Impact': "Impact, 'Arial Black', sans-serif",
    'Georgia': "Georgia, 'Times New Roman', serif",
    'Courier': "'Courier New', Courier, monospace",
}
DEFAULT_FONT = 'Inter'
_COLOUR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
_ID_RE = re.compile(r'^[a-z0-9]{1,16}$')


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    icon: str
    group: str
    sample: str
    per_player: bool = False


FIELDS: tuple[Field, ...] = (
    Field('player_name', 'Player name', 'person', 'Players', 'Wavedash', per_player=True),
    Field('player_pronouns', 'Pronouns', 'badge', 'Players', 'she/her', per_player=True),
    Field('players_all', 'All players', 'group', 'Players', 'Wavedash vs Moonlit'),
    Field('tournament', 'Tournament', 'emoji_events', 'Match', 'ALTTPR Open'),
    Field('tournament_short', 'Tournament short name', 'short_text', 'Match', 'ALTTPR'),
    Field('round', 'Round', 'stairs', 'Match', 'Top 8 · Game 1 of 3'),
    Field('tournament_round', 'Tournament · round', 'emoji_events', 'Match', 'ALTTPR Open · Top 8'),
    Field('match_title', 'Match title', 'title', 'Match', 'Grand final'),
    Field('scheduled_time', 'Scheduled time', 'schedule', 'Match', '3:00 PM'),
    Field('seed_code', 'Seed code', 'casino', 'Match', 'Bow · Hookshot · Lamp · Bottle'),
    Field('prize_pool', 'Prize pool', 'payments', 'Match', '$1,200'),
    Field('stage_name', 'Stage name', 'tv', 'Stage', 'Main Stage'),
    Field('channel', 'Channel', 'live_tv', 'Stage', 'twitch.tv/speedgaming'),
    Field('commentators', 'Commentators', 'mic', 'Stage', 'Rook, Pell'),
    Field('trackers', 'Trackers', 'track_changes', 'Stage', 'Juno'),
    Field('up_next', 'Up next', 'skip_next', 'Stage', '3:30 PM · Kestrel vs Juno'),
    Field('event_name', 'Event name', 'celebration', 'Other', 'SGL 2027'),
    Field('community_name', 'Community name', 'groups', 'Other', 'SpeedGaming Live'),
    Field('custom_text', 'Custom text', 'text_fields', 'Other', 'Thanks for watching!'),
)
FIELDS_BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}


def new_box_id() -> str:
    return secrets.token_hex(4)


def box_label(box: dict) -> str:
    """What the box list calls a box: "Player 1 · name", "Video hole"."""
    if box.get('kind') == KIND_HOLE:
        return f"Video hole · {box['label']}" if box.get('label') else 'Video hole'
    field = FIELDS_BY_KEY.get(box.get('field', ''))
    if field is None:
        return 'Box'
    if field.per_player:
        return f"Player {box.get('which', 1)} · {field.label.replace('Player ', '').lower()}"
    return field.label


def default_box(kind: str, field: Optional[str], width: int, height: int) -> dict:
    """A new box, centred on the canvas and sized for what it holds."""
    if kind == KIND_HOLE:
        w, h = width // 3, height // 3
    else:
        w, h = max(MIN_SIDE, width // 4), max(MIN_SIDE, height // 20)
    box: dict[str, Any] = {
        'id': new_box_id(), 'kind': kind,
        'x': (width - w) // 2, 'y': (height - h) // 2, 'w': w, 'h': h,
    }
    if kind == KIND_HOLE:
        box['radius'] = 0
        box['label'] = ''
        return box
    box.update({
        'field': field or 'custom_text', 'which': 1, 'font': DEFAULT_FONT,
        'size': max(12, h * 2 // 3), 'weight': 800, 'color': '#FFFFFF',
        'align': 'center', 'upper': False, 'shadow': True, 'text': '',
    })
    return box


def _int(value: Any, lo: int, hi: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a number.')
    return max(lo, min(hi, round(value)))


def clean_box(raw: Any, width: int, height: int) -> dict:
    """One box, checked and clamped onto the canvas. Raises ``ValueError``."""
    if not isinstance(raw, dict):
        raise ValueError('A box is malformed.')
    kind = raw.get('kind')
    if kind not in KINDS:
        raise ValueError('A box has an unknown kind.')
    box_id = raw.get('id') if isinstance(raw.get('id'), str) and _ID_RE.match(raw['id']) else new_box_id()
    w = _int(raw.get('w'), MIN_SIDE, width, 'Width')
    h = _int(raw.get('h'), MIN_SIDE, height, 'Height')
    x = _int(raw.get('x'), 0, width - w, 'X')
    y = _int(raw.get('y'), 0, height - h, 'Y')
    box: dict[str, Any] = {'id': box_id, 'kind': kind, 'x': x, 'y': y, 'w': w, 'h': h}
    if kind == KIND_HOLE:
        box['radius'] = _int(raw.get('radius', 0), 0, min(w, h) // 2, 'Corner radius')
        label = raw.get('label') or ''
        if not isinstance(label, str):
            raise ValueError('A video hole name must be text.')
        box['label'] = ' '.join(label.split())[:MAX_LABEL]
        return box

    field = raw.get('field')
    if field not in FIELDS_BY_KEY:
        raise ValueError('A box shows a field Wizzrobe does not know.')
    colour = raw.get('color', '#FFFFFF')
    if not isinstance(colour, str) or not _COLOUR_RE.match(colour):
        raise ValueError('Colours are six-digit hex, like #FFFFFF.')
    text = raw.get('text') or ''
    if not isinstance(text, str):
        raise ValueError('Custom text must be text.')
    if len(text) > MAX_TEXT:
        raise ValueError(f'Custom text can be up to {MAX_TEXT} characters.')
    font = raw.get('font', DEFAULT_FONT)
    weight = raw.get('weight', 800)
    align = raw.get('align', 'center')
    box.update({
        'field': field,
        'which': _int(raw.get('which', 1), 1, MAX_PLAYERS, 'Player'),
        'font': font if font in FONTS else DEFAULT_FONT,
        'size': _int(raw.get('size', 24), 6, 400, 'Size'),
        'weight': weight if weight in WEIGHTS else 800,
        'color': colour.upper(),
        'align': align if align in ALIGNS else 'center',
        'upper': bool(raw.get('upper', False)),
        'shadow': bool(raw.get('shadow', True)),
        'text': text.strip(),
    })
    return box


def clean_boxes(raw: Any, width: int, height: int) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError('The box list is malformed.')
    if len(raw) > MAX_BOXES:
        raise ValueError(f'A layout can have up to {MAX_BOXES} boxes.')
    boxes = [clean_box(b, width, height) for b in raw]
    seen: set[str] = set()
    for box in boxes:
        while box['id'] in seen:
            box['id'] = new_box_id()
        seen.add(box['id'])
    return boxes


def sample_values(boxes: list[dict]) -> dict[str, str]:
    """What the editor shows in each box before a preview match is picked."""
    out: dict[str, str] = {}
    for box in boxes:
        if box.get('kind') != KIND_FIELD:
            continue
        field = FIELDS_BY_KEY[box['field']]
        if field.key == 'custom_text':
            out[box['id']] = box.get('text') or field.sample
        elif field.per_player and box.get('which', 1) > 1:
            out[box['id']] = {'player_name': ['Wavedash', 'Moonlit', 'Kestrel', 'Juno'],
                              'player_pronouns': ['she/her', 'he/him', 'they/them', 'she/they']}[
                field.key][box['which'] - 1]
        else:
            out[box['id']] = field.sample
    return out
