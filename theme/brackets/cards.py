"""Match-card + section rendering for the redesigned bracket view.

Presentation layer over :mod:`theme.brackets.layout`: it consumes the pure
placements/connectors and paints absolutely-positioned NiceGUI elements — round
headers (sticky, with best-of/time chrome), match cards (seed · avatar · name ·
score cell, winner-accented), and the elbow connectors. All colors/sizes come
from ``static/css/brackets.css`` (``--bracket-*`` custom properties); this module
only positions elements and stamps the classes/data attributes the CSS and the
hover-run script key off.

No ORM writes and no business logic — read-only ``BracketMatch`` attribute access
for display, the sanctioned presentation-layer pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from nicegui import ui

from application.services.match.match_status import MatchStatus, label, tone
from models import BracketMatch, BracketMatchState
from theme.a11y import make_activatable

from .layout import (
    CARD_HEIGHT,
    COL_WIDTH,
    Placement,
    SectionLayout,
    SlotSource,
    avatar_hue,
    avatar_initial,
    round_label,
)

_STATE_CLASS = {
    BracketMatchState.PENDING: 'is-pending',
    BracketMatchState.OPEN: 'is-open',
    BracketMatchState.COMPLETE: 'is-complete',
}

# The derived live statuses that override the matchup's own state class (U2).
# ``SCHEDULED``/``UNSCHEDULED``/``COMPLETE``/``PENDING`` are absent on purpose:
# they say nothing ``_STATE_CLASS`` doesn't already say, and a card that shouted
# about every state would stop drawing the eye to the one being played.
LIVE_STATUS_CLASS = {
    MatchStatus.LIVE: 'is-live',
    MatchStatus.CHECKED_IN: 'is-checked-in',
    MatchStatus.AWAITING_RESULT: 'is-awaiting-result',
    MatchStatus.NEEDS_RESCHEDULE: 'is-needs-reschedule',
}


@dataclass
class BracketContext:
    """Everything the card layer needs to render one bracket, resolved once.

    Built by the page from the persisted graph; passed down to every section so
    the rendering stays a pure projection of these lookups.
    """

    entry_name: Dict[int, str]
    # entry_id -> Discord avatar URL, for the entrants whose entrant row is
    # linked to an account that has one. Absent = the initial-letter disc.
    entry_avatar: Dict[int, str] = field(default_factory=dict)
    entry_seed: Dict[int, Optional[int]] = field(default_factory=dict)
    match_number: Dict[int, int] = field(default_factory=dict)
    slot_sources: Dict = field(default_factory=dict)  # (match_id, slot) -> SlotSource
    rounds_config: Dict[str, dict] = field(default_factory=dict)
    scheduled_fmt: Callable[[str], str] = str  # UTC iso -> display string
    on_card_click: Optional[Callable[[int], None]] = None
    # {bracket_match_id: {'status': MatchStatus, 'watch_url': str}} from
    # ``BracketService.matchup_live_state``. Empty for a caller that didn't
    # resolve it, which renders exactly the pre-U2 card.
    live_state: Dict[int, dict] = field(default_factory=dict)
    viewer_entries: set = field(default_factory=set)
    # {bracket_match_id: (short, full)} — the next booked time of an
    # unfinished matchup (``render.booked_times``).
    booked: Dict[int, tuple] = field(default_factory=dict)


def _slot_entry_id(match: BracketMatch, slot: int) -> Optional[int]:
    return match.entry1_id if slot == 1 else match.entry2_id


def _slot_score(match: BracketMatch, slot: int) -> Optional[int]:
    return match.entry1_score if slot == 1 else match.entry2_score


def placeholder_text(
    match: BracketMatch, slot: int, ctx: BracketContext, *, completed: bool
) -> str:
    """Label for an empty slot: BYE, a source hint, or TBD."""
    other = _slot_entry_id(match, 2 if slot == 1 else 1)
    if completed and other is not None:
        return 'BYE'
    source: Optional[SlotSource] = ctx.slot_sources.get((match.id, slot))
    if source is not None:
        num = ctx.match_number.get(source.source_match_id)
        who = 'Winner' if source.kind == 'winner' else 'Loser'
        return f'{who} of {num}' if num is not None else who
    return 'TBD'


def _render_slot(match: BracketMatch, slot: int, ctx: BracketContext) -> None:
    completed = match.state == BracketMatchState.COMPLETE
    entry_id = _slot_entry_id(match, slot)
    is_winner = completed and match.winner_id is not None and entry_id == match.winner_id
    is_loser = completed and match.winner_id is not None and entry_id != match.winner_id

    classes = 'bracket-slot'
    if entry_id is None:
        classes += ' is-empty'
    elif entry_id in ctx.viewer_entries:
        classes += ' is-viewer'
    if is_winner:
        classes += ' is-winner'
    if is_loser:
        classes += ' is-loser'

    row = ui.element('div').classes(classes)
    if entry_id is not None:
        row.props(f'data-entrant-id={entry_id}')

    with row:
        seed = ctx.entry_seed.get(entry_id) if entry_id is not None else None
        # Always emit the seed cell (empty when unseeded) so the four columns
        # (seed · avatar · name · score) stay aligned across every slot.
        ui.label(str(seed) if seed is not None else '').classes('bracket-seed')

        if entry_id is not None:
            name = ctx.entry_name.get(entry_id, 'Unknown')
            render_avatar(name, ctx.entry_avatar.get(entry_id))
            mine = entry_id in ctx.viewer_entries
            ui.label(name).classes('bracket-name').tooltip(f'{name} (you)' if mine else name)
            if mine:
                ui.label('(you)').classes('bracket-you')
        else:
            ui.element('div').classes('bracket-avatar is-empty')
            ui.label(placeholder_text(match, slot, ctx, completed=completed)) \
                .classes('bracket-name is-placeholder')

        # Number when scored, else a W/L glyph; FF marks the forfeiting (losing)
        # side of a walkover.
        score = _slot_score(match, slot)
        if completed and match.forfeit and is_loser:
            score_txt = 'FF'
        elif score is not None:
            score_txt = str(score)
        elif is_winner:
            score_txt = 'W'
        elif is_loser:
            score_txt = 'L'
        else:
            score_txt = ''
        ui.label(score_txt).classes('bracket-score')


def render_avatar(name: str, url: Optional[str] = None) -> None:
    """An entrant's disc: their Discord avatar when we have one, else the initial.

    Shared with :mod:`theme.brackets.tables` so the standings and the cards paint
    the same disc.
    """
    with ui.element('div').classes('bracket-avatar').style(
        f'--bracket-avatar-hue: {avatar_hue(name)}'
    ):
        # The initial always renders; the avatar sits on top of it. A hash that
        # went stale 404s, ``q-img`` paints nothing, and the letter shows through
        # — no broken-image glyph, no blank disc.
        ui.label(avatar_initial(name))
        if url:
            ui.image(url).classes('bracket-avatar-img').props('no-spinner')


def card_state_class(match: BracketMatch, ctx: BracketContext) -> str:
    """The card's state class: the derived live status if there is one, else the
    matchup's own state (U2)."""
    live = ctx.live_state.get(match.id) or {}
    status = live.get('status')
    return LIVE_STATUS_CLASS.get(status) or _STATE_CLASS.get(match.state, 'is-pending')


