"""Say something in a racetime.gg room, or invite someone into it.

racetime only takes a bot's actions over the room's websocket, which the bot
runtime (``racetimebot/``) holds. The runtime registers its sender here when it
starts, so a service can speak in a room without importing the runtime. With no
runtime (``RACETIME_BOT_ENABLED`` off, or a test that registered nothing) every
call returns ``False``, the same as a room the bot isn't connected to.

When the runtime lives in another process (the ``bots`` container beside the
API, both with ``SEPARATE_BOTS`` on), a process with no sender hands the action
to the live relay and the process holding the runtime sends it. That hand-off
returns ``True`` once queued: whether racetime took it is only known over there.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

from application.events import live_relay
from application.utils.coroutine_queue import CoroutineQueue

logger = logging.getLogger(__name__)

Sender = Callable[[str, Dict[str, Any]], Awaitable[bool]]

RELAY_KIND = 'racetime_action'
# Asks the process holding the runtime to reconnect one bot (``racetimebot.manager``).
RESTART_KIND = 'racetime_restart'

_sender: Optional[Sender] = None
_relayed = CoroutineQueue('racetime relayed actions', logger=logger)
_relayed_started = False


def register_sender(sender: Optional[Sender]) -> None:
    """Install the runtime's ``(slug, action) -> sent?`` callable, or clear it."""
    global _sender
    _sender = sender


async def _send_here(slug: str, action: Dict[str, Any]) -> bool:
    if _sender is None:
        logger.info('racetime action for %s dropped: the bot runtime is not running', slug)
        return False
    try:
        return await _sender(slug, action)
    except Exception:
        logger.exception('racetime action for %s failed', slug)
        return False


async def send(slug: str, action: Dict[str, Any]) -> bool:
    """Send ``action`` to room ``slug``. ``False`` when it couldn't go. Never raises."""
    if _sender is None and live_relay.active():
        live_relay.forward(RELAY_KIND, slug, action)
        return True
    return await _send_here(slug, action)


def _deliver_relayed(slug: str, action: Dict[str, Any]) -> None:
    """An action another process handed over: send it if this process holds the runtime."""
    global _relayed_started
    if _sender is None:
        return
    if not _relayed_started:
        _relayed.start()
        _relayed_started = True
    _relayed.enqueue(_send_here(slug, action))


live_relay.register(RELAY_KIND, _deliver_relayed)


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
