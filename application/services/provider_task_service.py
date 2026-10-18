"""Lifecycle of :class:`ProviderTask` — long-running upstream provider calls.

Owns the row and its state transitions; knows nothing about what any particular
task *is*. What to do when one finishes belongs to the module that queued it
(today ``seed_roll_service`` for ``operation='generate_seed'``), which the
worker dispatches to.

The split matters because the two halves have different reasons to change: how a
task is claimed, timed out, and swept is queue mechanics, while what a finished
task means is domain work with its own audit trail and notifications.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from application.repositories import ProviderTaskRepository
from models import ProviderTask, ProviderTaskStatus, User

logger = logging.getLogger(__name__)

# How long an unfinished task may live before the sweeper gives up on it. Above
# the DK64 generation budget (10 minutes) with room for a restart in the middle:
# the point is to release a row nobody will ever collect, not to race the
# provider.
TASK_MAX_AGE = timedelta(minutes=20)
# A task that never got the provider's handle is a submit that died with the
# process (a restart mid-roll). Nothing will ever poll it, and while it stays
# active it blocks re-rolling the match, so it retires much sooner.
HANDLELESS_MAX_AGE = timedelta(minutes=5)


class ProviderTaskService:
    """Create, claim and retire provider tasks."""

    def __init__(self) -> None:
        self.repository = ProviderTaskRepository()

    async def queue(
        self,
        *,
        provider: str,
        operation: str,
        actor: Optional[User] = None,
        match_id: Optional[int] = None,
        preset_id: Optional[int] = None,
    ) -> ProviderTask:
        """Record a task before it is submitted.

        The row is written **first**, so a crash between here and the provider
        accepting the work leaves evidence rather than silence. Such a row sits
        at ``QUEUED`` with no ``provider_task_id`` and is retired by
        :meth:`sweep_stale`.
        """
        return await self.repository.create_queued(
            provider=provider,
            operation=operation,
            requested_by_id=actor.id if actor is not None else None,
            match_id=match_id,
            preset_id=preset_id,
        )

    async def mark_submitted(
        self, task: ProviderTask, *,
        provider_task_id: str, provider_params: dict, settings: dict,
    ) -> ProviderTask:
        """Attach the upstream handle and hand the task to the poller."""
        task.provider_task_id = provider_task_id
        task.provider_params = provider_params
        task.settings_snapshot = settings
        task.status = ProviderTaskStatus.RUNNING
        await task.save()
        return task

    async def claim(self, task: ProviderTask, status: ProviderTaskStatus) -> bool:
        """Transition a task, once. ``False`` means someone else got there first.

        Every terminal transition goes through here, because completing a seed
        roll twice would write two ``GeneratedSeeds`` rows and DM both players
        twice. Cheaper than a lock and correct across processes.
        """
        return await self.repository.claim(task, status)

    async def fail(self, task: ProviderTask, error: str) -> bool:
        """Record why a task will never finish. No retry: see the module docs."""
        task.error = error
        claimed = await self.claim(task, ProviderTaskStatus.FAILED)
        if claimed:
            await task.save(update_fields=['error'])
        return claimed

    async def get_by_id(self, task_id: int) -> Optional[ProviderTask]:
        """One task with its relations, scoped to the caller's tenant.

        The read every entry surface uses: a task belonging to another community
        comes back ``None`` and becomes a 404, never a cross-tenant peek.
        """
        return await self.repository.get_with_relations(task_id)

    async def due_for_poll(self, limit: int = 50) -> List[ProviderTask]:
        """The whole unfinished queue, across tenants. Worker-facing."""
        return await self.repository.due_for_poll(limit=limit)

    async def sweep_stale(self) -> int:
        """Abandon tasks that outlived their budget; return how many.

        ``ABANDONED`` rather than ``FAILED`` on purpose: the provider may well
        have finished the work, we simply stopped waiting for it. Conflating the
        two would tell an admin the roll broke when it did not.
        """
        abandoned = 0
        cutoff = datetime.now(timezone.utc) - TASK_MAX_AGE
        for task in await self.repository.stale(HANDLELESS_MAX_AGE):
            if task.provider_task_id is not None and task.created_at >= cutoff:
                continue
            task.error = task.error or 'Timed out waiting for the provider.'
            if await self.claim(task, ProviderTaskStatus.ABANDONED):
                await task.save(update_fields=['error'])
                abandoned += 1
                logger.error(
                    'Abandoned provider task %s (%s/%s) after %s',
                    task.id, task.provider, task.operation,
                    datetime.now(timezone.utc) - task.created_at,
                )
        return abandoned