def _render_status_pill(match: BracketMatch, ctx: BracketContext) -> None:
    """The corner badge: a linked "LIVE" for a game in progress, else a chip.

    The watch link is rendered for anonymous viewers too (D4) — the stream and
    the race room are already public on the schedule, so the bracket becomes the
    link you send a viewer rather than a dead end. ``target=_blank`` with
    ``rel=noopener`` because it leaves the app.
    """
    live = ctx.live_state.get(match.id) or {}
    status = live.get('status')
    if status is None or status not in LIVE_STATUS_CLASS:
        return
    if status == MatchStatus.LIVE:
        url = live.get('watch_url') or ''
        if url:
            ui.link('LIVE', url, new_tab=True).classes('bracket-live-pill') \
                .tooltip('Watch this match')
        else:
            ui.label('LIVE').classes('bracket-live-pill')
        return
    ui.label(label(status)).classes(f'bracket-status-pill tone-{tone(status)}')


def _card_classes(match: BracketMatch, ctx: BracketContext) -> str:
    classes = card_state_class(match, ctx)
    if ctx.viewer_entries and {match.entry1_id, match.entry2_id} & ctx.viewer_entries:  # type: ignore[attr-defined]
        classes += ' is-mine'
    return classes


def _render_booked(match: BracketMatch, ctx: BracketContext) -> None:
    """The next booked time, on the card's bottom edge, for an unfinished matchup."""
    booked = ctx.booked.get(match.id)
    if booked:
        short, full = booked
        ui.label(short).classes('bracket-time-pill').tooltip(f'Scheduled for {full}')


