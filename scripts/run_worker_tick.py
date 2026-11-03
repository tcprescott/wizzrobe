"""Run one background worker's tick, once, against the dev database.

Every periodic worker is a module-level ``_tick`` coroutine plus a
``run_worker_loop(_tick, TICK_SECONDS, …)`` registration, and the loop's only
job is scheduling. So a tick can be awaited directly — no worker changes, no
waiting out an interval that runs from 60 to 300 seconds.

That wait is why these are the least-exercised code in the app: five of the
eight have no test naming them, and the loop swallows every failure into a
single ``"<name> tick failed"`` log line, so a broken tick looks exactly like an
idle one. The one worker that ticks fast enough to stumble into by hand — seed
roll polling, at 5s — produced three consecutive bug commits, each found by
driving the real app rather than by a test.

Usage::

    poetry run python scripts/run_worker_tick.py --list    # placement + Cloud Scheduler cron
    poetry run python scripts/run_worker_tick.py seed_roll
    poetry run python scripts/run_worker_tick.py volunteer_reminder --tenant default

An unhandled exception is re-raised here rather than swallowed: in production
the loop hides it, which is the behaviour this script exists to see through.
"""

import argparse
import asyncio
import importlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tortoise import Tortoise

from application.worker_registry import WORKERS as _SPECS
from application.worker_registry import cron_schedule

WORKERS = {key: spec.module for key, spec in _SPECS.items()}


async def run_tick(worker: str, tenant: str | None) -> None:
    from dotenv import load_dotenv
    load_dotenv()

    from migrations.tortoise_config import TORTOISE_ORM

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        module = importlib.import_module(WORKERS[worker])
        tick = getattr(module, '_tick', None)
        if tick is None:
            raise SystemExit(
                f"{WORKERS[worker]} has no module-level _tick; the worker shape "
                f"changed and this runner needs updating."
            )
        if tenant:
            from application.tenant_context import tenant_scope
            from models import Tenant

            row = await Tenant.get_or_none(slug=tenant)
            if row is None:
                raise SystemExit(f"No tenant with slug {tenant!r}.")
            with tenant_scope(row.id):
                await tick()
        else:
            await tick()
    finally:
        await Tortoise.close_connections()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('worker', nargs='?', choices=sorted(WORKERS), help='which worker to tick')
    parser.add_argument('--tenant', help='pin the tick to one tenant slug')
    parser.add_argument('--list', action='store_true', help='list the workers and exit')
    parser.add_argument('-v', '--verbose', action='store_true', help='DEBUG logging')
    args = parser.parse_args()

    if args.list or not args.worker:
        for name, spec in sorted(_SPECS.items()):
            schedule = cron_schedule(spec.loop().interval) if spec.placement == 'scheduled' else '-'
            print(f"{name:26} {spec.placement:12} {schedule:16} {spec.module}")
        return

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)-8s %(name)s: %(message)s',
        stream=sys.stdout,
    )
    asyncio.run(run_tick(args.worker, args.tenant))
    print(f"\n{args.worker} tick completed without raising.")


if __name__ == '__main__':
    main()
