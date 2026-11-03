"""Let a deleted NiceGUI element be freed the moment it is deleted.

Every element's ``Classes``, ``Style`` and ``Props`` collection registers its
own bound ``_update`` as its change handler, so each one is a reference cycle
(collection -> handler list -> bound method -> collection). The element itself
goes by reference counting, but those three cycles stay behind until a full
garbage collection walks the whole heap to find them: about 150,000 objects per
pass in production on 2026-10-22, with every pass a 1-1.9s stop of the one event
loop. Clearing the handlers once an element is removed breaks the cycles, so the
collections are freed with it. A removed element never renders again, so there
is nothing left for the handlers to update.

This is also what lets :func:`gc_monitor.refreeze_after_full_passes` freeze the
live page trees: without it each later-deleted element would leave three cycles
in the frozen set, where no pass looks.
"""

from __future__ import annotations

from typing import Any, Iterable

from nicegui.client import Client

_original = Client.remove_elements
_COLLECTIONS = ('_classes', '_style', '_props')


def release(element: Any) -> None:
    for name in _COLLECTIONS:
        handlers = getattr(getattr(element, name, None), '_change_handlers', None)
        if isinstance(handlers, list):
            handlers.clear()


def _remove_elements(self: Client, elements: Iterable[Any]) -> None:
    element_list = list(elements)
    _original(self, element_list)
    for element in element_list:
        release(element)


def install() -> None:
    Client.remove_elements = _remove_elements  # type: ignore[method-assign]


def uninstall() -> None:
    Client.remove_elements = _original  # type: ignore[method-assign]
