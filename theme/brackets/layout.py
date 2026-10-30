"""Pure bracket layout walker — turns a persisted match graph into geometry.

No ORM, no NiceGUI: this module is the coordinate math behind the redesigned
bracket view (docs/features/brackets.md). It takes the light
:class:`MatchNode` projection of ``BracketMatch`` rows for **one section**
(winners+finals, or losers) and returns absolute pixel placements plus the elbow
connectors that join each match to the match its winner feeds — the only links a
conventional bracket draws.

The algorithm is Toornament's (a rooted tree on winner-links): a depth-first pass
assigns each leaf a sequential vertical *slot* and centers every parent on the
mean of its children's slots; slots and the round-derived column map to pixels.
Absolute positioning (rather than CSS grid) keeps connectors exact and the layout
correct for the irregular losers bracket, where a "minor" round match has a single
in-section feeder and a "major" round match has two.

Byes, placeholder source hints ("Winner of 7") and match numbering are computed
here too (all pure) so the card layer stays presentational. Round *names* used to
be, and moved to :mod:`application.services.bracket_engines.round_names` so a
service or a Discord DM can say "Semifinals" too; :func:`round_label` is
re-exported here so the card layer's imports are unchanged. Pixel constants
mirror ``static/css/brackets.css``; changing one means changing both.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from application.services.bracket_engines.round_names import round_label

__all__ = [
    'CARD_HEIGHT',
    'COL_STRIDE',
    'COL_WIDTH',
    'GUTTER',
    'ROW_PITCH',
    'MatchNode',
    'Placement',
    'SectionLayout',
    'Segment',
    'SlotSource',
    'assign_match_numbers',
    'avatar_hue',
    'avatar_img_html',
    'avatar_initial',
    'layout_section',
    'round_label',
    'slot_sources',
]

# Geometry (px) — keep in sync with static/css/brackets.css (--bracket-* vars).
COL_WIDTH = 200
GUTTER = 52              # horizontal space between round columns (holds connectors)
CARD_HEIGHT = 58         # match-card height (two ~29px entrant rows)
ROW_PITCH = 82           # vertical distance between adjacent leaf slots
COL_STRIDE = COL_WIDTH + GUTTER


@dataclass(frozen=True)
class MatchNode:
    """The projection of a ``BracketMatch`` the layout walker needs.

    ``winner_to_id`` is the match this one's winner feeds (``None`` for a section
    root). Loser links are deliberately absent — a bracket draws winner links
    only.
    """

    id: int
    round: int
    position: int
    winner_to_id: Optional[int] = None


@dataclass(frozen=True)
class Segment:
    """One straight connector segment, absolutely positioned within the canvas."""

    x: float
    y: float
    length: float
    orientation: str  # 'h' | 'v'


@dataclass(frozen=True)
class Placement:
    """Where one match card sits in its section."""

    match_id: int
    col: int          # 0-based column index (left → right)
    round: int
    slot: float       # vertical slot: leaf order for leaves, centered for parents
    left: float       # px
    top: float        # px
    center_y: float   # px (vertical center of the card)


@dataclass
class SectionLayout:
    """The full geometry of one bracket section."""

    columns: List[int]                       # round numbers, left → right
    placements: Dict[int, Placement]
    connectors: List[Segment] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0


def _column_order(rounds: List[int]) -> List[int]:
    """Order a section's distinct rounds into left→right columns.

    Winners/finals rounds are positive and ascend; losers rounds are negative and
    are ordered by increasing magnitude (``-1`` is the leftmost losers column).
    A section never mixes signs.
    """
    distinct = set(rounds)
    positive = sorted(r for r in distinct if r >= 0)
    negative = sorted((r for r in distinct if r < 0), key=abs)
    return positive if positive else negative


def layout_section(nodes: List[MatchNode]) -> SectionLayout:
    """Compute placements + connectors for one section's matches.

    Pure and deterministic. Leaves (matches with no in-section winner-feeder) are
    assigned consecutive slots in depth-first, position order; every parent is
    centered on the mean of its children's slots. Column = the round's index in
    :func:`_column_order`.
    """
    if not nodes:
        return SectionLayout(columns=[], placements={})

    by_id = {n.id: n for n in nodes}
    section_ids = set(by_id)
    columns = _column_order([n.round for n in nodes])
    col_of_round = {r: i for i, r in enumerate(columns)}

    # Children within the section, keyed by the parent's id (winner-link only).
    children: Dict[int, List[MatchNode]] = {n.id: [] for n in nodes}
    roots: List[MatchNode] = []
    for n in nodes:
        parent_id = n.winner_to_id
        if parent_id is not None and parent_id in section_ids:
            children[parent_id].append(n)
        else:
            roots.append(n)

    for kids in children.values():
        kids.sort(key=lambda m: (m.round, m.position))
    roots.sort(key=lambda m: (col_of_round.get(m.round, 0), m.position))

    slot: Dict[int, float] = {}
    counter = {'next': 0.0}
    visiting: set = set()

    def assign(node_id: int) -> float:
        if node_id in slot:
            return slot[node_id]
        if node_id in visiting:  # cycle guard (winner-links are a DAG; belt & braces)
            slot[node_id] = counter['next']
            counter['next'] += 1.0
            return slot[node_id]
        visiting.add(node_id)
        kids = children.get(node_id, [])
        if not kids:
            s = counter['next']
            counter['next'] += 1.0
        else:
            child_slots = [assign(k.id) for k in kids]
            s = sum(child_slots) / len(child_slots)
        visiting.discard(node_id)
        slot[node_id] = s
        return s

    for root in roots:
        assign(root.id)

    placements: Dict[int, Placement] = {}
    for n in nodes:
        col = col_of_round.get(n.round, 0)
        s = slot[n.id]
        left = col * COL_STRIDE
        top = s * ROW_PITCH
        placements[n.id] = Placement(
            match_id=n.id,
            col=col,
            round=n.round,
            slot=s,
            left=left,
            top=top,
            center_y=top + CARD_HEIGHT / 2,
        )

    connectors = _build_connectors(children, placements)

    max_slot = max(slot.values()) if slot else 0.0
    width = len(columns) * COL_STRIDE - GUTTER
    height = max_slot * ROW_PITCH + CARD_HEIGHT
    return SectionLayout(
        columns=columns,
        placements=placements,
        connectors=connectors,
        width=width,
        height=height,
    )


def _build_connectors(
    children: Dict[int, List[MatchNode]],
    placements: Dict[int, Placement],
) -> List[Segment]:
    """Elbow segments joining each parent to its in-section children.

    For a parent with children C: each child gets a horizontal stub to a shared
    vertical spine in the gutter left of the parent; the spine runs between the
    outermost child centers; a final horizontal stub joins the spine to the
    parent. The classic bracket "⊐" — correct for one child (a straight elbow) or
    many.
    """
    segments: List[Segment] = []
    for parent_id, kids in children.items():
        if not kids:
            continue
        parent = placements[parent_id]
        spine_x = parent.left - GUTTER / 2
        child_centers = [placements[k.id].center_y for k in kids]
        for k in kids:
            cp = placements[k.id]
            child_right = cp.left + COL_WIDTH
            segments.append(
                Segment(x=child_right, y=cp.center_y, length=spine_x - child_right, orientation='h')
            )
        top_y = min([*child_centers, parent.center_y])
        bot_y = max([*child_centers, parent.center_y])
        segments.append(Segment(x=spine_x, y=top_y, length=bot_y - top_y, orientation='v'))
        segments.append(
            Segment(x=spine_x, y=parent.center_y, length=parent.left - spine_x, orientation='h')
        )
    return segments


def assign_match_numbers(matches: List[MatchNode]) -> Dict[int, int]:
    """Stable 1-based display number per match (staff call "match 12 on stream").

    Ordered winners bracket first (ascending round/position), then the losers
    bracket (by increasing round magnitude), so numbering reads the way the
    bracket is played.
    """
    winners = sorted((m for m in matches if m.round >= 0), key=lambda m: (m.round, m.position))
    losers = sorted((m for m in matches if m.round < 0), key=lambda m: (abs(m.round), m.position))
    return {m.id: i for i, m in enumerate([*winners, *losers], start=1)}


@dataclass(frozen=True)
class SlotSource:
    """Where an empty match slot's entrant will come from."""

    kind: str          # 'winner' | 'loser'
    source_match_id: int