def render_match_card(match: BracketMatch, placement: Placement, ctx: BracketContext) -> None:
    card = ui.element('div').classes(f'bracket-match {_card_classes(match, ctx)}').style(
        f'left: {placement.left}px; top: {placement.top}px; '
        f'width: {COL_WIDTH}px; height: {CARD_HEIGHT}px'
    )
    card.props(f'data-match-id={match.id}')

    number = ctx.match_number.get(match.id)
    with card:
        if number is not None:
            ui.label(str(number)).classes('bracket-match-num')
        _render_status_pill(match, ctx)
        _render_slot(match, 1, ctx)
        _render_slot(match, 2, ctx)
        _render_booked(match, ctx)

    if ctx.on_card_click is not None:
        card.classes('is-clickable')
        card.on('click', lambda _=None, mid=match.id: ctx.on_card_click(mid))
        make_activatable(card)


def render_mobile_card(match: BracketMatch, ctx: BracketContext) -> None:
    """The same match card in normal flow (full-width) for the mobile accordion."""
    card = ui.element('div').classes(
        f'bracket-match bracket-match-flow {_card_classes(match, ctx)}'
    )
    card.props(f'data-match-id={match.id}')
    number = ctx.match_number.get(match.id)
    with card:
        if number is not None:
            ui.label(str(number)).classes('bracket-match-num')
        _render_status_pill(match, ctx)
        _render_slot(match, 1, ctx)
        _render_slot(match, 2, ctx)
        _render_booked(match, ctx)
    if ctx.on_card_click is not None:
        card.classes('is-clickable')
        card.on('click', lambda _=None, mid=match.id: ctx.on_card_click(mid))
        make_activatable(card)


def _render_header(
    col: int,
    round_number: int,
    ctx: BracketContext,
    *,
    is_grand_final: bool,
    is_reset: bool,
    max_winners_round: Optional[int],
    max_losers_magnitude: Optional[int],
    double_elim: bool,
) -> None:
    with ui.element('div').classes('bracket-header'):
        ui.label(
            round_label(
                round_number,
                is_grand_final=is_grand_final,
                is_reset=is_reset,
                max_winners_round=max_winners_round,
                max_losers_magnitude=max_losers_magnitude,
                double_elim=double_elim,
            )
        ).classes('bracket-header-name')
        meta = ctx.rounds_config.get(str(round_number)) or {}
        with ui.row().classes('bracket-header-meta'):
            scheduled = meta.get('scheduled_at')
            if scheduled:
                # "Round time" — the *planned* time for the round, which is a
                # different thing from each game's ``Match.scheduled_at`` shown
                # on the card and in the dialog, and allowed to differ from it
                # (U3c). Unlabelled, the two read as a contradiction.
                ui.label(f'Round time: {ctx.scheduled_fmt(scheduled)}') \
                    .classes('bracket-header-time') \
                    .tooltip('Planned time for this round; each game carries its own scheduled time')
            best_of = meta.get('best_of')
            if best_of:
                ui.label(f'Best of {best_of}').classes('bracket-badge')


def render_section(
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
) -> None:
    """Render one bracket section (winners+finals, or losers) end to end."""
    if not layout.placements:
        return
    if title:
        ui.label(title).classes('bracket-section-title')

    with ui.element('div').classes('bracket-scroll'):
        with ui.element('div').classes('bracket-headers').style(f'width: {layout.width}px'):
            for col, round_number in enumerate(layout.columns):
                _render_header(
                    col, round_number, ctx,
                    is_grand_final=(gf_round is not None and round_number == gf_round),
                    is_reset=(reset_round is not None and round_number == reset_round),
                    max_winners_round=max_winners_round,
                    max_losers_magnitude=max_losers_magnitude,
                    double_elim=double_elim,
                )
        with ui.element('div').classes('bracket-canvas').style(
            f'width: {layout.width}px; height: {layout.height}px'
        ):
            for seg in layout.connectors:
                if seg.orientation == 'h':
                    ui.element('div').classes('bracket-line bracket-line-h').style(
                        f'left: {seg.x}px; top: {seg.y}px; width: {max(seg.length, 0)}px'
                    )
                else:
                    ui.element('div').classes('bracket-line bracket-line-v').style(
                        f'left: {seg.x}px; top: {seg.y}px; height: {max(seg.length, 0)}px'
                    )
            for match_id, placement in layout.placements.items():
                match = matches_by_id.get(match_id)
                if match is not None:
                    render_match_card(match, placement, ctx)
