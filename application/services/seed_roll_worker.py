"""Background poller for seed rolls that live on a :class:`ProviderTask`.

The only thing that advances a task-queue roll. A request submits and returns;
this loop checks every unfinished task, completes the ones that landed, and
retires the ones that never will.

Because it reads its work from the table rather than from memory, restarting
mid-roll costs nothing but the seconds until the next tick — the resumed poll is
the same code as the first one, not a recovery path that only runs after a
crash and therefore only breaks after a crash.
"""

import logging

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 5


async def _tick() -> None:
    from application.services.provider_task_service import ProviderTaskService
    from application.services.seed_roll_service import SeedRollService

    task_service = ProviderTaskService()

    await task_service.sweep_stale()

    tasks = await task_service.due_for_poll()
    if not tasks:
        return

    seed_roll_service = SeedRollService()

    async def _poll(task) -> None:
        if task.operation != 'generate_seed':
            logger.warning(
                'Provider task %s has no handler for operation %r',
                task.id, task.operation,
            )
            return
        await seed_roll_service.poll(task)

    # The scan is cross-tenant; each task's work re-enters its own community so
    # every scoped read and write inside it resolves the right tenant.
    await for_each_tenant_scoped(
        tasks, _poll,
        tenant_id_of=lambda task: task.tenant_id,  # type: ignore[attr-defined]
        event_id_of=lambda task: task.match.event_id if task.match_id else None,  # type: ignore[attr-defined, union-attr]
        logger=logger,
        describe=lambda task: f'provider task {task.id} ({task.provider})',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'seed roll polling', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
