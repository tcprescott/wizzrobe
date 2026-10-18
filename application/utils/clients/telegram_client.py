"""Minimal Telegram Bot API client.

Configured by ``TELEGRAM_BOT_TOKEN`` (from @BotFather). The bot only ever
talks to people who opened it with their own one-time link, and it reads its
updates by long polling (:func:`get_updates`), so no public webhook URL needs
registering.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(40.0, connect=5.0)
# The 40s above is sized for the getUpdates long poll; a send that slow would
# hold the shared notification queue and a person's "Send test" click.
_SEND_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
# How long the poller rests after Telegram failed to answer, so an outage is a
# warning every quarter minute rather than every second.
_POLL_BACKOFF_SECONDS = 15

_client: Optional[httpx.AsyncClient] = None
_bot_username: Optional[str] = None


class TelegramBlocked(Exception):
    """The person blocked the bot or deleted the chat; stop sending to it."""


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    chat_id: Optional[int]
    chat_type: Optional[str]
    username: Optional[str]
    text: str


def is_configured() -> bool:
    return bool(os.getenv('TELEGRAM_BOT_TOKEN'))


def _url(method: str) -> str:
    return f'https://api.telegram.org/bot{os.getenv("TELEGRAM_BOT_TOKEN", "")}/{method}'


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _call(method: str, payload: dict, http_timeout: httpx.Timeout = _SEND_TIMEOUT) -> Optional[dict]:
    try:
        response = await _get_client().post(_url(method), json=payload, timeout=http_timeout)
    except httpx.HTTPError as exc:
        # The token is in the URL, so never log the exception's request.
        logger.warning('Telegram %s failed: %s', method, type(exc).__name__)
        return None
    body = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
    if response.status_code == 403:
        raise TelegramBlocked(body.get('description') or 'Forbidden')
    if not body.get('ok'):
        logger.warning('Telegram %s refused: HTTP %s %s', method, response.status_code, body.get('description'))
        return None
    return body


async def bot_username() -> Optional[str]:
    """The bot's @name, for ``t.me`` links. Looked up once and cached."""
    global _bot_username
    if _bot_username is None and is_configured():
        body = await _call('getMe', {})
        if body:
            _bot_username = body['result'].get('username')
    return _bot_username


async def send_message(
    chat_id: int,
    text: str,
    button_url: Optional[str] = None,
    button_label: Optional[str] = None,
    silent: bool = False,
) -> bool:
    """Send plain text, with one link button when ``button_url`` is https.

    ``silent`` delivers it without a sound or vibration.

    Raises :class:`TelegramBlocked` when the chat no longer accepts messages.
    """
    payload: dict = {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}
    if silent:
        payload['disable_notification'] = True
    # Telegram rejects the whole message for a non-https button (a dev
    # localhost link), so that case falls back to text only.
    if button_url and button_url.startswith('https://'):
        payload['reply_markup'] = {
            'inline_keyboard': [[{'text': button_label or 'Open', 'url': button_url}]],
        }
    return await _call('sendMessage', payload) is not None


async def get_updates(offset: Optional[int], poll_seconds: int = 25) -> List[TelegramUpdate]:
    """Long-poll for new messages to the bot."""
    payload: dict = {'timeout': poll_seconds, 'allowed_updates': ['message']}
    if offset is not None:
        payload['offset'] = offset
    try:
        body = await _call('getUpdates', payload, http_timeout=_TIMEOUT)
    except TelegramBlocked:
        body = None
    if body is None:
        await asyncio.sleep(_POLL_BACKOFF_SECONDS)
        return []
    updates: List[TelegramUpdate] = []
    for raw in (body or {}).get('result', []):
        message = raw.get('message') or {}
        chat = message.get('chat') or {}
        updates.append(TelegramUpdate(
            update_id=int(raw['update_id']),
            chat_id=chat.get('id'),
            chat_type=chat.get('type'),
            username=(message.get('from') or {}).get('username'),
            text=(message.get('text') or '').strip(),
        ))
    return updates
