"""The run sheet's tournament line, shared by the admin, proctor and player sheets."""

# The tournament line under the names: short name, bracket, and which game of
# a best-of-N set this is. The phone card, which has no Stage column, adds the
# stage on the end.
_META_INNER = ("""{{ props.row.tournament_short || props.row.tournament }}<template v-if="props.row.bracket"> · {{ props.row.bracket.name }}"""
               """<template v-if="props.row.bracket.game"> · <span class="run-sheet__game">Game {{ props.row.bracket.game }}<template v-if="props.row.bracket.best_of > 1"> of {{ props.row.bracket.best_of }}</template></span></template></template>"""
               """<template v-if="props.row.set_label"> · <span class="run-sheet__game">{{ props.row.set_label }}</span></template>""")
META = """<span class="run-sheet__meta" :title="props.row.tournament">""" + _META_INNER + """</span>"""
META_WITH_STAGE = ("""<span class="run-sheet__meta" :title="props.row.tournament">""" + _META_INNER
                   + """<template v-if="props.row._stage"> · {{ props.row._stage }}</template></span>""")

# The state as a word in its own colour, with the same colour down the card's
# or row's left edge. The word carries it for anyone who can't tell the colours.
STATUS_WORD = """<span class="run-sheet__status" :class="'st-' + props.row._tone">{{ props.row._status }}</span>"""
RAIL = "'run-sheet--' + props.row._tone"


def card_head(time: str, status: str = STATUS_WORD) -> str:
    """A phone card's first line: the time and its end, then the state on the right."""
    return '<span class="run-sheet__head"><span class="run-sheet__head-time">' + time + '</span>' + status + '</span>'
