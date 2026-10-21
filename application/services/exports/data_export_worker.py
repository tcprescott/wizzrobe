"""Background builder for queued data exports.

Every few seconds: fail exports a restart cut off, delete exports past their
retention (every few minutes), then build the oldest queued export in any
community, inside that community's ``tenant_scope``. One export per tick and
one tick at a time, so however many people press Export at once, the database
only ever sees one export's reads, spread out by the builder's pauses.
"""

import logging
import time

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 5
TICK_TIMEOUT_SECONDS = 600
PRUNE_EVERY_SECONDS = 10 * 60

_last_prune = 0.0


async def _tick() -> None:
    global _last_prune
    from application.repositories import DataExportRepository
    from application.services.exports.data_export_service import (
        INTERRUPTED_AFTER,
        INTERRUPTED_ERROR,
        RETENTION,
        DataExportService,
    )

    interrupted = await DataExportRepository.fail_interrupted_all(INTERRUPTED_AFTER, INTERRUPTED_ERROR)
    if interrupted:
        logger.warning('marked %d interrupted export(s) failed', interrupted)
    if time.monotonic() - _last_prune >= PRUNE_EVERY_SECONDS:
        _last_prune = time.monotonic()
        pruned = await DataExportRepository.prune_all(RETENTION)
        if pruned:
            logger.info('deleted %d export(s) past retention', pruned)

    export = await DataExportRepository.next_queued_all()
    if export is None or not await DataExportRepository.claim(export.id):
        return

    await for_each_tenant_scoped(
        [export],
        DataExportService().build,
        tenant_id_of=lambda e: e.tenant_id,  # type: ignore[attr-defined]
        logger=logger,
        describe=lambda e: f'export {e.id}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'data exports', logger, tick_timeout=TICK_TIMEOUT_SECONDS)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
