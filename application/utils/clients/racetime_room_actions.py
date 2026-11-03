"""Say something in a racetime.gg room, or invite someone into it.

racetime only takes a bot's actions over the room's websocket, which the bot
runtime (``racetimebot/``) holds. The runtime registers its sender here when it
starts, so a service can speak in a room without importing the runtime. With no
runtime (``RACETIME_BOT_ENABLED`` off, or a test that registered nothing) every
call returns ``False``, the same as a room the bot isn't connected to.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

Sender = Callable[[str, Dict[str, Any]], Awaitable[bool]]

_sender: Optional[Sender] = None


def register_sender(sender: Optional[Sender]) -> None:
    """Install the runtime's ``(slug, action) -> sent?`` callable, or clear it."""
    global _sender
    _sender = sender


async def send(slug: str, action: Dict[str, Any]) -> bool:
    """Send ``action`` to room ``slug``. ``False`` when it couldn't go. Never raises."""
    if _sender is None:
        logger.info('racetime action for %s dropped: the bot runtime is not running', slug)
        return False
    try:
        return await _sender(slug, action)
    except Exception:
        logger.exception('racetime action for %s failed', slug)
        return False


def message_action(text: str) -> Dict[str, Any]:
    return {
        'action': 'message',
        'data': {'message': text, 'pinned': False, 'actions': None, 'direct_to': None,
                 'guid': str(uuid.uuid4())},
    }


def invite_action(racetime_user_id: str) -> Dict[str, Any]:
    return {'action': 'invite_to_race', 'data': {'user': racetime_user_id}}


async def say(slug: str, text: str) -> bool:
    return await send(slug, message_action(text))


async def invite(slug: str, racetime_user_id: str) -> bool:
    return await send(slug, invite_action(racetime_user_id))
