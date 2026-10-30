"""Escaping + match-card/section markup for the static bracket view.

The websocket-free twin of :mod:`theme.brackets.cards`: it emits the same DOM
shape and class names, so ``static/css/brackets.css`` styles both views and a
visual change lands in one place. Geometry, match numbering and placeholder
hints all come from the same pure helpers the interactive renderer uses
(:func:`theme.brackets.layout.layout_section`, :func:`build_context`), so the
two cannot drift.

Pure and I/O-free. Every piece of user-controlled text goes through
:func:`html.escape` — this is the one bracket surface that builds markup by
hand, so escaping is the module's standing obligation, not a per-call decision.
"""

from __future__ import annotations

import html
from typing import Dict, List, Optional

from application.services.match.match_status import MatchStatus
from application.services.match.match_status import label as status_label
from application.services.match.match_status import tone as status_tone
from application.utils.safe_url import http_url_or_empty
from models import BracketMatch, BracketMatchState

from ..cards import (
    LIVE_STATUS_CLASS,
    BracketContext,
    card_state_class,
    placeholder_text,
)
from ..layout import (
    CARD_HEIGHT,
    COL_WIDTH,
    Placement,
    SectionLayout,
    avatar_hue,
    avatar_img_html,
    avatar_initial,
    layout_section,
    round_label,
)
from ..render import detect_finals, match_nodes


def _esc(value) -> str:
    return html.escape('' if value is None else str(value), quote=True)


def _tag(tag: str, classes: str, inner: str = '', *, style: str = '', attrs: str = '') -> str:
    style_attr = f' style="{style}"' if style else ''
    extra = f' {attrs}' if attrs else ''
    return f'<{tag} class="{classes}"{style_attr}{extra}>{inner}</{tag}>'


def _avatar_html(name: str, url: Optional[str] = None) -> str:
    """The entrant disc: the initial, with a Discord avatar layered over it.

    ``avatar_img_html`` accepts only plain https CDN URLs, so the one attribute
    here that isn't run through :func:`_esc` cannot carry markup.
    """
    return _tag(
        'div', 'bracket-avatar',
        _esc(avatar_initial(name)) + (avatar_img_html(url) if url else ''),
        style=f'--bracket-avatar-hue: {avatar_hue(name)}',
    )


def _score_text(match: BracketMatch, slot: int, *, is_winner: bool, is_loser: bool) -> str:
    completed = match.state == BracketMatchState.COMPLETE
    score = match.entry1_score if slot == 1 else match.entry2_score
    if completed and match.forfeit and is_loser:
        return 'FF'
    if score is not None:
        return str(score)
    if is_winner:
        return 'W'
    if is_loser:
        return 'L'
    return ''


def _slot_html(match: BracketMatch, slot: int, ctx: BracketContext) -> str:
    completed = match.state == BracketMatchState.COMPLETE
    entry_id = match.entry1_id if slot == 1 else match.entry2_id
    decided = completed and match.winner_id is not None
    is_winner = decided and entry_id == match.winner_id
    is_loser = decided and entry_id != match.winner_id

    classes = 'bracket-slot'
    if entry_id is None:
        classes += ' is-empty'
    if is_winner:
        classes += ' is-winner'
    if is_loser:
        classes += ' is-loser'

    seed = ctx.entry_seed.get(entry_id) if entry_id is not None else None
    # Always emitted so the four columns stay aligned.
    parts = [_tag('div', 'bracket-seed', _esc(seed) if seed is not None else '')]

    if entry_id is not None:
        name = ctx.entry_name.get(entry_id, 'Unknown')
        parts.append(_avatar_html(name, ctx.entry_avatar.get(entry_id)))
        parts.append(_tag('div', 'bracket-name', _esc(name), attrs=f'title="{_esc(name)}"'))
    else:
        parts.append(_tag('div', 'bracket-avatar is-empty'))
        parts.append(_tag(
            'div', 'bracket-name is-placeholder',
            _esc(placeholder_text(match, slot, ctx, completed=completed)),
        ))

    parts.append(_tag(
        'div', 'bracket-score',
        _esc(_score_text(match, slot, is_winner=is_winner, is_loser=is_loser)),
    ))

    attrs = f'data-entrant-id="{_esc(entry_id)}"' if entry_id is not None else ''
    return _tag('div', classes, ''.join(parts), attrs=attrs)


