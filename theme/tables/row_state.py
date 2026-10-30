"""Viewer-stamped match-row fields, carried across a single-row re-fetch.

``MatchTableView.refresh`` stamps each row with fields that belong to the viewer
rather than the match: ``_watching``, ``_stream_volunteer``, ``_can_reschedule``,
``_reschedule_pending``, the ``_hard_*`` family. ``update_row_by_id`` rebuilds
one row from the display service, which knows nothing of them, so every field it
does not carry is silently dropped from that row until the next full refresh.

It used to carry a hand-kept list, and the list fell behind: acknowledging a
match, or any ``match_live`` flash, removed the row's Ask to change button. The
convention is now the contract: a leading underscore marks a viewer-stamped
field, and every one is carried unless the fresh row already sets it.

Two families are *not* carried, because someone other than the viewer changes
them: the reschedule pair (staff approving a move clears "Change requested" and
brings Ask to change back) and the harder-preset fields (staff override, the
seed rolling, the other player opting in). ``update_row_by_id`` recomputes those
for the one match instead; carrying them showed a moved match still "waiting on
staff" until a reload.
"""

from typing import Any, Dict

from theme.tables.hard_preset_rows import HARD_PRESET_ROW_FIELDS

# Not carried across updates: carrying one would replay it.
TRANSIENT_ROW_FIELDS = frozenset({'_flash'})

RESTAMPED_ROW_FIELDS = frozenset(
    {'_can_reschedule', '_reschedule_pending', *HARD_PRESET_ROW_FIELDS}
)


def carry_viewer_row_state(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Copy each viewer-only ``_`` field of ``source`` that ``target`` does not set."""
    skip = TRANSIENT_ROW_FIELDS | RESTAMPED_ROW_FIELDS
    for key, value in source.items():
        if key.startswith('_') and key not in skip and key not in target:
            target[key] = value
