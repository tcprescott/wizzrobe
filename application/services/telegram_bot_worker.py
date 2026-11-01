"""The Telegram bot: answers ``/start <token>`` and ``/stop``.

Two ways in. By default it long-polls ``getUpdates`` so no public webhook has
to be registered. With ``TELEGRAM_WEBHOOK_SECRET`` set, :func:`start` registers
``<BASE_URL>/telegram/webhook`` instead and Telegram POSTs each update there
(:func:`handle_update`), so nothing has to stay up polling. It only binds chats
through the one-time tokens a profile mints (see
``NotificationChannelService.create_telegram_link``), and answers commands in
private chats only; anything else gets a short pointer back to the profile.
Started only when ``TELEGRAM_BOT_TOKEN`` is set and Discord isn't mocked.
"""

import asyncio
import logging
from typing import Optional

from application.utils.background_loop import run_worker_loop
from application.utils.clients import telegram_client

logger = logging.getLogger(__name__)

TICK_SECONDS = 1
POLL_TIMEOUT = 25

_HELP = ('This bot sends Wizzrobe notifications. To connect it, open Wizzrobe, go to '
         'Profile → Notifications and press Connect next to Telegram.')

_offset: Optional[int] = None


async def handle_text(chat_id: int, username: Optional[str], text: str) -> Optional[str]:
    """The reply for one message, or ``None`` to stay quiet."""
    from application.services.notification_channel_service import NotificationChannelService

    command, _, argument = text.partition(' ')
    # Commands may arrive as /start@BotName in some clients.
    command = command.split('@', 1)[0].lower()
    service = NotificationChannelService()
    if command == '/start' and argument.strip():
        return await service.handle_telegram_start(argument.strip(), chat_id, username)
    if command == '/stop':
        return await service.handle_telegram_stop(chat_id)
    if command in ('/start', '/help'):
        return _HELP
    return None


async def handle_update(update: telegram_client.TelegramUpdate) -> None:
    """Answer one update, from the poll or the webhook. Never raises."""
    if update.chat_id is None or update.chat_type != 'private' or not update.text.startswith('/'):
        return
    try:
        reply = await handle_text(update.chat_id, update.username, update.text)
        if reply:
            await telegram_client.send_message(update.chat_id, reply)
    except telegram_client.TelegramBlocked:
        return
    except Exception:
        logger.exception('Telegram update %s failed', update.update_id)


_webhook_cleared = False


async def _tick() -> None:
    global _offset, _webhook_cleared
    # A webhook left over from webhook mode makes every getUpdates fail.
    if not _webhook_cleared:
        _webhook_cleared = await telegram_client.delete_webhook()
    updates = await telegram_client.get_updates(_offset, poll_seconds=POLL_TIMEOUT)
    for update in updates:
        _offset = update.update_id + 1
        await handle_update(update)


_loop = run_worker_loop(_tick, TICK_SECONDS, 'telegram bot', logger)


WEBHOOK_PATH = '/telegram/webhook'


def webhook_url() -> str:
    from application.utils.environment import get_base_url
    return f'{get_base_url().rstrip("/")}{WEBHOOK_PATH}'


async def register_webhook() -> bool:
    """Point Telegram at this deployment's webhook; ``False`` when webhook mode is off."""
    secret = telegram_client.webhook_secret()
    if secret is None:
        return False
    if await telegram_client.set_webhook(webhook_url(), secret):
        logger.info('Telegram webhook registered at %s', webhook_url())
    else:
        logger.warning('Telegram webhook registration failed; updates will not arrive')
    return True


# Hold the reference: an unreferenced task can be garbage collected.
_register_task: Optional[asyncio.Task] = None


def start() -> None:
    """Register the webhook in the background, or start polling when no webhook secret is set."""
    global _register_task
    if telegram_client.webhook_secret() is not None:
        _register_task = asyncio.get_running_loop().create_task(register_webhook())
        return
    _loop.start()


async def stop() -> None:
    await _loop.stop()
    await telegram_client.aclose()
