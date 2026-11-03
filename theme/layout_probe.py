"""A test-only hook that re-renders every live section of a page in place.

``scripts/ui_layout_shift.js`` checks that a page holds still when it updates.
Most updates arrive from somewhere the browser cannot reach (another person's
action, a worker, the reply to the viewer's own click), so the page needs a way
to be told "redraw everything you would redraw on an update" without anything
actually changing. With identical data the redraw must not move a single pixel:
any shift the scan measures is a section that went blank, or changed height,
while it rebuilt.

Only installed when ``WIZ_LAYOUT_PROBE`` is set (the CI ``browser`` job and a
local ``/ui-validation`` run), so production pages carry no handler.
"""

import gc
import logging
import weakref

from nicegui import Client, app, background_tasks, ui
from nicegui.functions.refreshable import refreshable

from application.utils.environment import env_flag

logger = logging.getLogger(__name__)

PROBE_EVENT = 'wiz_layout_probe'

_skipped: 'weakref.WeakSet[refreshable]' = weakref.WeakSet()


def skip_layout_probe(section: refreshable) -> None:
    """Leave ``section`` out of the redraw; the sections inside it are still redrawn.

    For a section that only refreshes to show something else on the viewer's
    say-so (a view switcher), where a redraw rebuilds whole boards that then
    load their rows, which no live update ever does.
    """
    _skipped.add(section)


_attached: 'weakref.WeakSet[Client]' = weakref.WeakSet()


def install_layout_probe() -> None:
    """Give every page the redraw handler, once per client (app start-up)."""
    if env_flag('WIZ_LAYOUT_PROBE'):
        app.on_connect(_attach)


def _attach(client: Client) -> None:
    if client in _attached:
        return  # on_connect fires again on every reconnect
    _attached.add(client)
    client.layout.on(PROBE_EVENT, lambda _: background_tasks.create(_refresh_all(client)))


async def _refresh_all(client) -> None:
    targets = []
    for obj in gc.get_objects():
        if isinstance(obj, refreshable) and obj not in _skipped:
            obj.prune()
            for target in obj.targets:
                if target.container.client is client:
                    targets.append((obj, target))
    containers = {id(t.container) for _, t in targets}
    # Only the outermost: refreshing an inner one too races the parent deleting it.
    outermost = [
        (r, t) for r, t in targets
        if not any(id(a) in containers for a in t.container.ancestors())
    ]
    awaitables = []
    seen = set()
    with client:
        for r, target in outermost:
            # _execute_refresh redraws every target bound to this instance.
            if (id(r), id(target.instance)) in seen:
                continue
            seen.add((id(r), id(target.instance)))
            logger.info('layout probe: redrawing %s', r.func.__qualname__)
            try:
                awaitables += r._execute_refresh((), {}, instance=target.instance)  # pylint: disable=protected-access
            except Exception:
                logger.exception('layout probe: refreshing %s failed', r.func.__qualname__)
    for awaitable in awaitables:
        try:
            await awaitable
        except Exception:
            logger.exception('layout probe: an async refresh failed')
    with client:
        ui.run_javascript('window.__wizProbeDone = (window.__wizProbeDone || 0) + 1')
