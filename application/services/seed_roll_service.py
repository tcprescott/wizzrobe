"""Drives a seed roll that lives on a :class:`ProviderTask`.

The domain half of the persisted roll: submit a task-queue backend's work,
check it later, and when it lands, complete whatever asked for it. Queue
mechanics (claiming, sweeping, timing out) belong to ``ProviderTaskService``;
this module knows what ``operation='generate_seed'`` means.

Nothing here retries. A failed roll is left terminal for a human to re-run,
because every attempt consumes a real, publicly-numbered seed upstream and a
silent retry loop spends them where nobody is watching.
"""

import asyncio
import logging
from typing import Optional

import aiohttp

from application.errors import MissingCredentialError
from application.events import EventType, match_live
from application.services.audit_service import AuditActions, AuditService
from application.services.provider_task_service import ProviderTaskService
from application.services.seedgen_service import SeedGenerationService
from application.utils.seed_provider import (
    SeedProviderRateLimited,
    SeedProviderTimeout,
    SeedProviderUnavailable,
)
from models import Match, Preset, ProviderTask, ProviderTaskStatus, User

logger = logging.getLogger(__name__)


class SeedRollService:
    """Submit, poll and complete seed rolls tracked by a ``ProviderTask``."""

    def __init__(self) -> None:
        self.seedgen_service = SeedGenerationService()
        self.task_service = ProviderTaskService()
        self.audit_service = AuditService()

    async def submit(self, task: ProviderTask, preset=None) -> ProviderTask:
        """Send a queued task's work upstream and record the handle it returns.

        An upstream failure is terminal and recorded on the task rather than
        raised past the caller: the row already exists, so the honest outcome is
        a ``FAILED`` task the admin can see, not an exception that leaves a
        ``QUEUED`` row nobody explains.

        A **misconfiguration** is different and is re-raised. A missing
        credential or an unknown branch happens before the provider is contacted
        at all, so there is no work to track — leaving a failed row behind would
        file a config mistake as a broken roll, and file another one every time
        the admin clicks Generate before fixing it.
        """
        try:
            submission = await self.seedgen_service.submit_async_roll(
                task.provider, preset,
            )
        except (MissingCredentialError, ValueError):
            await task.delete()
            raise
        except Exception as exc:
            logger.exception('Provider task %s failed to submit', task.id)
            await self.task_service.fail(task, str(exc))
            return task

        return await self.task_service.mark_submitted(
            task,
            provider_task_id=submission.provider_task_id,
            provider_params=submission.provider_params,
            settings=submission.settings,
        )

    async def poll(self, task: ProviderTask) -> ProviderTaskStatus:
        """Check one task and finish it if it has landed.

        Returns the status the task now holds, so a caller can log a sweep
        without re-reading the row.
        """
        if task.provider_task_id is None:
            # No upstream handle yet, so there is nothing to poll — but this is
            # **not** a failure. It is also the state of a task whose submit is
            # still in flight, and the submit takes as long as the provider takes
            # to answer. Failing here would let a tick that lands in that window
            # kill a live roll. ``sweep_stale`` retires the ones that really are
            # stuck, on a timer long enough to tell the two apart.
            return ProviderTaskStatus(task.status)

        try:
            result = await self.seedgen_service.poll_async_roll(
                task.provider,
                task.provider_task_id,
                task.provider_params,
                task.settings_snapshot,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, SeedProviderTimeout,
                SeedProviderUnavailable, SeedProviderRateLimited) as exc:
            # The provider blipped, not the roll: the seed may still be building
            # upstream. Poll again next tick; sweep_stale retires it if it never
            # comes back.
            logger.warning('Provider task %s (%s) poll failed, will retry: %s',
                           task.id, task.provider, exc or type(exc).__name__)
            return ProviderTaskStatus(task.status)
        except Exception as exc:
            logger.error(
                'Provider task %s (%s) failed: %s', task.id, task.provider, exc, exc_info=exc,
            )
            await self._fail(task, str(exc))
            return ProviderTaskStatus.FAILED

        if result.seed is None:
            return ProviderTaskStatus(task.status)

        # Claim before completing: a duplicate tick that loses this race must not
        # write a second GeneratedSeeds row or DM both players twice.
        if not await self.task_service.claim(task, ProviderTaskStatus.SUCCEEDED):
            return ProviderTaskStatus.SUCCEEDED

        await self._complete(task, result)
        return ProviderTaskStatus.SUCCEEDED

    async def _complete(self, task: ProviderTask, result) -> None:
        from application.services.match.match_schedule_service import MatchScheduleService

        match = await self._match_for(task)
        if match is None:
            logger.error(
                'Provider task %s finished with no match to give the seed to', task.id,
            )
            return

        await MatchScheduleService().complete_seed_roll(
            match,
            task.provider,
            await self._relation(Preset, task.preset_id),  # type: ignore[attr-defined]
            await self._relation(User, task.requested_by_id),  # type: ignore[attr-defined]
            seed_url=result.seed.url,
            settings=result.seed.settings,
            seed_code=result.seed.code,
            provider_meta={
                'provider': task.provider,
                'operation': task.operation,
                'surface': 'match',
                'provider_task_id': task.provider_task_id,
                'verification_hash': result.verification_hash,
            },
        )

    @staticmethod
    async def _relation(model, obj_id):
        """Resolve a nullable FK by id.

        Not ``task.preset``: Tortoise hands back a ``_NoneAwaitable`` for an
        unset relation, which is neither the object nor ``None`` and so slips
        past an ``is not None`` check to fail deep inside a later write.
        """
        return await model.get_or_none(id=obj_id) if obj_id else None

    async def _fail(self, task: ProviderTask, error: str) -> None:
        if not await self.task_service.fail(task, error):
            return
        match_id = task.match_id  # type: ignore[attr-defined]
        if match_id is None:
            return
        from application.services import opt_in_notifications
        from application.services.discord import discord_queue
        discord_queue.enqueue(opt_in_notifications.seed_failed(match_id, error))

        requester = await self._relation(User, task.requested_by_id)  # type: ignore[attr-defined]
        if requester is None:
            # No requester means nothing to attribute the failure to, and the
            # audit layer refuses a null actor by design. The task row still
            # carries the error, and match_live still clears the UI below.
            match_live.publish(match_id)
            return

        await self.audit_service.write_and_publish(
            requester,
            AuditActions.MATCH_SEED_ROLL_FAILED,
            {
                'match_id': match_id,
                'randomizer': task.provider,
                'provider_task_id': task.provider_task_id,
                'error': error,
            },
            EventType.MATCH_SEED_ROLL_FAILED,
            event_details={'match_id': match_id, 'error': error},
        )
        # Puts the failure in front of whoever is looking at the schedule, and
        # returns the row's Generate button to a usable state.
        match_live.publish(match_id)

    @staticmethod
    async def _match_for(task: ProviderTask) -> Optional[Match]:
        """The task's match with everything ``complete_seed_roll`` reads."""
        match_id = task.match_id  # type: ignore[attr-defined]
        if match_id is None:
            return None
        return await Match.get_or_none(
            id=match_id, tenant_id=task.tenant_id,  # type: ignore[attr-defined]
        ).prefetch_related('tournament', 'players', 'players__user', 'stage')
