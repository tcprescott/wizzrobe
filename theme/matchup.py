"""A matchup line whose undecided sides read as placeholders.

A booking made before the bracket decides its players carries "Andy or Benny",
"Winner of C" or "TBD" for each open side. Set in the same type as a real name
they read as players, so every matchup line renders them muted and italic, the
way the bracket pages draw an empty slot. The pieces come from
:func:`application.utils.match_labels.matchup_parts`; this module only draws them.
"""

from html import escape
from typing import Sequence

from nicegui import ui

from application.utils.match_labels import MatchupPart

TBD_CLASS = 'wiz-tbd'

# For a q-table slot whose row carries ``_who_parts``.
WHO_PARTS_VUE = (
    '<template v-for="(p, i) in props.row._who_parts" :key="i">'
    f'<span v-if="p.tbd" class="{TBD_CLASS}">{{{{ p.text }}}}</span>'
    '<template v-else>{{ p.text }}</template></template>'
)


def matchup_label(parts: Sequence[MatchupPart], classes: str = '', *, tag: str = 'span') -> ui.html:
    # One element, not one per name: a long board builds a row at a time.
    return ui.html(matchup_html(parts), tag=tag).classes(classes)


def matchup_html(parts: Sequence[MatchupPart]) -> str:
    return ''.join(
        f'<span class="{TBD_CLASS}">{escape(str(p["text"]))}</span>' if p['tbd'] else escape(str(p['text']))
        for p in parts
    )
