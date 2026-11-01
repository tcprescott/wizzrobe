"""DM outbox sweeper.

Every minute, resends the Discord copy of DMs that are still waiting in the
outbox: the send hit a failure that may clear up (the bot was offline, Discord
timed out or answered 5xx/429), or the process sending it stopped. Their
device, email and Telegram copies went out the first time, so only Discord is
tried again.

A DM is given up (logged as failed) after ``MAX_ATTEMPTS`` tries or once it is
``GIVE_UP_AFTER`` old, because a "your match is in 15 minutes" message an hour
late does more harm than good.
"""

import logging
from datetime import datetime, timedelta, timezone

from application.utils.background_loop import for_each_tenant_scoped, run_worker_loop

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
MAX_ATTEMPTS = 5
GIVE_UP_AFTER = timedelta(minutes=15)
BATCH = 100


async def _sweep_tenant(tenant_id: int) -> None:
    from application.services.discord.discord_service import DiscordService
    from application.services.discord.dm_outbox import DM_CLAIM_SECONDS, resend
    from application.services.sent_message_service import SentMessageRef, SentMessageService
    from models import SentMessageStatus

    log = SentMessageService()
    discord_service = None
    now = datetime.now(timezone.utc)
    for row in await log.list_waiting(now, BATCH):
        if row.attempts >= MAX_ATTEMPTS or row.created_at < now - GIVE_UP_AFTER:
            await log.record_result(
                SentMessageRef(row.id, row.tenant_id), SentMessageStatus.FAILED,
                f'Gave up after {row.attempts} tries: {row.error or "not sent"}',
            )
            continue
        if not await log.claim(row, now, now + timedelta(seconds=DM_CLAIM_SECONDS)):
            continue
        discord_service = discord_service or DiscordService()
        await resend(discord_service, row)


async def _tick() -> None:
    from application.services.sent_message_service import SentMessageService

    await for_each_tenant_scoped(
        await SentMessageService.tenants_with_waiting(),
        _sweep_tenant,
        tenant_id_of=lambda tenant_id: tenant_id,
        logger=logger,
        describe=lambda tenant_id: f'community {tenant_id}',
    )


_loop = run_worker_loop(_tick, TICK_SECONDS, 'DM outbox', logger, tick_timeout=TICK_SECONDS * 5)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
