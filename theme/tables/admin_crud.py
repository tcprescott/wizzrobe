"""Shared building blocks for admin CRUD tabs.

Presentation-only: NiceGUI widgets and a thin actor lookup. No business logic or
ORM writes. Inline dialogs use :func:`theme.dialog._helpers.form_dialog` for the
standard chrome.
"""

import inspect
from typing import Any, Awaitable, Callable, Optional

from nicegui import app, background_tasks, ui

from application.event_context import event_scope, get_current_event_id
from application.services import get_user_from_session
from application.table_preferences_context import current_table_prefs, table_prefs_scope
from application.tenant_context import get_current_tenant_id, tenant_scope
from application.timezone_context import current_timezone_name, hour_cycle_scope, tz_scope, uses_12_hour
from models import User

RenderContext = tuple[Optional[int], str, Optional[dict], bool, Optional[int]]


def capture_render_context() -> RenderContext:
    """Snapshot the ambient tenant, display zone, table layouts, 12/24-hour clock and event.

    Call at build time, use with :func:`scoped_background`. A detached task has
    neither the contextvars nor the client stash — NiceGUI keys its slot stack by
    asyncio task id, so ``app.storage.client`` raises inside one — and all five
    values must therefore be carried in by hand. The event goes last so the
    positional reads of the first four stay put.
    """
    return (
        get_current_tenant_id(), current_timezone_name(), current_table_prefs(), uses_12_hour(),
        get_current_event_id(),
    )


def scoped_background(context: RenderContext, coro) -> None:
    """Run ``coro`` as a background task with a captured context rebound.

    The tenant alone is not enough: without the zone the task repaints the same
    rows on the fallback clock, so a table silently changes timezone the moment
    you paginate or switch tabs — and without the table preferences it repaints
    them in the *shipped* columns, so a customized board reverts to defaults on
    the first refresh.
    """
    tenant_id, tz, prefs, hour12, event_id = context

    async def _run():
        with tenant_scope(tenant_id), event_scope(event_id), tz_scope(tz), hour_cycle_scope(hour12), \
                table_prefs_scope(prefs):
            await coro

    background_tasks.create(_run())


def refresh_button(
    refresh: Callable[[], Any],
    *,
    tooltip: str = 'Refresh table',
    icon: str = 'refresh',
) -> ui.button:
    """The toolbar **Refresh** control every admin tab wants, wired correctly.

    Hand-rolling it as ``on_click=lambda: background_tasks.create(reload())``
    reads fine and does nothing: a task spawned from a *click* has neither the
    tenant contextvar nor the client stash the fallback reads, so the first
    scoped query inside raises. NiceGUI logs that and swallows it, so the button
    fails in silence — the audit found eleven of the fifteen admin refresh
    controls dead this way, and their server-side errors were worse than the
    silence (a staff member's click logged *"Only Staff can view webhooks"*, and
    a community with SpeedGaming enabled logged *"not enabled for this
    community"*, both because the actor and the flags resolve against no
    tenant).

    So the context is captured **here**, during the page build where it is still
    live, and rebound around the call — the same shape as :func:`wire_tab_refresh`.

    ``refresh`` is a *callable*, not a coroutine, and is invoked inside the task
    (pass ``lambda: view.refresh()`` where the toolbar is built before the thing
    it refreshes exists — several tabs are laid out that way).
    That is what makes this work for a ``@ui.refreshable``'s ``.refresh()`` too:
    it returns an ``AwaitableResponse`` that must be awaited immediately or not
    at all, so building it a task early — the second failure the audit found, on
    the Equipment and Feedback tabs — raises before it refreshes anything.
    """
    context = capture_render_context()

    async def _call() -> None:
        result = refresh()
        if inspect.isawaitable(result):
            await result

    return ui.button(
        icon=icon, on_click=lambda: scoped_background(context, _call()),
    ).props('flat color=primary').tooltip(tooltip)


__all__ = [
    'capture_render_context',
    'current_actor',
    'refresh_button',
    'scoped_background',
    'wire_tab_refresh',
]


async def current_actor() -> Optional[User]:
    """The logged-in ``User`` (or ``None``)."""
    return await get_user_from_session(app.storage.user)


def wire_tab_refresh(tab_name: str, refresh: Callable[..., Awaitable[None]]) -> None:
    """Refresh when the parent admin page emits ``selected_tab`` for ``tab_name``.

    ``tab_name`` must match the tab label registered in ``pages/admin.py``.

    The render context is captured **here** (this runs during the page build,
    where the request context is live) and rebound around the refresh:
    ``selected_tab`` is a client event, so its handler spawns a detached
    background task that has lost the tenant contextvar *and* the client stash —
    a scoped read inside ``refresh`` would otherwise raise ``require_tenant_id()``
    (swallowed) and never repaint, and every time it renders would silently fall
    back to the default zone.
    """
    context = capture_render_context()

    def _fire() -> None:
        scoped_background(context, refresh())

    ui.on('selected_tab', lambda e: _fire() if e.args == tab_name else None)
