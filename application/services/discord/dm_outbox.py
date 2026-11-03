"""The DM outbox: what ``send_dm`` keeps on a ``SentMessage`` row so a Discord send can be retried.

``send_dm`` stores the Discord message as JSON (``outbox_payload``) before it
sends; a failure in ``DM_RETRYABLE`` leaves the row pending for
``dm_outbox_worker``, which rebuilds the message (``rebuild``) and sends it
again. Buttons survive only as a :class:`ViewSpec`, a registered view kind
and its arguments.
"""

import logging
from typing import Any, Dict, Optional, Tuple

import discord

from application.services.sent_message_service import SentMessageRef, SentMessageService
from application.utils.discord_messages import DMLink
from models import SentMessage

logger = logging.getLogger(__name__)

DM_SEND_TIMEOUT_SECONDS = 45
DM_BOT_MISSING = "Discord bot not initialized"
DM_BOT_OFFLINE = "Discord bot is not connected. Please try again in a moment."
DM_TIMED_OUT = "Discord didn't answer in time. Please try again in a moment."
DM_DISCORD_TROUBLE = "Discord had a problem sending this, so it will be tried again."
# Failures that may clear up on their own; the DM stays in the outbox and dm_outbox_worker resends it.
DM_RETRYABLE = frozenset({DM_BOT_MISSING, DM_BOT_OFFLINE, DM_TIMED_OUT, DM_DISCORD_TROUBLE})
# Longer than one send can take, so a send in flight is never taken over.
DM_CLAIM_SECONDS = DM_SEND_TIMEOUT_SECONDS + 75


class ViewSpec:
    """A DM's buttons by registered kind and arguments, so a DM in the outbox can be rebuilt later.

    Called like a view factory; ``to_wire``/``from_wire`` round-trip it through JSON.
    """

    __slots__ = ('args', 'key')

    def __init__(self, key: str, *args: Any) -> None:
        self.key = key
        self.args = args

    def __call__(self) -> discord.ui.View:
        from application.services.discord.discord_service import _view_factories

        return _view_factories[self.key](*self.args)

    def to_wire(self) -> list:
        return [self.key, [getattr(a, 'value', a) for a in self.args]]

    @classmethod
    def from_wire(cls, data: list) -> 'ViewSpec':
        return cls(data[0], *data[1])


def outbox_payload(
    message: str, view_factory: Any, embed: Optional[discord.Embed], link: Optional[DMLink], silent: bool,
) -> Optional[Dict[str, Any]]:
    """The Discord message as JSON, or ``None`` when its buttons can't be rebuilt (an ad-hoc factory)."""
    if view_factory is not None and not isinstance(view_factory, ViewSpec):
        return None
    try:
        return {
            'content': message,
            'embed': embed.to_dict() if embed is not None else None,
            'view': view_factory.to_wire() if view_factory is not None else None,
            'link': [link.label, link.url] if link is not None else None,
            'silent': silent,
        }
    except Exception:
        logger.exception('DM could not be written to the outbox; it is sent once, without a retry')
        return None


def rebuild(outbox: Optional[Dict[str, Any]]) -> Tuple[str, Optional[ViewSpec], Optional[discord.Embed], Optional[DMLink], bool]:
    """``(content, view, embed, link, silent)`` from a stored payload."""
    data = outbox or {}
    return (
        data.get('content') or '',
        ViewSpec.from_wire(data['view']) if data.get('view') else None,
        discord.Embed.from_dict(data['embed']) if data.get('embed') else None,
        DMLink(*data['link']) if data.get('link') else None,
        bool(data.get('silent')),
    )


async def record_outcome(
    log: SentMessageService, ref: Optional[SentMessageRef], result: Tuple[bool, str], *, retry: bool,
) -> None:
    """Settle the logged DM, or leave it waiting when the failure may clear up and it can be resent."""
    from application.services.discord.discord_service import sent_message_status

    if retry and not result[0] and result[1] in DM_RETRYABLE:
        await log.record_retry(ref, result[1])
        return
    await log.record_result(ref, *sent_message_status(result))


async def resend(service: Any, row: SentMessage) -> Tuple[bool, str]:
    """Send a waiting DM again through ``service``, to Discord only: its other copies already went."""
    content, view, embed, link, silent = rebuild(row.outbox)
    result = await service._send_to_discord(int(row.discord_id), content, view, embed, link, None, silent)
    await record_outcome(SentMessageService(), SentMessageRef(row.id, row.tenant_id), result, retry=True)
    return result
