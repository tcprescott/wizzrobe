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
from application.services.notification_catalog import (
    CATALOG,
    Notice,
    NoticeTarget,
    StaffChoice,
    notices,
    staff_choice_for,
    staff_modes,
)
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
    # On a staff recipient's row, what the message's staff recipients add up to
    # on the Staff dropdown; None on other rows and for a custom mix.
    staff_choice: Optional[StaffChoice] = None

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
        return [row for n in notices() for row in cls._rows(n, overrides)]

    @staticmethod
    def _rows(notice: Notice, overrides: Overrides) -> List[NotificationSettingRow]:
        modes = {r: overrides.get((notice.key, r), d) for r, d in notice.defaults.items()}
        choice = staff_choice_for(notice, modes) if notice.staff_recipients else None
        return [
            NotificationSettingRow(notice, r, m, choice if r in notice.staff_recipients else None)
            for r, m in modes.items()
        ]

    @staticmethod
    def _notice(notice_key: str) -> Notice:
        notice = CATALOG.get(notice_key)
        if notice is None:
            raise ValueError('That is not a notification setting.')
        return notice

    @classmethod
    async def _write(
        cls, actor: User, notice: Notice, modes: Dict[NotificationRecipient, NotificationMode],
        overrides: Overrides,
    ) -> Dict[NotificationRecipient, NotificationMode]:
        """Store ``modes`` for ``notice``, choosing the default deletes. Returns the old modes."""
        old = {r: overrides.get((notice.key, r), notice.defaults[r]) for r in modes}
        for recipient, mode in modes.items():
            if mode == old[recipient]:
                continue
            if mode == notice.defaults[recipient]:
                await NotificationSettingRepository.delete(notice.key, recipient)
            else:
                await NotificationSettingRepository.upsert(notice.key, recipient, mode, actor.id)
            overrides[(notice.key, recipient)] = mode
        cls._forget(get_current_tenant_id())
        return old

    @classmethod
    async def set_mode(
        cls, actor: User, notice_key: str, recipient: NotificationRecipient | str,
        mode: NotificationMode | str,
    ) -> NotificationSettingRow:
        """Set one cell. Choosing the default removes the override."""
        await AuthService.ensure(
            await AuthService.is_staff(actor), 'Only staff can change notification settings',
        )
        notice = cls._notice(notice_key)
        try:
            recipient = NotificationRecipient(recipient)
            mode = NotificationMode(mode)
        except ValueError:
            raise ValueError('That is not a notification setting.') from None
        if recipient not in notice.defaults:
            raise ValueError('That is not a notification setting.')
        overrides = await NotificationSettingRepository.overrides()
        old = (await cls._write(actor, notice, {recipient: mode}, overrides))[recipient]
        if old != mode:
            await AuditService().write_log(
                actor, AuditActions.NOTIFICATION_SETTING_CHANGED,
                {'notice': notice.key, 'title': notice.title, 'recipient': recipient.value,
                 'old': old.value, 'new': mode.value},
            )
        return next(r for r in cls._rows(notice, overrides) if r.recipient is recipient)

    @classmethod
    async def set_staff_choice(
        cls, actor: User, notice_key: str, choice: StaffChoice | str,
    ) -> List[NotificationSettingRow]:
        """Set every staff recipient of one message from the Staff dropdown.

        Returns the message's staff rows as they now stand.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor), 'Only staff can change notification settings',
        )
        notice = cls._notice(notice_key)
        try:
            choice = StaffChoice(choice)
        except ValueError:
            raise ValueError('That is not a staff notification choice.') from None
        modes = staff_modes(notice, choice)
        overrides = await NotificationSettingRepository.overrides()
        old = await cls._write(actor, notice, modes, overrides)
        if old != modes:
            was = staff_choice_for(notice, old)
            await AuditService().write_log(
                actor, AuditActions.NOTIFICATION_SETTING_CHANGED,
                {'notice': notice.key, 'title': notice.title, 'recipient': 'staff_alerts',
                 'old': was.value if was else 'custom',
                 'new': choice.value},
            )
        return [r for r in cls._rows(notice, overrides) if r.recipient in modes]

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
