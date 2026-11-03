"""Background worker for outbound webhook deliveries.

Kept apart from ``dispatch_queue`` because a receiver that hangs costs about
half a minute per delivery (timeout times retries), and on the shared queue that
delayed every push, email and Telegram mirror behind it.
"""

import logging
from collections.abc import Coroutine

from application.utils.coroutine_queue import CoroutineQueue

logger = logging.getLogger(__name__)

_q = CoroutineQueue(
    'webhook delivery',
    logger=logger,
    drop_warning=(
        'webhook delivery queue stopping with %d item(s) still queued — '
        'they will not run'
    ),
)


def start() -> None:
    _q.start()


async def stop() -> None:
    await _q.stop()


def discard_pending() -> int:
    """Close whatever is queued without running it (see ``CoroutineQueue``)."""
    return _q.discard_pending()


def enqueue(coro: Coroutine) -> None:
    _q.enqueue(coro)
