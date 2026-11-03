"""Swiss / group data tables for the redesigned bracket view.

Standings (with the configured tiebreaker chain, advancement tint + cut line),
per-round Swiss pairings, and per-group crosstables — rendered as NiceGUI
elements (``ui.label`` escapes its text) rather than raw ``ui.html`` strings, so
user-controlled entrant names can never become an XSS sink. Styling routes
through ``static/css/brackets.css`` (``.bracket-standings`` / ``.bracket-pairings``
/ ``.bracket-crosstable``). Presentation-only: read-only display of the pure
``compute_standings`` output and the persisted match rows.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from nicegui import ui

from models import BracketMatch, BracketMatchState

from .cards import render_avatar

_TB_LABEL = {'buchholz': 'BH', 'omw': 'OMW%'}
_TB_TITLE = {
    'buchholz': "Buchholz — sum of opponents' match points",
    'omw': 'Opponent match-win %',
}


def render_standings(
    standings,
    entry_name: Dict[int, str],
    entry_seed: Dict[int, Optional[int]],
    tiebreakers,
    *,
    advancing: Optional[int] = None,
    show_elim: bool = False,
    dropped_ids=frozenset(),
    entry_avatar: Optional[Dict[int, str]] = None,
) -> None:
    """Standings table with tiebreaker columns, advancement tint, and a cut line.

    ``advancing`` (a position count) tints the top rows green and draws a heavier
    cut line after the Nth; ``show_elim`` additionally tints the rest red once the
    stage is locked; ``dropped_ids`` strikes dropped entrants through.
    """
    entry_avatar = entry_avatar or {}
    tb_cols = [tb for tb in tiebreakers if tb in _TB_LABEL]

    with ui.element('div').classes('bracket-standings-wrap'), \
            ui.element('div').classes('bracket-standings'):
        with ui.element('div').classes('bracket-strow is-head'):
            ui.label('#').classes('c-rank')
            ui.label('Entrant').classes('c-name')
            ui.label('W-L-D').classes('c-rec')
            ui.label('Pts').classes('c-pts')
            for tb in tb_cols:
                ui.label(_TB_LABEL[tb]).classes('c-tb').tooltip(_TB_TITLE[tb])

        for s in standings:
            name = entry_name.get(s.ref, 'Unknown')
            classes = 'bracket-strow'
            if s.ref in dropped_ids:
                classes += ' is-dropped'
            elif advancing is not None and s.rank <= advancing:
                classes += ' is-advancing'
            elif show_elim and advancing is not None and s.rank > advancing:
                classes += ' is-eliminated'
            if advancing is not None and s.rank == advancing:
                classes += ' cut-below'

            with ui.element('div').classes(classes):
                ui.label(str(s.rank)).classes('c-rank')
                with ui.element('div').classes('c-name bracket-entrant-cell'):
                    seed = entry_seed.get(s.ref)
                    if seed is not None:
                        ui.label(str(seed)).classes('bracket-seed') \
                            .tooltip(f'Seed {seed}')
                    render_avatar(name, entry_avatar.get(s.ref))
                    ui.label(name).classes('nm').tooltip(name)
                rec = f'{s.wins}-{s.losses}-{s.draws}' + (f' · {s.byes}B' if s.byes else '')
                ui.label(rec).classes('c-rec').tooltip(rec)
                ui.label(f'{s.points:g}').classes('c-pts')
                for tb in tb_cols:
                    v = s.tiebreakers.get(tb)
                    txt = '—' if v is None else (f'{v * 100:.1f}' if tb == 'omw' else f'{v:g}')
                    ui.label(txt).classes('c-tb')


def _record_chip(entry_id: Optional[int], records: Dict[int, tuple]) -> None:
    rec = records.get(entry_id) if entry_id is not None else None
    if rec is not None and (rec[0] or rec[1]):
        ui.label(f'({rec[0]}-{rec[1]})').classes('rec')


def render_pairings(
    matches: List[BracketMatch],
    entry_name: Dict[int, str],
    records: Dict[int, tuple],
) -> None:
    """One Swiss round's pairings: board #, A (record) vs B (record), result."""
    contests = sorted(
        [m for m in matches if m.entry1_id is not None and m.entry2_id is not None],
        key=lambda m: m.position,
    )
    byes = [m for m in matches if m.entry1_id is None or m.entry2_id is None]

    with ui.element('div').classes('bracket-pairings'):
        board = 1
        for m in contests:
            done = m.state == BracketMatchState.COMPLETE
            awin = done and m.winner_id == m.entry1_id
            bwin = done and m.winner_id == m.entry2_id
            if m.entry1_score is not None and m.entry2_score is not None:
                result = f'{m.entry1_score}–{m.entry2_score}'
            elif done and m.forfeit:
                result = 'FF'
            elif done:
                result = '✓'
            else:
                result = 'vs'
            with ui.element('div').classes(
                'bracket-prow ' + ('is-complete' if done else 'is-open')
            ):
                ui.label(str(board)).classes('c-board')
                with ui.element('div').classes('c-player right' + (' win' if awin else '')):
                    ui.label(entry_name.get(m.entry1_id, '?')).classes('nm')
                    _record_chip(m.entry1_id, records)
                ui.label(result).classes('c-result')
                with ui.element('div').classes('c-player' + (' win' if bwin else '')):
                    ui.label(entry_name.get(m.entry2_id, '?')).classes('nm')
                    _record_chip(m.entry2_id, records)
            board += 1
        for m in byes:
            who_id = m.entry1_id if m.entry1_id is not None else m.entry2_id
            with ui.element('div').classes('bracket-prow is-complete'):
                ui.label(str(board)).classes('c-board')
                with ui.element('div').classes('c-player bye'):
                    ui.label(f'{entry_name.get(who_id, "?")} — bye').classes('nm')
            board += 1


def render_crosstable(
    order_ids: List[int],
    matches: List[BracketMatch],
    entry_name: Dict[int, str],
) -> None:
    """Head-to-head grid: entrants on both axes, diagonal blacked out."""
    res: Dict[tuple, str] = {}
    for m in matches:
        if m.state != BracketMatchState.COMPLETE:
            continue
        if m.entry1_id is None or m.entry2_id is None:
            continue
        a, b = m.entry1_id, m.entry2_id
        if m.winner_id == a:
            res[(a, b)], res[(b, a)] = 'W', 'L'
        elif m.winner_id == b:
            res[(a, b)], res[(b, a)] = 'L', 'W'
        else:
            res[(a, b)] = res[(b, a)] = 'D'

    n = len(order_ids)
    cls_of = {'W': 'win', 'L': 'loss', 'D': 'draw'}
    grid = ui.element('div').classes('bracket-crosstable').style(
        f'grid-template-columns: minmax(90px, 1fr) repeat({n}, 26px)'
    )
    with grid:
        ui.element('div').classes('cx-corner')
        for i, e in enumerate(order_ids):
            ui.label(str(i + 1)).classes('cx-head').tooltip(entry_name.get(e, '?'))
        for i, a in enumerate(order_ids):
            ui.label(f'{i + 1}. {entry_name.get(a, "?")}').classes('cx-rowhead') \
                .tooltip(entry_name.get(a, '?'))
            for b in order_ids:
                if a == b:
                    ui.element('div').classes('cx-cell diag')
                else:
                    v = res.get((a, b), '')
                    ui.label(v).classes('cx-cell ' + cls_of.get(v, ''))