def _status_pill_html(match: BracketMatch, ctx: BracketContext) -> str:
    """The corner badge — a linked LIVE for a game in progress, else a chip.

    The watch link stays anonymous-visible for the same reason it does on the
    interactive card (D4): the stream and race room are already public on the
    schedule, so the static bracket becomes the link you send a viewer.
    """
    live = ctx.live_state.get(match.id) or {}
    status = live.get('status')
    if status is None or status not in LIVE_STATUS_CLASS:
        return ''
    if status == MatchStatus.LIVE:
        url = http_url_or_empty(live.get('watch_url'))
        if url:
            return (
                f'<a class="bracket-live-pill" href="{_esc(url)}" '
                'target="_blank" rel="noopener noreferrer">LIVE</a>'
            )
        return _tag('div', 'bracket-live-pill', 'LIVE')
    return _tag(
        'div', f'bracket-status-pill tone-{_esc(status_tone(status))}',
        _esc(status_label(status)),
    )


def _card_html(
    match: BracketMatch, ctx: BracketContext, *, placement: Optional[Placement] = None,
) -> str:
    classes = f'bracket-match {card_state_class(match, ctx)}'
    style = ''
    if placement is not None:
        style = (
            f'left: {placement.left}px; top: {placement.top}px; '
            f'width: {COL_WIDTH}px; height: {CARD_HEIGHT}px'
        )
    else:
        classes += ' bracket-match-flow'

    number = ctx.match_number.get(match.id)
    inner = ''.join([
        _tag('div', 'bracket-match-num', _esc(number)) if number is not None else '',
        _status_pill_html(match, ctx),
        _slot_html(match, 1, ctx),
        _slot_html(match, 2, ctx),
    ])
    return _tag(
        'div', classes, inner, style=style, attrs=f'data-match-id="{_esc(match.id)}"',
    )


def _round_meta_html(round_number: int, ctx: BracketContext) -> str:
    meta = ctx.rounds_config.get(str(round_number)) or {}
    scheduled, best_of = meta.get('scheduled_at'), meta.get('best_of')
    parts = []
    if scheduled:
        parts.append(_tag(
            'div', 'bracket-header-time',
            f'Round time: {_esc(ctx.scheduled_fmt(scheduled))}',
        ))
    if best_of:
        parts.append(_tag('div', 'bracket-badge', f'Best of {_esc(best_of)}'))
    return ''.join(parts)


def _header_html(
    round_number: int,
    ctx: BracketContext,
    *,
    is_grand_final: bool,
    is_reset: bool,
    max_winners_round: Optional[int],
    max_losers_magnitude: Optional[int],
    double_elim: bool,
) -> str:
    name = round_label(
        round_number,
        is_grand_final=is_grand_final,
        is_reset=is_reset,
        max_winners_round=max_winners_round,
        max_losers_magnitude=max_losers_magnitude,
        double_elim=double_elim,
    )
    meta = _round_meta_html(round_number, ctx)
    return _tag(
        'div', 'bracket-header',
        _tag('div', 'bracket-header-name', _esc(name))
        + (_tag('div', 'bracket-header-meta', meta) if meta else ''),
    )


def _section_html(
    title: Optional[str],
    layout: SectionLayout,
    matches_by_id: Dict[int, BracketMatch],
    ctx: BracketContext,
    *,
    gf_round: Optional[int] = None,
    reset_round: Optional[int] = None,
    max_winners_round: Optional[int] = None,
    max_losers_magnitude: Optional[int] = None,
    double_elim: bool = False,
) -> str:
    if not layout.placements:
        return ''
    headers = ''.join(
        _header_html(
            round_number, ctx,
            is_grand_final=(gf_round is not None and round_number == gf_round),
            is_reset=(reset_round is not None and round_number == reset_round),
            max_winners_round=max_winners_round,
            max_losers_magnitude=max_losers_magnitude,
            double_elim=double_elim,
        )
        for round_number in layout.columns
    )
    lines = []
    for seg in layout.connectors:
        if seg.orientation == 'h':
            lines.append(_tag(
                'div', 'bracket-line bracket-line-h',
                style=f'left: {seg.x}px; top: {seg.y}px; width: {max(seg.length, 0)}px',
            ))
        else:
            lines.append(_tag(
                'div', 'bracket-line bracket-line-v',
                style=f'left: {seg.x}px; top: {seg.y}px; height: {max(seg.length, 0)}px',
            ))
    cards = [
        _card_html(matches_by_id[match_id], ctx, placement=placement)
        for match_id, placement in layout.placements.items()
        if match_id in matches_by_id
    ]
    body = (
        _tag('div', 'bracket-headers', headers, style=f'width: {layout.width}px')
        + _tag(
            'div', 'bracket-canvas', ''.join(lines + cards),
            style=f'width: {layout.width}px; height: {layout.height}px',
        )
    )
    return (
        (_tag('div', 'bracket-section-title', _esc(title)) if title else '')
        + _tag('div', 'bracket-scroll', body)
    )


