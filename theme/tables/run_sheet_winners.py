"""Who won, on a live two-player line: both names as buttons, one tap each.

The tap is held behind Undo (``application.pending_actions``), so while it is
held the line says so in place of the buttons, on every open board.
"""

from application import pending_actions


def winner_choices(row: dict) -> list[dict]:
    """The two players a live head-to-head can be won by, or none."""
    players = row.get('players') or []
    if (row.get('state') != 'Started' or len(players) != 2 or row.get('placeholders')
            or row.get('is_racetime') or row.get('played_on_site')
            or not all(p.get('id') for p in players)):
        return []
    return [{'id': p['id'], 'name': p.get('name') or ''} for p in players]


def stamp_winners(row: dict) -> None:
    row['_winners'] = winner_choices(row)
    row['_pending_result'] = pending_actions.pending_label(('result', row.get('id')))


def winner_buttons(run: str = 'true', *, inline: bool = False) -> str:
    """The pair of buttons, or the held result, for rows where ``run`` (a Vue
    expression over ``props.row``) says this viewer may record one. ``inline``
    lays them under the names, left-aligned, as the admin board's Match cell
    and phone card do; otherwise they sit right-aligned in the Next step."""
    mod = ' run-sheet__winners--inline' if inline else ''
    return (
        f"""<span v-if="({run}) && props.row._pending_result" class="run-sheet__pending{mod}" role="status">"""
        """<q-spinner size="14px" aria-hidden="true" />{{ props.row._pending_result }}</span>"""
        f"""<span v-else-if="({run}) && props.row._winners && props.row._winners.length" class="run-sheet__winners{mod}" role="group" """
        """:aria-label="'Who won ' + (props.row._who || 'this match') + '?'" @click.stop>"""
        """<span class="run-sheet__winners-label" aria-hidden="true">Who won?</span>"""
        """<q-btn v-for="w in props.row._winners" :key="w.id" dense no-caps outline color="primary" """
        """class="run-sheet__winner" :label="w.name" :aria-label="w.name + ' won'" """
        """@click.stop="$parent.$emit('report_winner', { key: props.row.id, player: w.id })" /></span>"""
    )
