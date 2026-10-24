"""Fold a run sheet's groups, and remember the folds per viewer.

A group header is a button (``match_run_sheet.fold_header``). Folding stamps
``_folded`` on the group's rows, which the slots draw as the header alone and
``RUN_SHEET_FILTER`` keeps out of the pager. The choice is saved per group in
the board's table preferences, so a group that starts folded
(``FOLDED_BY_DEFAULT``) stays open once its viewer has opened it.
"""

from typing import Any, Optional

from nicegui import background_tasks, context, ui

from application.services import TablePreferenceService
from application.table_preferences_context import table_prefs_for
from theme.tables.admin_crud import current_actor
from theme.tables.match_run_sheet import FOLDED_BY_DEFAULT


class MatchBoardFoldMixin:
    """Folded run-sheet groups for a ``MatchTableView``."""

    # Supplied by MatchTableView.
    table: Any
    table_key: Optional[str]

    def _wire_folding(self) -> None:
        saved = table_prefs_for(self.table_key) if self.table_key else None
        folded = (saved or {}).get('folded')
        self._folded = dict(folded) if isinstance(folded, dict) else {}
        self.table.on('toggle_group', lambda event: background_tasks.create(
            self._toggle_group(event.args, context.client)))
        self._racers_open: set = set()
        self.table.on('toggle_racers', lambda event: self._toggle_racers(event.args))

    def _is_folded(self, group: str) -> bool:
        return getattr(self, '_folded', {}).get(group, group in FOLDED_BY_DEFAULT)

    def _stamp_folded(self, rows: list) -> list:
        opened = getattr(self, '_racers_open', set())
        for row in rows:
            row['_folded'] = self._is_folded(row['_group'])
            row['_who_open'] = row.get('id') in opened
        return rows

    def _toggle_racers(self, match_id) -> None:
        """Show every racer on one big race's line, or fold them back.

        Kept for the tab, not saved: it's a peek. Held on the view rather than
        the row, so a live update to the row doesn't fold it again.
        """
        opened = getattr(self, '_racers_open', set())
        opened ^= {match_id}
        self._racers_open = opened
        for row in self.table.rows:
            if row.get('id') == match_id:
                row['_who_open'] = match_id in opened
        self.table.update()

    async def _toggle_group(self, group, client) -> None:
        """Fold or open one group, and remember it for this viewer.

        Re-flags the rows already on the board rather than refreshing: a fold
        is not a tidy, so rows held in place stay held. A held row keeps the
        group it is drawn under, and that is the group it folds with.
        """
        if not isinstance(group, str):
            return
        folded = not self._is_folded(group)
        self._folded = {**getattr(self, '_folded', {}), group: folded}
        for row in self.table.rows:
            if row.get('_group') == group:
                row['_folded'] = folded
        self.table.update()
        if not self.table_key:
            return
        with client:
            actor = await current_actor()
            if actor is None:
                return
            try:
                await TablePreferenceService().set_folded(actor, self.table_key, group, folded)
            except ValueError as e:
                ui.notify(str(e), color='warning')
