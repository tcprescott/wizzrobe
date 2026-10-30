"""The Telegram bot: answers ``/start <token>`` and ``/stop``.

Long-polls ``getUpdates`` so no public webhook has to be registered. It only
binds chats through the one-time tokens a profile mints (see
``NotificationChannelService.create_telegram_link``), and answers commands in
private chats only; anything else gets a short pointer back to the profile.
Started only when ``TELEGRAM_BOT_TOKEN`` is set and Discord isn't mocked.
"""

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


async def _tick() -> None:
    global _offset
    updates = await telegram_client.get_updates(_offset, poll_seconds=POLL_TIMEOUT)
    for update in updates:
        _offset = update.update_id + 1
        if update.chat_id is None or update.chat_type != 'private' or not update.text.startswith('/'):
            continue
        try:
            reply = await handle_text(update.chat_id, update.username, update.text)
            if reply:
                await telegram_client.send_message(update.chat_id, reply)
        except telegram_client.TelegramBlocked:
            continue
        except Exception:
            logger.exception('Telegram update %s failed', update.update_id)


_loop = run_worker_loop(_tick, TICK_SECONDS, 'telegram bot', logger)


def start() -> None:
    _loop.start()


async def stop() -> None:
    await _loop.stop()
    await telegram_client.aclose()
