"""The run sheet's tournament line, shared by the admin, proctor and player sheets."""

_META_INNER = ("""{{ props.row.tournament_short || props.row.tournament }}<template v-if="props.row.bracket"> · {{ props.row.bracket.name }}"""
               """<template v-if="props.row.bracket.game"> · <span class="run-sheet__game">Game {{ props.row.bracket.game }}<template v-if="props.row.bracket.best_of > 1"> of {{ props.row.bracket.best_of }}</template></span></template></template>"""
               """<template v-if="props.row.set_label"> · <span class="run-sheet__game">{{ props.row.set_label }}</span></template>""")
META = """<span class="run-sheet__meta" :title="props.row.tournament">""" + _META_INNER + """</span>"""
META_WITH_STAGE = ("""<span class="run-sheet__meta" :title="props.row.tournament">""" + _META_INNER
                   + """<template v-if="props.row._stage"> · {{ props.row._stage }}</template></span>""")

STATUS_WORD = """<span class="run-sheet__status" :class="'st-' + props.row._tone">{{ props.row._status }}</span>"""
RAIL = "'run-sheet--' + props.row._tone"


def card_head(time: str, status: str = STATUS_WORD) -> str:
    """A phone card's first line: the time and its end, then the state on the right."""
    return '<span class="run-sheet__head"><span class="run-sheet__head-time">' + time + '</span>' + status + '</span>'


def crew_columns(columns: list[dict], broadcasters: bool) -> list[dict]:
    """``columns`` without the Broadcasters column on an event that doesn't use them."""
    return columns if broadcasters else [c for c in columns if c['name'] != 'broadcasters']
