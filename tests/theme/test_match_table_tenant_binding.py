"""A table event handler that touches tenant-scoped data must keep its context.

``MatchTableView`` captures the request's tenant *and display zone* at
construction and rebinds both in ``_bg``. A handler scheduled with a bare
``background_tasks.create`` instead loses them: it dies on
``require_tenant_id()`` — silently, because the exception surfaces in a
background task rather than the UI — or, worse, survives and repaints every
timestamp on the fallback clock. ``assign_stations`` regressed the first way;
this pins both halves of the wiring.
"""

import inspect
import re

from theme.tables.admin_crud import scoped_background
from theme.tables.match import MatchTableView
from theme.tables.tournament import TournamentTableView
from theme.tables.user import UserTableView

# Events whose handlers reach tenant-scoped data and which are NOT given a
# client context to run inside. These must go through ``_bg``.
# The event name must be the one the template emits, verbatim.
# An entry naming an event that is never registered would make the check below
# pass vacuously, which is why test_listed_events_are_actually_registered exists.
TENANT_BOUND_EVENTS = [
    'roll', 'seat', 'start', 'finish', 'confirm', 'edit_result',
    'assign_stations', 'set_stage',
]


def _setup_source() -> str:
    return inspect.getsource(MatchTableView._setup_ui)


def _registrations() -> list[str]:
    """One flattened string per ``self.table.on(...)`` call.

    Registrations wrap across lines, so a line-by-line scan would miss the
    ``context.client`` argument on a continuation line.
    """
    flat = re.sub(r'\s+', ' ', _setup_source())
    parts = flat.split('self.table.on(')[1:]
    return ['self.table.on(' + p for p in parts]


def test_bg_delegates_to_the_shared_scoped_background():
    src = inspect.getsource(MatchTableView._bg)
    assert 'scoped_background(' in src


def test_scoped_background_rebinds_both_tenant_and_display_zone():
    """The tenant alone is not enough.

    A task that rebinds the tenant but not the zone repaints the same rows on
    the fallback clock, so a table silently changes timezone the moment you
    paginate or switch tabs — a wrong answer that looks like a working page.
    """
    src = inspect.getsource(scoped_background)
    assert 'tenant_scope(' in src
    assert 'tz_scope(' in src


def test_every_view_that_captures_a_tenant_also_captures_a_zone():
    """The two halves are captured together or the pairing drifts apart again."""
    for view in (MatchTableView, UserTableView, TournamentTableView):
        src = inspect.getsource(view.__init__)
        if 'get_current_tenant_id()' not in src:
            continue
        assert 'capture_render_context()' in src, (
            f"{view.__name__} captures a tenant for its background tasks but no "
            f"display zone; use capture_render_context() so both travel together."
        )


def test_listed_events_are_actually_registered():
    """Without this, a typo in the list above silently disables its own check.

    ``test_tenant_bound_events_are_scheduled_through_bg`` scans registration
    lines for each listed event; an event that is never registered has no line
    to offend, so it passes for the wrong reason. ``'edit_stage'`` sat in
    the list for exactly that reason — the emitted name was ``'edit-stage'``
    — until this check caught it.
    """
    src = _setup_source()
    missing = [e for e in TENANT_BOUND_EVENTS if f"self.table.on('{e}'" not in src]
    assert not missing, (
        f"listed as tenant-bound but never registered: {missing}. Either the "
        f"event was renamed and the list was not, or the registration was lost."
    )


def test_tenant_bound_events_are_scheduled_through_bg():
    src = _setup_source()
    offenders = []
    for event in TENANT_BOUND_EVENTS:
        for line in src.splitlines():
            if f"self.table.on('{event}'" not in line:
                continue
            if 'self._bg(' not in line:
                offenders.append(event)
    assert not offenders, (
        f"these table events are scheduled without rebinding the tenant: "
        f"{offenders}. Use self._bg(...) rather than background_tasks.create(...) "
        f"— otherwise the handler raises 'No tenant in context' in a background "
        f"task and the click appears to do nothing."
    )


def test_every_registered_event_is_either_bg_or_given_a_client():
    """Catch a *new* handler wired the broken way, not just the known list."""
    for reg in _registrations():
        m = re.search(r"self\.table\.on\('([a-z_-]+)'", reg)
        if not m or 'background_tasks.create' not in reg:
            continue
        assert 'context.client' in reg, (
            f"event '{m.group(1)}' uses a bare background task with no tenant "
            f"rebind and no client context; use self._bg(...)"
        )
