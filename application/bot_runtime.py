"""Start and stop the pieces both entry points share.

``main.py`` (the web app and API) and ``bots.py`` (the always-on bot service)
each bring up the Discord client and the delivery queues that carry DMs,
webhooks and event-bus fan-out. Keeping that here means the two processes
can't drift: an event a racetime handler publishes on the bot service reaches
the same webhook and telemetry subscribers it would in the single process.
"""

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger('wizzrobe.runtime')

RETRY_SECONDS = 30

# Hold the reference: an unreferenced task can be garbage collected.
_discord_task: Optional[asyncio.Task] = None


def _log_discord_task_result(task: asyncio.Task) -> None:
    """Surface a bot startup/gateway failure immediately instead of letting it
    sit unobserved in a GC-able task (bad token, intents, gateway crash)."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error('Discord bot task exited with an exception', exc_info=exc)


async def _run_gateway(bot, token: str) -> None:
    """Keep trying to bring the bot up until it connects.

    discord.py reconnects on its own once logged in, but ``login()`` runs outside
    that loop: one failed attempt (Discord down while the container restarts) left
    the bot dead, and every DM and button with it, until the next restart.
    """
    import discord
    while True:
        try:
            await bot.start(token)
            return
        except discord.LoginFailure:
            raise
        except Exception:
            logger.warning('Discord bot could not connect; retrying in %ss',
                           RETRY_SECONDS, exc_info=True)
        await bot.close()
        bot.clear()
        await asyncio.sleep(RETRY_SECONDS)


async def _sign_in_rest() -> None:
    """Gateway-less sign-in, retried like ``_run_gateway`` so an outage at boot heals."""
    import discord

    from application.services.discord.discord_service import sign_in_for_rest
    while True:
        try:
            await sign_in_for_rest()
            logger.info('Discord signed in for REST only (no gateway)')
            return
        except discord.LoginFailure:
            raise
        except Exception:
            logger.warning('Discord REST sign-in failed; retrying in %ss',
                           RETRY_SECONDS, exc_info=True)
        await asyncio.sleep(RETRY_SECONDS)


async def start_discord(*, gateway: bool) -> None:
    """Connect the shared Discord client in the background.

    ``gateway=False`` signs in for REST only: DMs still send, and button presses
    arrive as HTTP interactions (``discordbot/http_interactions.py``).
    """
    global _discord_task
    from application.services.discord.discord_service import get_discord_bot
    from application.utils.mocks.mock_discord import is_mock_discord

    if is_mock_discord():
        logger.info('MOCK_DISCORD enabled — skipping Discord bot start.')
        return
    token = os.environ.get('DISCORD_TOKEN')
    if not token:
        logger.warning('DISCORD_TOKEN not set. Discord features will not work.')
        return
    if gateway:
        _discord_task = asyncio.create_task(_run_gateway(get_discord_bot(), token))
    else:
        _discord_task = asyncio.create_task(_sign_in_rest())
    _discord_task.add_done_callback(_log_discord_task_result)


async def close_discord() -> None:
    global _discord_task
    from application.services.discord.discord_service import get_discord_bot
    from application.utils.mocks.mock_discord import is_mock_discord

    if is_mock_discord():
        return
    await get_discord_bot().close()
    if _discord_task is not None:
        _discord_task.cancel()
        try:
            await _discord_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception('Discord bot task errored during shutdown')
        _discord_task = None


def start_delivery() -> None:
    """Start the DM, webhook and event fan-out queues and subscribe their consumers."""
    from application.events import EventType, event_bus, webhook_queue
    from application.events import dispatch_queue as event_dispatch_queue
    from application.services import PersonalWebhookService, TelemetryService, WebhookService
    from application.services.discord import discord_queue
    from application.services.personal_webhook_service import SUBSCRIBABLE_EVENT_TYPES
    from application.services.race_room_bot_service import on_seed_rolled_event

    discord_queue.start()
    event_dispatch_queue.start()
    webhook_queue.start()
    event_bus.subscribe_async(WebhookService().deliver_event)
    event_bus.subscribe_async(
        PersonalWebhookService().deliver_event, event_types=SUBSCRIBABLE_EVENT_TYPES,
    )
    event_bus.subscribe_async(TelemetryService().record_event)
    event_bus.subscribe_async(on_seed_rolled_event, event_types=[EventType.MATCH_SEED_ROLLED])


async def stop_delivery() -> None:
    from application.events import dispatch_queue as event_dispatch_queue
    from application.events import webhook_queue
    from application.services import web_push_service
    from application.services.discord import discord_queue

    await event_dispatch_queue.stop()
    await webhook_queue.stop()
    await discord_queue.stop()
    await web_push_service.aclose_http_client()
