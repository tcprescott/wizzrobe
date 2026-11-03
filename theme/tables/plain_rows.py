"""Hand a ``ui.table`` its rows without NiceGUI wrapping each one.

``table.rows = rows`` stores the list in the element's ``Props``, an
``ObservableDict`` that recursively copies every nested dict and list into an
observable twin so an in-place edit can push itself to the browser. A match row
carries players, crew, seed and bracket sub-dicts, so at an event's size (1,300
matches) that copy cost ~0.5s of event-loop time per board refresh — every
viewer's tab stalled while one board loaded — and it bought nothing: every
in-place edit on these boards already calls ``table.update()`` itself.

So the big boards store their rows as the plain list the service built. The one
rule that comes with it: **an in-place edit to a row must be followed by
``table.update()``**, which is how these views have always been written.
"""

from typing import Any, Dict, List

from nicegui import ui


def set_plain_rows(table: ui.table, rows: List[Dict[str, Any]]) -> None:
    """Replace ``table``'s rows with ``rows`` as-is and push them to the browser."""
    # dict.__setitem__ skips ObservableDict.__setitem__, which is the deep copy.
    dict.__setitem__(table.props, 'rows', rows)
    table.update()
