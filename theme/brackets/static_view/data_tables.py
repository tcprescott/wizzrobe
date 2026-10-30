"""Standings, pairings, crosstables and group/Swiss layout as static HTML.

The websocket-free twin of :mod:`theme.brackets.tables`, emitting the same class
names so one stylesheet serves both views. Standings come from the same pure
``compute_standings`` pass the interactive page runs, so the static table can
never disagree with the live one. Interactive chrome that needs client state
(Quasar tabs, expansion panels) becomes native ``<details>`` disclosures.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

from application.services.bracket_engines.standings import (
    compute_standings,
    standings_config_from,
)
from models import BracketEntry, BracketEntryStatus, BracketMatch, BracketMatchState

from ..render import entry_records, results_from_matches
from .markup import _avatar_html, _esc, _tag

_TB_LABEL = {'buchholz': 'BH', 'omw': 'OMW%'}
_TB_TITLE = {
    'buchholz': "Buchholz — sum of opponents' match points",
    'omw': 'Opponent match-win %',
}


def _standings_html(
    standings,
    entry_name: Dict[int, str],
    entry_seed: Dict[int, Optional[int]],
    tiebreakers: Sequence[str],
    *,
    advancing: Optional[int] = None,
    show_elim: bool = False,
    dropped_ids: Iterable[int] = frozenset(),
    entry_avatar: Optional[Dict[int, str]] = None,
) -> str:
    entry_avatar = entry_avatar or {}
    dropped = set(dropped_ids)
    tb_cols = [tb for tb in tiebreakers if tb in _TB_LABEL]

    head = ''.join([
        _tag('div', 'c-rank', '#'),
        _tag('div', 'c-name', 'Entrant'),
        _tag('div', 'c-rec', 'W-L-D'),
        _tag('div', 'c-pts', 'Pts'),
        *[
            _tag('div', 'c-tb', _esc(_TB_LABEL[tb]), attrs=f'title="{_esc(_TB_TITLE[tb])}"')
            for tb in tb_cols
        ],
    ])
    rows = [_tag('div', 'bracket-strow is-head', head)]

    for s in standings:
        name = entry_name.get(s.ref, 'Unknown')
        classes = 'bracket-strow'
        if s.ref in dropped:
            classes += ' is-dropped'
        elif advancing is not None and s.rank <= advancing:
            classes += ' is-advancing'
        elif show_elim and advancing is not None and s.rank > advancing:
            classes += ' is-eliminated'
        if advancing is not None and s.rank == advancing:
            classes += ' cut-below'

        seed = entry_seed.get(s.ref)
        name_cell = _tag(
            'div', 'c-name bracket-entrant-cell',
            (
                _tag('div', 'bracket-seed', _esc(seed), attrs=f'title="Seed {_esc(seed)}"')
                if seed is not None else ''
            )
            + _avatar_html(name, entry_avatar.get(s.ref))
            + _tag('div', 'nm', _esc(name), attrs=f'title="{_esc(name)}"'),
        )
        rec = f'{s.wins}-{s.losses}-{s.draws}' + (f' · {s.byes}B' if s.byes else '')
        cells = [
            _tag('div', 'c-rank', _esc(s.rank)),
            name_cell,
            _tag('div', 'c-rec', _esc(rec), attrs=f'title="{_esc(rec)}"'),
            _tag('div', 'c-pts', _esc(f'{s.points:g}')),
        ]
        for tb in tb_cols:
            v = s.tiebreakers.get(tb)
            txt = '—' if v is None else (f'{v * 100:.1f}' if tb == 'omw' else f'{v:g}')
            cells.append(_tag('div', 'c-tb', _esc(txt)))
        rows.append(_tag('div', classes, ''.join(cells)))

    return _tag(
        'div', 'bracket-standings-wrap',
        _tag('div', 'bracket-standings', ''.join(rows)),
    )


def _record_chip_html(entry_id: Optional[int], records: Dict[int, tuple]) -> str:
    rec = records.get(entry_id) if entry_id is not None else None
    if rec is not None and (rec[0] or rec[1]):
        return _tag('div', 'rec', _esc(f'({rec[0]}-{rec[1]})'))
    return ''


def _pairings_html(
    matches: List[BracketMatch], entry_name: Dict[int, str], records: Dict[int, tuple],
) -> str:
    contests = sorted(
        [m for m in matches if m.entry1_id is not None and m.entry2_id is not None],
        key=lambda m: m.position,
    )
    byes = [m for m in matches if m.entry1_id is None or m.entry2_id is None]

    rows = []
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
        rows.append(_tag(
            'div', 'bracket-prow ' + ('is-complete' if done else 'is-open'),
            _tag('div', 'c-board', _esc(board))
            + _tag(
                'div', 'c-player right' + (' win' if awin else ''),
                _tag('div', 'nm', _esc(entry_name.get(m.entry1_id, '?')))
                + _record_chip_html(m.entry1_id, records),
            )
            + _tag('div', 'c-result', _esc(result))
            + _tag(
                'div', 'c-player' + (' win' if bwin else ''),
                _tag('div', 'nm', _esc(entry_name.get(m.entry2_id, '?')))
                + _record_chip_html(m.entry2_id, records),
            ),
        ))
        board += 1
    for m in byes:
        who_id = m.entry1_id if m.entry1_id is not None else m.entry2_id
        rows.append(_tag(
            'div', 'bracket-prow is-complete',
            _tag('div', 'c-board', _esc(board))
            + _tag(
                'div', 'c-player bye',
                _tag('div', 'nm', _esc(f'{entry_name.get(who_id, "?")} — bye')),
            ),
        ))
        board += 1
    return _tag('div', 'bracket-pairings', ''.join(rows))


def _crosstable_html(
    order_ids: List[int], matches: List[BracketMatch], entry_name: Dict[int, str],
) -> str:
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

    cls_of = {'W': 'win', 'L': 'loss', 'D': 'draw'}
    cells = [_tag('div', 'cx-corner')]
    for i, e in enumerate(order_ids):
        cells.append(_tag(
            'div', 'cx-head', _esc(i + 1),
            attrs=f'title="{_esc(entry_name.get(e, "?"))}"',
        ))
    for i, a in enumerate(order_ids):
        label = f'{i + 1}. {entry_name.get(a, "?")}'
        cells.append(_tag('div', 'cx-rowhead', _esc(label), attrs=f'title="{_esc(label)}"'))
        for b in order_ids:
            if a == b:
                cells.append(_tag('div', 'cx-cell diag'))
            else:
                v = res.get((a, b), '')
                cells.append(_tag('div', 'cx-cell ' + cls_of.get(v, ''), _esc(v)))
    return _tag(
        'div', 'bracket-crosstable', ''.join(cells),
        style=f'grid-template-columns: minmax(90px, 1fr) repeat({len(order_ids)}, 26px)',
    )


def _results_list_html(matches: List[BracketMatch], entry_name: Dict[int, str]) -> str:
    played = [m for m in matches if m.state == BracketMatchState.COMPLETE]
    upcoming = [m for m in matches if m.state == BracketMatchState.OPEN]
    if not played and not upcoming:
        return _tag('div', 'wizs-muted', 'No matches yet.')
    lines = []
    for m in played:
        if m.entry1_id is None or m.entry2_id is None:
            winner = entry_name.get(m.winner_id, 'Unknown')
            lines.append(_tag('div', 'wizs-line', _esc(f'{winner} — bye')))
            continue
        winner_name = entry_name.get(m.winner_id, 'Unknown')
        loser_id = m.entry2_id if m.winner_id == m.entry1_id else m.entry1_id
        loser_name = entry_name.get(loser_id, 'Unknown')
        lines.append(_tag('div', 'wizs-line', _esc(f'{winner_name} def. {loser_name}')))
    for m in upcoming:
        name1 = entry_name.get(m.entry1_id, 'TBD') if m.entry1_id else 'TBD'
        name2 = entry_name.get(m.entry2_id, 'TBD') if m.entry2_id else 'TBD'
        lines.append(_tag('div', 'wizs-line wizs-muted', _esc(f'{name1} vs {name2}')))
    return ''.join(lines)


def _group_letter(group: Optional[int]) -> Optional[str]:
    if group is None:
        return None
    try:
        return chr(ord('A') + (int(group) - 1))
    except (TypeError, ValueError):
        return str(group)


def _round_robin_html(
    entries: List[BracketEntry],
    matches: List[BracketMatch],
    entry_name: Dict[int, str],
    config: Optional[dict],
    *,
    advancement: Optional[dict],
    complete: bool,
    entry_avatar: Optional[Dict[int, str]] = None,
) -> str:
    entry_seed = {e.id: e.seed for e in entries}
    tiebreakers = standings_config_from(config).tiebreakers
    group_of: Dict[int, Optional[int]] = {}
    for m in matches:
        for eid in (m.entry1_id, m.entry2_id):
            if eid is not None:
                group_of[eid] = m.group_number
    for e in entries:
        group_of.setdefault(e.id, e.group_number)

    groups = sorted({group_of.get(e.id) for e in entries}, key=lambda g: (g is None, g))
    per_group_cut = (
        advancement.get('count') if advancement and advancement.get('per_group') else None
    )

    cards = []
    for group in groups:
        group_entry_ids = [e.id for e in entries if group_of.get(e.id) == group]
        group_matches = [m for m in matches if m.group_number == group]
        letter = _group_letter(group)
        title = _tag(
            'div', 'bracket-section-title', _esc(f'Group {letter}' if letter else 'Standings'),
        )
        if not group_entry_ids:
            cards.append(_tag('div', 'bracket-group-card wizs-card', title + _tag(
                'div', 'wizs-muted', 'No entrants yet.')))
            continue
        standings = compute_standings(
            group_entry_ids, results_from_matches(group_matches), standings_config_from(config),
        )
        body = _standings_html(
            standings, entry_name, entry_seed, tiebreakers,
            advancing=per_group_cut, show_elim=complete, entry_avatar=entry_avatar,
        )
        if 2 <= len(group_entry_ids) <= 10:
            body += (
                '<details class="wizs-details"><summary class="wizs-summary">Crosstable</summary>'
                + _crosstable_html([s.ref for s in standings], group_matches, entry_name)
                + '</details>'
            )
        body += (
            '<details class="wizs-details"><summary class="wizs-summary">Matches</summary>'
            + _results_list_html(group_matches, entry_name)
            + '</details>'
        )
        cards.append(_tag('div', 'bracket-group-card wizs-card', title + body))

    return _tag('div', 'bracket-groups-grid', ''.join(cards))


def _swiss_html(
    entries: List[BracketEntry],
    matches: List[BracketMatch],
    entry_name: Dict[int, str],
    config: Optional[dict],
    *,
    advancement: Optional[dict],
    complete: bool,
    entry_avatar: Optional[Dict[int, str]] = None,
) -> str:
    entry_seed = {e.id: e.seed for e in entries}
    dropped = {e.id for e in entries if e.status == BracketEntryStatus.DROPPED}
    tiebreakers = standings_config_from(config).tiebreakers

    out = _tag('div', 'bracket-section-title', 'Standings')
    if not entries:
        return out + _tag('div', 'wizs-muted', 'No entrants yet.')

    standings = compute_standings(
        [e.id for e in entries], results_from_matches(matches), standings_config_from(config),
    )
    out += _standings_html(
        standings, entry_name, entry_seed, tiebreakers,
        advancing=advancement.get('count') if advancement else None,
        show_elim=complete, dropped_ids=dropped, entry_avatar=entry_avatar,
    )
    if 'head_to_head' in tiebreakers:
        out += _tag(
            'div', 'wizs-muted', 'Remaining ties are broken by head-to-head result.',
        )

    rounds = sorted({m.round for m in matches})
    if not rounds:
        return out

    records = entry_records([e.id for e in entries], matches)
    open_rounds = [m.round for m in matches if m.state == BracketMatchState.OPEN]
    default_round = max(open_rounds) if open_rounds else max(rounds)

    out += _tag('div', 'bracket-section-title', 'Pairings')
    for r in rounds:
        opened = ' open' if r == default_round else ''
        out += (
            f'<details class="wizs-details"{opened}>'
            f'<summary class="wizs-summary">Round {_esc(r)}</summary>'
            + _pairings_html([m for m in matches if m.round == r], entry_name, records)
            + '</details>'
        )
    return out
