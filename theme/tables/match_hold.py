"""Hold a match board still while it changes, until the viewer tidies it.

A run sheet groups and orders its rows by state ("Late", "Ready to start", "In
play"…), so every check-in, start or finish used to move a row to another group,
and a deleted match took its row with it. Whoever was about to tap the next row
found a different one under their finger. That happened for the viewer's own
taps and for every other proctor's, live, through ``match_live``.

Now a change redraws the row where it sits. A row whose place would change keeps
it and says where it belongs ("Now Ready to start"); a deleted row fades in
place, inert; a new match waits off the board. A "Tidy up" pill counts what is
being held. The board re-sorts in one step when the viewer taps the pill,
scrolls, or leaves it untouched for a minute. docs/style-guide.md#hold-still.

``MatchTableView`` mixes this in. ``refresh`` is the tidy: it rebuilds every
row from the database, so it drops the held state with it.
"""

import json
from typing import Any

from nicegui import ui

IDLE_TIDY_MS = 60_000

_PLACE_FIELDS = ('_group', '_group_start', '_group_count', '_folded')

# Listeners live on window so the board's own redraws never reset them.
_WATCH_JS = """
(() => {
  const name = __NAME__;
  const held = (window.__wizHeldBoards = window.__wizHeldBoards || {});
  if (held[name]) return;
  let timer;
  const idle = () => { clearTimeout(timer); timer = setTimeout(fire, __IDLE__); };
  const stop = () => {
    clearTimeout(timer);
    window.removeEventListener('scroll', fire);
    ['pointerdown', 'keydown'].forEach((t) => window.removeEventListener(t, idle, true));
    delete held[name];
  };
  function fire() { stop(); emitEvent(name, {}); }
  window.addEventListener('scroll', fire, { passive: true });
  ['pointerdown', 'keydown'].forEach((t) => window.addEventListener(t, idle, true));
  held[name] = stop;
  idle();
})();
"""

_UNWATCH_JS = 'window.__wizHeldBoards && window.__wizHeldBoards[__NAME__] && window.__wizHeldBoards[__NAME__]();'


class MatchBoardHoldMixin:
    """Held-change bookkeeping and the Tidy up pill for a ``MatchTableView``."""

    table: Any
    _bg: Any
    refresh: Any
    _arrange: Any

    def _build_tidy_pill(self) -> None:
        self._held_ids: set = set()
        self._tidy_pill = ui.button(on_click=lambda: self._bg(self.refresh())).props(
            'no-caps unelevated rounded'
        ).classes('wiz-tidy-pill')
        self._tidy_pill.set_visibility(False)
        self._tidy_event = f'wiz_board_tidy_{self._tidy_pill.id}'
        ui.on(self._tidy_event, lambda _: self._bg(self.refresh()))

    def _hold(self, match_id) -> None:
        """Count ``match_id`` as a change waiting for the next tidy."""
        self._held_ids.add(match_id)
        n = len(self._held_ids)
        self._tidy_pill.set_text(f"{n} {'change' if n == 1 else 'changes'} held · Tidy up")
        if not self._tidy_pill.visible:
            self._tidy_pill.set_visibility(True)
            self._tidy_pill.client.run_javascript(
                _WATCH_JS.replace('__NAME__', json.dumps(self._tidy_event))
                .replace('__IDLE__', str(IDLE_TIDY_MS))
            )

    def _release_hold(self) -> None:
        """Called by ``refresh``: every row is in its place again."""
        if not self._held_ids:
            return
        self._held_ids.clear()
        self._tidy_pill.set_visibility(False)
        self._tidy_pill.client.run_javascript(_UNWATCH_JS.replace('__NAME__', json.dumps(self._tidy_event)))

    def _hold_removed(self, idx: int) -> None:
        """Fade the row at ``idx`` in place instead of dropping it."""
        row = self.table.rows[idx]
        if not row.get('_removed'):
            row['_removed'] = True
            row['_held_note'] = 'Removed'
            self._hold(row.get('id'))

    def _place_in_hold(self, idx: int, fresh: dict) -> None:
        """Put ``fresh`` at ``idx`` without moving it, noting where it now belongs.

        Arranges a copy of the board with the new row in it to learn its group
        and whether anything's order would change; the row keeps its old group
        fields, so it stays under the header it was drawn under.
        """
        old = self.table.rows[idx]
        board = [fresh if i == idx else dict(r) for i, r in enumerate(self.table.rows)
                 if i == idx or not r.get('_removed')]
        arranged = self._arrange(board)
        group = fresh.get('_group')
        for key in _PLACE_FIELDS:
            fresh[key] = old.get(key)
        fresh['_held_note'] = f'Now {group}' if group and group != old.get('_group') else None
        live = [r.get('id') for r in self.table.rows if not r.get('_removed')]
        if [r.get('id') for r in arranged] != live:
            self._hold(fresh.get('id'))
        self.table.rows[idx] = fresh

    def _visible_rows(self) -> list:
        return [r for r in self.table.rows if not r.get('_removed')]
