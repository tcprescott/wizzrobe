"""Count what is alive in the process, to find what a leak is holding on to.

``runtime_stats`` says the heap is growing (RSS, longer gen-2 passes); this
says what it is growing with. It walks every object the collector tracks, so a
call costs about as long as a full collection (a second or two on a large
heap) and the loop watchdog will report it as a stall. Run it on demand, never
on a timer.

The NiceGUI figures are the usual suspects: a closed tab's page should leave
no ``Client`` and no deleted element behind once a collection has run, so
``clients_alive`` above ``clients_registered``, or a growing
``elements_deleted``, means something still holds a reference to a page that
is gone.
"""

from __future__ import annotations

import gc
from collections import Counter
from typing import Any, Dict


def _type_name(obj: object) -> str:
    cls = type(obj)
    return f'{cls.__module__}.{cls.__qualname__}'


def take(top: int = 40) -> Dict[str, Any]:
    from nicegui import Client
    from nicegui.element import Element

    from application.events import check_in_live, match_live

    counts: Counter[str] = Counter()
    clients_alive = 0
    elements_alive = 0
    elements_deleted = 0
    for obj in gc.get_objects():
        counts[_type_name(obj)] += 1
        if isinstance(obj, Element):
            elements_alive += 1
            if obj.is_deleted:
                elements_deleted += 1
        elif isinstance(obj, Client):
            clients_alive += 1
    return {
        'tracked_objects': sum(counts.values()),
        'frozen_objects': gc.get_freeze_count(),
        'clients_registered': len(Client.instances),
        'clients_alive': clients_alive,
        'elements_alive': elements_alive,
        'elements_deleted': elements_deleted,
        'match_live_subscribers': len(match_live._subscribers),
        'check_in_live_subscribers': len(check_in_live._subscribers),
        'top_types': [{'type': name, 'count': n} for name, n in counts.most_common(top)],
    }
