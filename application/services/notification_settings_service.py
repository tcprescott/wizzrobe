"""
NotificationSettings Service - Business Logic Layer

Which notifications a community sends, to whom, and whether they ping
(Admin → Notifications). The catalogue and its defaults are code
(:mod:`application.services.notification_catalog`); this service reads and writes
a community's overrides and answers ``send_dm``'s one question: how should this
message reach this recipient here?
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from application.repositories import NotificationSettingRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.notification_catalog import CATALOG, Notice, NoticeTarget, notices
from application.tenant_context import get_current_tenant_id
from models import NotificationMode, NotificationRecipient, User

logger = logging.getLogger(__name__)

Overrides = Dict[Tuple[str, NotificationRecipient], NotificationMode]

# Per-community overrides, read once per burst of DMs rather than once per DM. The
# app runs one worker (docs/scaling-roadmap.md), so a write here drops the entry
# everywhere it matters; the TTL bounds a change made from another process.
_CACHE_SECONDS = 30.0
_cache: Dict[int, Tuple[float, Overrides]] = {}


@dataclass(frozen=True)
class NotificationSettingRow:
    notice: Notice
    recipient: NotificationRecipient
    mode: NotificationMode

    @property
    def default(self) -> NotificationMode:
        return self.notice.defaults[self.recipient]

    @property
    def changed(self) -> bool:
        return self.mode != self.default


class NotificationSettingsService:
    @staticmethod
    async def _overrides(tenant_id: int) -> Overrides:
        hit = _cache.get(tenant_id)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        overrides = await NotificationSettingRepository.overrides()
        _cache[tenant_id] = (time.monotonic() + _CACHE_SECONDS, overrides)
        return overrides

    @staticmethod
    def _forget(tenant_id: Optional[int]) -> None:
        if tenant_id is not None:
            _cache.pop(tenant_id, None)

    @classmethod
    async def mode_for(cls, target: NoticeTarget) -> NotificationMode:
        """The community's choice for ``target``, or the catalogue default.

        Never raises: a send outside any community, or a failed read, falls back
        to the default rather than dropping or mis-pinging the message.
        """
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            return target.default
        try:
            overrides = await cls._overrides(tenant_id)
        except Exception:
            logger.exception('Reading notification settings failed; using the default')
            return target.default
        return overrides.get((target.key, target.recipient), target.default)

    @classmethod
    async def list_settings(cls, actor: Optional[User]) -> List[NotificationSettingRow]:
        """Every catalogue cell with its current mode, in display order. Staff only."""
        await AuthService.ensure(
            await AuthService.is_staff(actor), 'Only staff can see notification settings',
        )
        overrides = await NotificationSettingRepository.overrides()
        return [
            NotificationSettingRow(n, r, overrides.get((n.key, r), default))
            for n in notices()
            for r, default in n.defaults.items()
        ]

    @classmethod
    async def set_mode(
        cls, actor: User, notice_key: str, recipient: NotificationRecipient | str,
        mode: NotificationMode | str,
    ) -> NotificationSettingRow:
        """Set one cell. Choosing the default removes the override."""
        await AuthService.ensure(
            await AuthService.is_staff(actor), 'Only staff can change notification settings',
        )
        notice = CATALOG.get(notice_key)
        try:
            recipient = NotificationRecipient(recipient)
            mode = NotificationMode(mode)
        except ValueError:
            raise ValueError('That is not a notification setting.') from None
        if notice is None or recipient not in notice.defaults:
            raise ValueError('That is not a notification setting.')
        overrides = await NotificationSettingRepository.overrides()
        old = overrides.get((notice.key, recipient), notice.defaults[recipient])
        if mode == notice.defaults[recipient]:
            await NotificationSettingRepository.delete(notice.key, recipient)
        else:
            await NotificationSettingRepository.upsert(notice.key, recipient, mode, actor.id)
        cls._forget(get_current_tenant_id())
        if old != mode:
            await AuditService().write_log(
                actor, AuditActions.NOTIFICATION_SETTING_CHANGED,
                {'notice': notice.key, 'title': notice.title, 'recipient': recipient.value,
                 'old': old.value, 'new': mode.value},
            )
        return NotificationSettingRow(notice, recipient, mode)

    @classmethod
    async def reset_all(cls, actor: User) -> int:
        """Put every notification back to its default. Returns how many changed."""
        await AuthService.ensure(
            await AuthService.is_staff(actor), 'Only staff can change notification settings',
        )
        count = await NotificationSettingRepository.delete_all()
        cls._forget(get_current_tenant_id())
        if count:
            await AuditService().write_log(
                actor, AuditActions.NOTIFICATION_SETTINGS_RESET, {'count': count},
            )
        return count
