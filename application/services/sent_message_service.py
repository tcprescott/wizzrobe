"""Sent Message Service - the log of every DM Wizzrobe sent.

``DiscordService.send_dm`` writes one :class:`SentMessage` per DM (before the
send, so the device/email/Telegram mirrors can report back against it) and
updates it with Discord's answer. Staff read it from Admin → Reports → Sent
messages.

Logging must never cost a DM: every write here swallows its own failure.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, NamedTuple, Optional

from application.repositories import SentMessageRepository, UserRepository
from application.services.auth_service import AuthService
from application.services.notification_catalog import CATALOG, GROUPS
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


def _discord_id(user: User) -> Optional[str]:
    return str(user.discord_id) if user.discord_id else None


class SentMessageService:
    """Write and read the per-community DM log."""

    def __init__(self) -> None:
        self.repository = SentMessageRepository()

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
        await self._update(
            ref, status=status, error=(error or None) and error[:_ERROR_MAX], outbox=None, claimed_until=None,
        )

    async def hold(self, ref: Optional[SentMessageRef], outbox: Dict[str, Any], seconds: float) -> None:
        """Keep the Discord message in the outbox, held by this sender for ``seconds``, until its result is recorded."""
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await self._update(ref, outbox=outbox, claimed_until=until)

    async def record_retry(self, ref: Optional[SentMessageRef], error: Optional[str]) -> None:
        """A send that may work later: stays pending, in the outbox, for ``dm_outbox_worker``."""
        if ref is None:
            return
        try:
            await self.repository.count_attempt(ref.id, ref.tenant_id, (error or None) and error[:_ERROR_MAX])
        except Exception:
            logger.exception('Could not update sent message %s', ref.id)

    async def claim(self, message: SentMessage, now: datetime, until: datetime) -> bool:
        return await self.repository.claim(message.id, now, until)

    async def list_waiting(self, now: datetime, limit: int = 100) -> List[SentMessage]:
        return await self.repository.list_waiting(now, limit)

    @staticmethod
    async def tenants_with_waiting() -> List[int]:
        return await SentMessageRepository.tenants_with_waiting()

    async def record_push(self, ref: Optional[SentMessageRef], devices: int) -> None:
        await self._update(ref, push_devices=devices)

    async def record_channels(
        self, ref: Optional[SentMessageRef], *, email: Optional[bool], telegram: Optional[bool],
    ) -> None:
        fields = {k: v for k, v in (('email_sent', email), ('telegram_sent', telegram)) if v is not None}
        if fields:
            await self._update(ref, **fields)

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
        event_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SentMessage]:
        await self._ensure_can_read(actor)
        return await self.repository.list(
            start, end, PROBLEM_STATUSES if problems_only else None, search, recipient_id, event_id,
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
        event_id: Optional[int] = None,
    ) -> int:
        await self._ensure_can_read(actor)
        return await self.repository.count(
            start, end, PROBLEM_STATUSES if problems_only else None, search, recipient_id, event_id,
        )

    async def totals(
        self, *, actor: User, start: Optional[datetime] = None, end: Optional[datetime] = None,
        event_id: Optional[int] = None,
    ) -> Dict[str, int]:
        """``sent``, ``silent``, ``off_or_blocked`` and ``failed`` counts over the window."""
        await self._ensure_can_read(actor)
        raw = await self.repository.totals(start, end, event_id)
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

    @staticmethod
    def _keys_in(group: Optional[str]) -> Optional[List[str]]:
        if group is None:
            return None
        return [key for key, notice in CATALOG.items() if notice.group == group]

    async def list_mine(
        self, *, actor: User, group: Optional[str] = None, limit: int = 30, offset: int = 0,
    ) -> List[SentMessage]:
        """The messages sent to ``actor`` in this community, newest first.

        ``group`` narrows to one catalogue group ("Matches", "Volunteer shifts", …).
        """
        return await self.repository.list_for_recipient(
            actor.id, _discord_id(actor), self._keys_in(group), limit=limit, offset=offset,
        )

    async def count_mine(self, *, actor: User, group: Optional[str] = None) -> int:
        return await self.repository.count_for_recipient(actor.id, _discord_id(actor), self._keys_in(group))

    async def my_groups(self, *, actor: User) -> List[str]:
        """The catalogue groups ``actor`` has had a message from, in catalogue order."""
        keys = set(await self.repository.notice_keys_for_recipient(actor.id, _discord_id(actor)))
        present = {CATALOG[k].group for k in keys if k in CATALOG}
        return [g for g in GROUPS if g in present]

    async def get_mine(self, message_id: int, *, actor: User) -> Optional[SentMessage]:
        """One message sent to ``actor``, or ``None`` (someone else's reads as missing)."""
        return await self.repository.get_for_recipient(message_id, actor.id, _discord_id(actor))
