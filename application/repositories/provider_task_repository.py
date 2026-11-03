"""Data access for :class:`ProviderTask` — long-running upstream provider calls."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from application.event_writes import guarded_update
from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import ProviderTask, ProviderTaskStatus

_ACTIVE = (ProviderTaskStatus.QUEUED, ProviderTaskStatus.RUNNING)

_FAILED = (ProviderTaskStatus.FAILED, ProviderTaskStatus.ABANDONED)


class ProviderTaskRepository(TenantScopedRepository[ProviderTask]):
    model = ProviderTask

    @classmethod
    async def get_with_relations(cls, task_id: int) -> Optional[ProviderTask]:
        """One task with everything a completion needs, scoped to this tenant."""
        return await scoped(
            ProviderTask.filter(id=task_id)
        ).prefetch_related('match__generated_seed', 'preset', 'requested_by').first()

    @classmethod
    async def active_for_match(cls, match_id: int) -> Optional[ProviderTask]:
        """The unfinished task for a match, if one is already in flight.

        What stops a second Generate click from queueing a second upstream
        generation. Each one consumes a real, publicly-numbered seed, so this is
        about not wasting them as much as it is about correctness.
        """
        return await scoped(
            ProviderTask.filter(match_id=match_id, status__in=_ACTIVE)
        ).order_by('-created_at').first()

    @classmethod
    async def latest_failure_for_match(cls, match_id: int) -> Optional[ProviderTask]:
        """The most recent roll for a match that produced nothing, if any."""
        return await scoped(
            ProviderTask.filter(match_id=match_id, status__in=_FAILED)
        ).order_by('-created_at').first()

    @classmethod
    async def board_state_for_matches(
        cls, match_ids: List[int],
    ) -> tuple[dict[int, ProviderTask], dict[int, ProviderTask]]:
        """``(rolling, failed)`` newest-per-match, in **one** query.

        The seed cell asks two questions of every row it draws — is this rolling,
        and did the last roll break — and a board renders many rows. Fetching
        both states together and partitioning here keeps that at one round trip
        rather than one per question (``tests/test_query_budget.py`` holds the
        line). Ascending order, so the last write per match wins: the newest.
        """
        if not match_ids:
            return {}, {}
        tasks = await scoped(
            ProviderTask.filter(
                match_id__in=match_ids, status__in=_ACTIVE + _FAILED,
            )
        ).order_by('created_at')
        rolling: dict[int, ProviderTask] = {}
        failed: dict[int, ProviderTask] = {}
        for task in tasks:
            bucket = rolling if task.status in _ACTIVE else failed
            bucket[task.match_id] = task
        return rolling, failed

    @classmethod
    async def create_queued(cls, **fields) -> ProviderTask:
        return await ProviderTask.create(
            tenant_id=current_tenant_id(),
            status=ProviderTaskStatus.QUEUED,
            **fields,
        )

    # Poller-facing queries — deliberately cross-tenant

    @classmethod
    async def pollable(cls, task_id: int) -> Optional[ProviderTask]:
        """One task if it is still pollable, across tenants, loaded like :meth:`due_for_poll`.

        Unscoped for the same reason: a Cloud Tasks call names the task and
        re-enters its tenant before acting on it.
        """
        return await ProviderTask.filter(
            id=task_id, status__in=_ACTIVE, provider_task_id__isnull=False,
        ).prefetch_related('match', 'preset', 'requested_by').first()

    @classmethod
    async def due_for_poll(cls, limit: int = 50) -> List[ProviderTask]:
        """Every **pollable** task across all tenants, oldest first.

        Deliberately unscoped: the worker runs outside any request and owns the
        whole queue, then re-enters each task's own ``tenant_scope`` to act on
        it. Oldest first so a backlog drains in the order people asked.

        A task with no ``provider_task_id`` is excluded rather than picked up and
        failed: that is the state of a submit still in flight, and the provider
        takes as long as it takes to answer. ``stale`` retires the ones that
        never get a handle, on a timer long enough to tell them apart.
        """
        return await ProviderTask.filter(
            status__in=_ACTIVE, provider_task_id__isnull=False,
        ).order_by('created_at').limit(limit).prefetch_related(
            'match', 'preset', 'requested_by'
        )

    @classmethod
    async def claim(cls, task: ProviderTask, status: ProviderTaskStatus) -> bool:
        """Move a task to a terminal (or running) state, once.

        The guard against double completion: the update is conditional on the
        row still holding the status we read, so a duplicate tick loses the race
        and returns ``False`` instead of writing a second ``GeneratedSeeds`` and
        sending a second round of DMs. Unscoped by the same reasoning as
        :meth:`due_for_poll` — the caller is the worker, holding a task it just
        read from the global queue.
        """
        completed: Optional[datetime] = (
            datetime.now(timezone.utc) if status in ProviderTaskStatus.terminal() else None
        )
        updated = await guarded_update(ProviderTask.filter(
            id=task.id, status=task.status
        ), status=status, completed_at=completed)
        if updated:
            task.status = status
            task.completed_at = completed  # type: ignore[assignment]
        return bool(updated)

    @classmethod
    async def stale(cls, older_than: timedelta) -> List[ProviderTask]:
        """Unfinished tasks that have outlived their budget.

        Cross-tenant for the same reason as :meth:`due_for_poll`: the worker
        sweeps the whole queue, then acts on each task inside its own tenant.
        """
        cutoff = datetime.now(timezone.utc) - older_than
        return await ProviderTask.filter(
            status__in=_ACTIVE, created_at__lt=cutoff
        ).order_by('created_at')