def _elimination_2d_html(matches: List[BracketMatch], ctx: BracketContext, *, double: bool) -> str:
    matches_by_id = {m.id: m for m in matches}
    positive = [m for m in matches if m.round > 0]
    negative = [m for m in matches if m.round < 0]

    if not double:
        layout = layout_section(match_nodes(positive))
        return _section_html(
            None, layout, matches_by_id, ctx,
            max_winners_round=max((m.round for m in positive), default=None),
        )

    grand_final, reset = detect_finals(matches)
    gf_round = grand_final.round if grand_final is not None else None
    reset_round = reset.round if reset is not None else None
    finals_rounds = {r for r in (gf_round, reset_round) if r is not None}
    max_wr = max((m.round for m in positive if m.round not in finals_rounds), default=None)
    max_lm = max((abs(m.round) for m in negative), default=None)

    out = _section_html(
        'Winners bracket', layout_section(match_nodes(positive)), matches_by_id, ctx,
        gf_round=gf_round, reset_round=reset_round,
        max_winners_round=max_wr, max_losers_magnitude=max_lm, double_elim=True,
    )
    if negative:
        out += _section_html(
            'Losers bracket', layout_section(match_nodes(negative)), matches_by_id, ctx,
            max_losers_magnitude=max_lm, double_elim=True,
        )
    return out


def _elimination_list_html(matches: List[BracketMatch], ctx: BracketContext, *, double: bool) -> str:
    """Per-round ``<details>`` accordion — the phone view, JavaScript-free.

    Stands in for the interactive page's ``ui.expansion`` list; the stylesheet's
    existing ``max-width: 1023px`` rule is what swaps the two views, so this
    needs no toggle and no client state.
    """
    if not matches:
        return ''
    positive = sorted({m.round for m in matches if m.round > 0})
    negative = sorted({m.round for m in matches if m.round < 0}, key=abs)

    grand_final, reset = detect_finals(matches) if double else (None, None)
    gf_round = grand_final.round if grand_final is not None else None
    reset_round = reset.round if reset is not None else None
    finals_rounds = {r for r in (gf_round, reset_round) if r is not None}
    max_lm = max((abs(r) for r in negative), default=None) if double else None
    max_wr = max([r for r in positive if r not in finals_rounds], default=None)

    by_round: Dict[int, List[BracketMatch]] = {}
    for m in matches:
        by_round.setdefault(m.round, []).append(m)

    def chron(r: int) -> int:
        return r if r >= 0 else 1000 + abs(r)

    open_rounds = [m.round for m in matches if m.state == BracketMatchState.OPEN]
    ordered = positive + negative
    default_round = (
        min(open_rounds, key=chron) if open_rounds else (ordered[-1] if ordered else None)
    )

    out = []
    for r in ordered:
        name = round_label(
            r,
            is_grand_final=r == gf_round,
            is_reset=r == reset_round,
            max_winners_round=max_wr,
            max_losers_magnitude=max_lm,
            double_elim=double,
        )
        meta = _round_meta_html(r, ctx)
        cards = ''.join(
            _card_html(m, ctx)
            for m in sorted(by_round.get(r, []), key=lambda m: m.position)
        )
        opened = ' open' if r == default_round else ''
        out.append(
            f'<details class="bracket-round-accordion"{opened}>'
            f'<summary class="wizs-summary">{_esc(name)}</summary>'
            + (_tag('div', 'bracket-round-meta', meta) if meta else '')
            + cards
            + '</details>'
        )
    return ''.join(out)
