"""``steady_refreshable``: an async refreshable that never shows an empty frame.

NiceGUI's ``ui.refreshable`` clears its container and *then* runs the builder.
A synchronous builder refills it in the same tick, so the browser never sees the
gap. An async one awaits (a query, nearly always) between the clear and the
refill, and that await flushes the empty container to the browser: the section
collapses, everything under it jumps up, and jumps back down when the query
returns. On the Match Schedule board it was the Needs you list, and the board
beside it snapped to full width and back on every confirm (PR #482).

This variant builds the new content off-screen and swaps it in, old out and new
in, in one step once the builder has finished. The section holds its old
content (and its height) until then. Overlapping refreshes keep only the newest.
A synchronous builder behaves exactly as ``ui.refreshable``.

Use it for every async refreshable in ``pages/`` and ``theme/``;
``check_steady_refresh`` blocks a new async ``@ui.refreshable``.
docs/style-guide.md#hold-still has the rule.
"""

import inspect
from collections.abc import Awaitable
from typing import Any

from nicegui import ui
from nicegui.functions.refreshable import RefreshableContainer, RefreshableTarget, refreshable

__all__ = ['steady_refreshable']


class steady_refreshable(refreshable):
    """Drop-in for ``@ui.refreshable`` whose async refresh swaps content in one step."""

    def __init__(self, func: Any) -> None:
        super().__init__(func)
        # While a refresh builds off-screen, target.container is the staging container.
        self._visible: dict[int, RefreshableContainer] = {}

    def prune(self) -> None:
        super().prune()
        live = {id(t) for t in self.targets}
        self._visible = {k: v for k, v in self._visible.items() if k in live}

    def _execute_refresh(
        self, args: tuple[Any, ...], kwargs: dict[str, Any], *, instance: Any,
    ) -> list[Awaitable[Any]]:
        if not inspect.iscoroutinefunction(self.func):
            return super()._execute_refresh(args, kwargs, instance=instance)
        awaitables: list[Awaitable[Any]] = []
        for target in self.targets:
            if target.instance != instance:
                continue
            target.args = args or target.args
            target.kwargs.update(kwargs)
            awaitables.append(self._rebuild(target))
        return awaitables

    async def _rebuild(self, target: RefreshableTarget) -> None:
        visible = self._visible.setdefault(id(target), target.container)
        if visible.is_deleted:
            return
        # Off-screen under the page root so the parent's :empty and sibling selectors never see the staging element.
        with visible.client.layout:
            holder = ui.element('div').style('display: none')
        with holder:
            staging = RefreshableContainer()
        target.container = staging
        try:
            await target.run(self.func)
        finally:
            self._swap(target, visible, holder, staging)

    def _swap(self, target: RefreshableTarget, visible: RefreshableContainer,
              holder: ui.element, staging: RefreshableContainer) -> None:
        if target.container is not staging or visible.is_deleted or staging.is_deleted:
            # A newer refresh owns the target now, or the page went away.
            if not holder.is_deleted:
                holder.delete()
            return
        slot = visible.parent_slot
        assert slot is not None
        staging.move(slot.parent, slot.children.index(visible), target_slot=slot.name)
        visible.delete()
        holder.delete()
        self._visible[id(target)] = staging
