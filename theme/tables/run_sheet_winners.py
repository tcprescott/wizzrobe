"""Who won, on a live two-player line: both names as buttons, one tap each.

The tap is held behind Undo (``application.pending_actions``), so while it is
held the line says so in place of the buttons, on every open board.
"""

from application.run_sheet import stamp_winners, winner_choices

__all__ = ['stamp_winners', 'winner_buttons', 'winner_choices']


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
