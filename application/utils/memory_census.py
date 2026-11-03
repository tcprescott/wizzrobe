"""Count what is alive in the process, to find what a leak is holding on to.

``runtime_stats`` says the heap is growing (RSS, longer gen-2 passes); this
says what it is growing with. It walks every object the collector tracks, so a
call costs about as long as a full collection (a second or two on a large
heap); the loop watchdog records that stall under its own name but doesn't
alert on it. Run it on demand, never on a timer.

The NiceGUI figures are the usual suspects: a closed tab's page should leave
no ``Client`` and no deleted element behind once a collection has run, so
``clients_alive`` above ``clients_registered``, or a growing
``elements_deleted``, means something still holds a reference to a page that
is gone.
"""

from __future__ import annotations

import gc
import types
from collections import Counter
from typing import Any, Dict, List


def _type_name(obj: object) -> str:
    cls = type(obj)
    return f'{cls.__module__}.{cls.__qualname__}'


def _label(obj: object) -> str:
    if isinstance(obj, types.FunctionType):
        return f'function {obj.__module__}.{obj.__qualname__}'
    if isinstance(obj, types.MethodType):
        return f'bound method {_type_name(obj.__self__)}.{obj.__func__.__name__}'
    if isinstance(obj, types.CellType):
        return 'closure cell'
    if isinstance(obj, dict) and '__name__' in obj and '__loader__' in obj:
        return f'globals of {obj["__name__"]}'
    return _type_name(obj)


def retained_by(targets: List[object], depth: int = 6, top: int = 10) -> List[Dict[str, Any]]:
    """What holds ``targets`` alive, as referrer chains counted by shape.

    Walks back from each target one referrer at a time, ``depth`` steps,
    keeping the first referrer found for each object. One heap walk per step
    for all targets together, so the cost is ``depth`` walks however many
    targets there are. Frames are skipped (a frame is this function's own
    locals, or a coroutine that will let go when it ends), which also keeps the
    walk from wandering through its own bookkeeping. A chain stops at a
    module's globals: that is the root holding it.
    """
    if not targets:
        return []
    chain: Dict[int, str] = {}
    weight: Dict[int, int] = {}
    for t in targets:
        chain[id(t)] = _label(t)
        weight[id(t)] = weight.get(id(t), 0) + 1
    current = list({id(t): t for t in targets}.values())
    seen = set(chain)
    counts: Counter[str] = Counter()
    for _ in range(depth):
        by_id = {id(o): o for o in current}
        # An exact tuple, so f(*args) passes this same object and it can be told apart and skipped.
        args = tuple(current)
        own = {id(current), id(args), id(by_id), id(chain), id(weight), id(seen), id(targets), id(counts)}
        extended: set[int] = set()
        nxt: Dict[int, object] = {}
        for ref in gc.get_referrers(*args):
            if id(ref) in own or id(ref) in seen or isinstance(ref, types.FrameType):
                continue
            for child in gc.get_referents(ref):
                cid = id(child)
                if cid not in by_id or cid in extended:
                    continue
                extended.add(cid)
                if id(ref) not in nxt:
                    nxt[id(ref)] = ref
                    chain[id(ref)] = f'{chain[cid]} <- {_label(ref)}'
                    weight[id(ref)] = 0
                weight[id(ref)] += weight[cid]
        for cid in by_id:
            if cid not in extended:
                counts[chain[cid]] += weight[cid]
        seen.update(nxt)
        current = []
        for oid, obj in nxt.items():
            if _label(obj).startswith('globals of '):
                counts[chain[oid]] += weight[oid]
            else:
                current.append(obj)
        del by_id, args, nxt
        if not current:
            break
    for obj in current:
        counts[chain[id(obj)]] += weight[id(obj)]
    return [{'chain': c, 'count': n} for c, n in counts.most_common(top)]


def take(top: int = 40, collect: bool = True, explain: int = 50) -> Dict[str, Any]:
    """Count the heap. ``collect`` runs a full collection first, so what is counted
    is what is truly reachable rather than garbage the collector hasn't reached
    yet; ``explain`` is how many leaked pages and deleted elements to trace back
    to whatever holds them."""
    from application.utils.loop_watchdog import watchdog

    with watchdog.expected_block('memory census'):
        return _take(top, collect, explain)


def _take(top: int, collect: bool, explain: int) -> Dict[str, Any]:
    from nicegui import Client
    from nicegui.element import Element

    from application.events import check_in_live, match_live

    collected = gc.collect() if collect else None
    registered = {id(c) for c in Client.instances.values()}
    counts: Counter[str] = Counter()
    clients_alive = 0
    elements_alive = 0
    elements_deleted = 0
    stale_clients: List[object] = []
    dead_elements: List[object] = []
    for obj in gc.get_objects():
        counts[_type_name(obj)] += 1
        if isinstance(obj, Element):
            elements_alive += 1
            if obj.is_deleted:
                elements_deleted += 1
                if len(dead_elements) < explain:
                    dead_elements.append(obj)
        elif isinstance(obj, Client):
            clients_alive += 1
            if id(obj) not in registered and len(stale_clients) < explain:
                stale_clients.append(obj)
    try:
        stale_clients_held_by = retained_by(stale_clients)
        deleted_elements_held_by = retained_by(dead_elements)
    finally:
        del stale_clients, dead_elements
    return {
        'collected_first': collected,
        'stale_clients_held_by': stale_clients_held_by,
        'deleted_elements_held_by': deleted_elements_held_by,
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


def dump_on_signal(path: str) -> None:
    """Write a census to ``path`` (JSON) on every SIGUSR1, for the CI leak check.

    The process id goes to ``path + '.pid'`` so the check can signal the app
    worker rather than its launcher. Off unless ``MEMORY_CENSUS_DUMP`` is set:
    never in production, where the MCP tool is the way in.
    """
    import asyncio
    import json
    import os
    import signal

    def _dump() -> None:
        census = take(top=20, explain=30)
        tmp = f'{path}.tmp'
        with open(tmp, 'w') as f:
            json.dump(census, f)
        os.replace(tmp, path)

    asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, _dump)
    with open(f'{path}.pid', 'w') as f:
        f.write(str(os.getpid()))
