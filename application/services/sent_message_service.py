"""Sent Message Service - the log of every DM Wizzrobe sent.

``DiscordService.send_dm`` writes one :class:`SentMessage` per DM (before the
send, so the device/email/Telegram mirrors can report back against it) and
updates it with Discord's answer. Staff read it from Admin → Reports → Sent
messages.

Logging must never cost a DM: every write here swallows its own failure.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, NamedTuple, Optional

from application.repositories import SentMessageRepository, UserRepository
from application.services.auth_service import AuthService
from application.tenant_context import get_current_tenant_id
from models import SentMessage, SentMessageStatus, User

logger = logging.getLogger(__name__)

_TITLE_MAX = 256
_ERROR_MAX = 500
_LINK_LABEL_MAX = 80
_LINK_URL_MAX = 1024

PROBLEM_STATUSES = [SentMessageStatus.BLOCKED, SentMessageStatus.FAILED]


class SentMessageRef(NamedTuple):
    """Where a logged DM lives, for the writes that follow the first one."""

    id: int
    tenant_id: int


def _title_for(message: str, embed: Any) -> Optional[str]:
    title = getattr(embed, 'title', None) if embed is not None else None
    if not title:
        title = next((line.strip() for line in (message or '').splitlines() if line.strip()), None)
    return title[:_TITLE_MAX] if title else None


def _embed_dict(embed: Any) -> Optional[dict]:
    if embed is None:
        return None
    try:
        return embed.to_dict()
    except Exception:
        return None


class SentMessageService:
    """Write and read the per-community DM log."""

    def __init__(self) -> None:
        self.repository = SentMessageRepository()

    # ------------------------------------------------------------- writing

    async def record_attempt(
        self,
        discord_id: int,
        message: str,
        *,
        embed: Any = None,
        link: Any = None,
        silent: bool = False,
        notice: Any = None,
    ) -> Optional[SentMessageRef]:
        """Log a DM about to go out. ``None`` outside a community, or if the write fails.

        ``notice`` is the catalogue entry a sender passes to ``send_dm``; only its
        ``key`` and ``recipient`` are kept (as ``notice_key`` and ``audience``), so the report
        can name the message type.
        """
        tenant_id = get_current_tenant_id()
        if tenant_id is None or not discord_id:
            return None
        try:
            recipient = await UserRepository.get_by_discord_id(str(discord_id))
            audience = getattr(notice, 'recipient', None)
            row = await self.repository.create(
                recipient_id=recipient.id if recipient else None,
                discord_id=str(discord_id),
                title=_title_for(message, embed),
                body=message or '',
                embed=_embed_dict(embed),
                link_label=(link.label or '')[:_LINK_LABEL_MAX] or None if link is not None else None,
                link_url=(link.url or '')[:_LINK_URL_MAX] or None if link is not None else None,
                silent=silent,
                notice_key=getattr(notice, 'key', None),
                audience=getattr(audience, 'value', audience),
            )
            return SentMessageRef(row.id, tenant_id)
        except Exception:
            logger.exception('Could not log DM to %s', discord_id)
            return None

    async def _update(self, ref: Optional[SentMessageRef], **fields: Any) -> None:
        if ref is None:
            return
        try:
            await self.repository.update(ref.id, ref.tenant_id, **fields)
        except Exception:
            logger.exception('Could not update sent message %s', ref.id)

    async def record_result(
        self, ref: Optional[SentMessageRef], status: SentMessageStatus, error: Optional[str] = None,
    ) -> None:
        await self._update(ref, status=status, error=(error or None) and error[:_ERROR_MAX])

    async def record_push(self, ref: Optional[SentMessageRef], devices: int) -> None:
        await self._update(ref, push_devices=devices)

    async def record_channels(
        self, ref: Optional[SentMessageRef], *, email: Optional[bool], telegram: Optional[bool],
    ) -> None:
        fields = {k: v for k, v in (('email_sent', email), ('telegram_sent', telegram)) if v is not None}
        if fields:
            await self._update(ref, **fields)

    # ------------------------------------------------------------- reading

    @staticmethod
    async def _ensure_can_read(actor: User) -> None:
        """Staff only: a DM can carry a staff-private note or someone's request."""
        if not await AuthService.is_staff(actor):
            raise PermissionError('Only Staff can read sent messages.')

    async def list_messages(
        self,
        *,
        actor: User,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        problems_only: bool = False,
        search: Optional[str] = None,
        recipient_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SentMessage]:
        await self._ensure_can_read(actor)
        return await self.repository.list(
            start, end, PROBLEM_STATUSES if problems_only else None, search, recipient_id,
            limit=limit, offset=offset,
        )

    async def count_messages(
        self,
        *,
        actor: User,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        problems_only: bool = False,
        search: Optional[str] = None,
        recipient_id: Optional[int] = None,
    ) -> int:
        await self._ensure_can_read(actor)
        return await self.repository.count(
            start, end, PROBLEM_STATUSES if problems_only else None, search, recipient_id,
        )

    async def totals(
        self, *, actor: User, start: Optional[datetime] = None, end: Optional[datetime] = None,
    ) -> Dict[str, int]:
        """``sent``, ``silent``, ``off_or_blocked`` and ``failed`` counts over the window."""
        await self._ensure_can_read(actor)
        raw = await self.repository.totals(start, end)
        return {
            'sent': sum(n for k, n in raw.items() if k != 'silent'),
            'silent': raw.get('silent', 0),
            'off_or_blocked': raw.get(SentMessageStatus.DISCORD_OFF.value, 0)
            + raw.get(SentMessageStatus.BLOCKED.value, 0),
            'failed': raw.get(SentMessageStatus.FAILED.value, 0),
        }

    async def get_message(self, message_id: int, *, actor: User) -> Optional[SentMessage]:
        await self._ensure_can_read(actor)
        return await self.repository.get(message_id)
