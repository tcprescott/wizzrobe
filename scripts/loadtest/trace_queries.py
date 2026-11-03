"""Attribute every SQL statement a page render issues to the app code that issued it.

``page_timings.js`` says *which* page runs too many queries; this says *where*
they come from. It renders one page in-process (``ASGITransport``, the real
lifespan), wraps the database client so each statement records the innermost
frames of app code that led to it, and prints the call sites ranked by count.
An N+1 shows up as one call site with a count close to the row count.

    COOKIE_FILE=cookie.txt PYTHONPATH=. poetry run python scripts/loadtest/trace_queries.py /t/default/home/schedule
    PYTHONPATH=. poetry run python scripts/loadtest/trace_queries.py worker:volunteer_reminder

The cookie comes from a mock login (see README.md). Only the initial render is
traced: content a lazy tab builds over the websocket after load is not.
"""

import asyncio
import os
import sys
import traceback
from collections import Counter

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
METHODS = ('execute_query', 'execute_query_dict', 'execute_insert', 'execute_many')


def _site() -> str:
    frames = [f for f in traceback.extract_stack()[:-2]
              if f.filename.startswith(ROOT) and '/scripts/loadtest/' not in f.filename and '.venv' not in f.filename]
    return ' <- '.join(f'{os.path.relpath(f.filename, ROOT)}:{f.lineno}' for f in reversed(frames[-6:]))


def _trace(sites: Counter) -> None:
    from tortoise import connections

    client = connections.get('default')
    for name in METHODS:
        original = getattr(client, name)

        def wrap(orig):
            async def traced(sql, *a, **kw):
                sites[(sql.split(' FROM ')[-1][:60] if 'FROM' in sql else sql[:60], _site())] += 1
                return await orig(sql, *a, **kw)
            return traced
        client.__dict__[name] = wrap(original)


async def trace_worker(worker: str) -> None:
    """One tick of a background worker (names as in measure_backend.WORKERS)."""
    import importlib

    from dotenv import load_dotenv
    from tortoise import Tortoise

    from scripts.loadtest.measure_backend import WORKERS
    load_dotenv()
    from migrations.tortoise_config import TORTOISE_ORM

    await Tortoise.init(config=TORTOISE_ORM)
    sites: Counter = Counter()
    try:
        _trace(sites)
        await importlib.import_module(WORKERS[worker])._tick()
    finally:
        await Tortoise.close_connections()
    _report(f'worker {worker}', sites)


def _report(label: str, sites: Counter) -> None:
    print(f'{label}: {sum(sites.values())} statements')
    for (sql, site), n in sites.most_common(25):
        print(f'{n:6d}  {sql}\n        {site}')


async def main(path: str) -> None:
    if path.startswith('worker:'):
        await trace_worker(path.split(':', 1)[1])
        return

    import main as appmod

    with open(os.environ.get('COOKIE_FILE', 'cookie.txt')) as fh:  # noqa: ASYNC230 - before the app starts
        cookie = fh.read().strip()
    app = appmod.app
    sites: Counter = Counter()
    async with app.router.lifespan_context(app):
        _trace(sites)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', timeout=120) as c:
            await c.get(path, headers={'Cookie': cookie})
            sites.clear()
            r = await c.get(path, headers={'Cookie': cookie})
    _report(f'{path}: HTTP {r.status_code}', sites)


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else '/t/default/'))
