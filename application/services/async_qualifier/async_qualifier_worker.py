"""Async-qualifier run expiry worker.

A background loop (the shape of ``volunteer_reminder`` / ``race_room_worker``)
that finds in-progress qualifier runs past — or approaching — their deadline,
warns the runner once, and then forfeits the run automatically. Why a run needs a
deadline at all is explained in
:mod:`application.services.async_qualifier.async_qualifier_expiry`.

Each run's work happens inside its own ``tenant_scope``; a per-run failure is
logged and retried on the next tick, and a tenant with the qualifier feature
disabled is skipped rather than raising (the worker carve-out in CLAUDE.md).
"""

import logging
from datetime import datetime, timezone

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 60


async def _tick() -> None:
    from application.repositories import AsyncQualifierRepository, AsyncQualifierRunRepository
    from application.services.async_qualifier import async_qualifier_rules as rules
    from application.services.async_qualifier.async_qualifier_service import (
        AsyncQualifierService,
    )
    from application.services.feature_flag_service import FeatureFlagService
    from models import FeatureFlag

    now = datetime.now(timezone.utc)
    # Every in-progress run, not an age-filtered slice: each qualifier sets its
    # own limit, so no single cutoff is right for all of them, and the set is
    # small (one active run per player per qualifier).
    candidates = await AsyncQualifierRunRepository.list_in_progress_all()
    stale_claims = await AsyncQualifierRunRepository.list_stale_claims_all(
        now - rules.REVIEW_CLAIM_TTL
    )
    # Qualifiers whose queue might have gone unworked. Cheap to enumerate (a
    # community runs a handful), and the per-qualifier read behind the decision is
    # two scalars.
    backlogs = await AsyncQualifierRepository.list_active_all()
    if not candidates and not stale_claims and not backlogs:
        return

    service = AsyncQualifierService()

    async def _handle(run) -> None:
        if not await FeatureFlagService().is_enabled(FeatureFlag.ASYNC_QUALIFIERS):
            return  # tenant has async qualifiers disabled
        qualifier = run.qualifier
        deadline = rules.run_deadline(qualifier, run.started_at)
        if deadline is None:
            return
        if now >= deadline:
            await service.expire_run(run, now=now)
            logger.info('expired abandoned qualifier run %s', run.id)
            return
        # Not due yet — warn once, if the deadline is close enough.
        if run.expiry_warned_at is None and now >= deadline - rules.expiry_warning_lead(qualifier):
            await service.warn_run_expiring(run, deadline, now=now)
            logger.info('warned qualifier run %s of upcoming expiry', run.id)

    async def _unclaim(run) -> None:
        if not await FeatureFlagService().is_enabled(FeatureFlag.ASYNC_QUALIFIERS):
            return  # tenant has async qualifiers disabled
        await service.release_stale_claim(run)
        logger.info('released a stale review claim on qualifier run %s', run.id)

    # ``tenant_id`` is Tortoise's generated FK column, invisible to mypy.
    def tenant_id_of(run):
        return run.tenant_id

    def run_event_id(run):
        return run.qualifier.event_id

    await for_each_tenant_scoped(
        candidates,
        _handle,
        tenant_id_of=tenant_id_of,
        event_id_of=run_event_id,
        logger=logger,
        describe=lambda run: f'qualifier run {getattr(run, "id", None)}',
    )
    await for_each_tenant_scoped(
        stale_claims,
        _unclaim,
        tenant_id_of=tenant_id_of,
        event_id_of=run_event_id,
        logger=logger,
        describe=lambda run: f'stale review claim on qualifier run {getattr(run, "id", None)}',
    )

    async def _watch(qualifier) -> None:
        """Two things per active qualifier: its window, and its review queue.

        The window crossing belongs to a worker because nobody performs it — the
        clock reaches ``opens_at`` and qualifying is open, with no request in flight
        to notice. An admin's own edit is synced by the service instead, so a
        qualifier activated into its own window announces itself without waiting.
        """
        if not await FeatureFlagService().is_enabled(FeatureFlag.ASYNC_QUALIFIERS):
            return  # tenant has async qualifiers disabled
        crossed = await service.sync_window_state(qualifier, now=now)
        if crossed is not None:
            logger.info('qualifier %s window is now %s', qualifier.id, crossed.value)
        sent = await service.notify_review_backlog(qualifier, now=now)
        if sent:
            logger.info('told %s reviewer(s) about the queue on qualifier %s',
                        sent, qualifier.id)

    await for_each_tenant_scoped(
        backlogs,
        _watch,
        tenant_id_of=tenant_id_of,
        event_id_of=lambda q: q.event_id,
        logger=logger,
        describe=lambda q: f'window and review backlog on qualifier {getattr(q, "id", None)}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'async qualifier run expiry', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