def slot_sources(
    winner_links: List[Tuple[int, Optional[int], Optional[int]]],
    loser_links: List[Tuple[int, Optional[int], Optional[int]]],
) -> Dict[Tuple[int, int], SlotSource]:
    """Invert the progression pointers into per-slot source hints.

    ``winner_links`` / ``loser_links`` are ``(from_match_id, to_match_id,
    to_slot)`` triples (the ``winner_to`` / ``loser_to`` pointers). The result
    maps ``(target_match_id, slot)`` → the feeder, so an empty slot can render
    "Winner of 7" / "Loser of 12" instead of a bare "TBD".
    """
    out: Dict[Tuple[int, int], SlotSource] = {}
    for from_id, to_id, to_slot in winner_links:
        if to_id is not None and to_slot in (1, 2):
            out[(to_id, to_slot)] = SlotSource(kind='winner', source_match_id=from_id)
    for from_id, to_id, to_slot in loser_links:
        if to_id is not None and to_slot in (1, 2):
            out[(to_id, to_slot)] = SlotSource(kind='loser', source_match_id=from_id)
    return out


def avatar_hue(name: str) -> int:
    """Deterministic hue (0–359) for an entrant's initial-letter avatar disc."""
    return sum(ord(c) for c in name) % 360 if name else 0


def avatar_initial(name: str) -> str:
    """The single letter shown on an entrant's avatar disc."""
    for ch in name:
        if ch.isalnum():
            return ch.upper()
    return '?'


def avatar_img_html(url: str) -> str:
    """The Discord-avatar ``<img>`` that overlays an entrant's initial disc.

    The initial stays in the DOM underneath. A hash we cached before the person
    changed their avatar 404s, and ``onerror`` drops the image so the disc shows
    the letter again rather than a broken-image glyph — that fallback is the
    whole reason the letter is still rendered.

    Returns ``''`` for a URL that isn't a plain https CDN link: this is the one
    piece of bracket markup built by hand for both the NiceGUI and static views,
    so it refuses anything that could carry script instead of escaping it.
    """
    if not url.startswith('https://') or '"' in url or '<' in url:
        return ''
    return (
        f'<img class="bracket-avatar-img" src="{html.escape(url, quote=True)}" alt="" '
        'loading="lazy" decoding="async" onerror="this.remove()">'
    )


