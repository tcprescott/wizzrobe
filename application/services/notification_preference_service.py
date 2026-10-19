"""
NotificationPreference Service - Business Logic Layer

A person's own say over the messages they get (Profile → Notifications): a
preset, plus Off / Silent / Ping on any single message. The community's setting
(:class:`NotificationSettingsService`) is a ceiling: the quieter of the two wins,
so nobody can make ping what their community sends quietly.
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from application.repositories import NotificationPreferenceRepository, UserRepository
from application.repositories._save import save_fields
from application.services.auth_service import AuthService
from application.services.notification_catalog import (
    CATALOG,
    FOLLOWERS,
    PLAYERS,
    STAFF_COMMENT,
    WATCHERS,
    Notice,
    NoticeTarget,
    is_opt_in,
    is_urgent,
    needs_action,
    targets,
)
from application.services.notification_settings_service import NotificationSettingsService
from application.tenant_context import get_current_tenant_id
from models import NotificationMode, NotificationPreset, NotificationRecipient, User

logger = logging.getLogger(__name__)

OFF, SILENT, PING = NotificationMode.OFF, NotificationMode.SILENT, NotificationMode.PING
_RANK = {OFF: 0, SILENT: 1, PING: 2}

STAFF_RECIPIENTS = frozenset({
    NotificationRecipient.ON_DUTY, NotificationRecipient.ADMINS, NotificationRecipient.STAFF,
})

# Grouped by what the reader is doing when the message arrives, not by the admin
# page's groups: the same "Match scheduled" reads differently as your own match
# and as one you watch.
SECTIONS: Tuple[Tuple[str, str], ...] = (
    ('Your matches', 'When you\'re playing'),
    ('Brackets and requests', 'Bookings, sign-ups and reschedule answers'),
    ('Async qualifiers', 'Runs you submitted'),
    ('Matches you crew', 'When you\'re commentating or tracking'),
    ('Matches you watch', 'Matches you tapped the bell on'),
    ('Tournaments and events you follow', "Other people's matches, and community events you subscribed to"),
    ('Volunteer shifts', "Shifts you're on"),
    ('Your membership', 'Joining, events you host, event check-in'),
    ('Staff alerts', 'Requests and problems someone on staff has to handle'),
)

_CACHE_SECONDS = 30.0
_cache: Dict[Tuple[int, str], Tuple[float, NotificationPreset, Dict[str, str]]] = {}


def quieter(a: NotificationMode, b: NotificationMode) -> NotificationMode:
    return a if _RANK[a] <= _RANK[b] else b


def _key(target: NoticeTarget) -> str:
    return f'{target.key}:{target.recipient.value}'


def preset_mode(preset: NotificationPreset, target: NoticeTarget) -> NotificationMode:
    """What ``preset`` asks for ``target``, before the community's ceiling."""
    if is_opt_in(target):
        return OFF
    if preset is NotificationPreset.EVERYTHING:
        return PING
    if preset is NotificationPreset.NEEDS_ME:
        return PING if needs_action(target) else SILENT
    if is_urgent(target):
        return PING
    if target.recipient in (WATCHERS, FOLLOWERS):
        return OFF
    return SILENT


def section_for(target: NoticeTarget) -> str:
    recipient, group = target.recipient, CATALOG[target.key].group
    if recipient in STAFF_RECIPIENTS:
        return 'Staff alerts'
    if recipient is PLAYERS:
        if group in ('Brackets', 'Reschedule requests'):
            return 'Brackets and requests'
        if group == 'Async qualifiers':
            return 'Async qualifiers'
        return 'Your matches'
    return {
        NotificationRecipient.CREW: 'Matches you crew',
        NotificationRecipient.WATCHERS: 'Matches you watch',
        NotificationRecipient.FOLLOWERS: 'Tournaments and events you follow',
        NotificationRecipient.VOLUNTEERS: 'Volunteer shifts',
    }.get(recipient, 'Your membership')


@dataclass(frozen=True)
class MyNotificationRow:
    target: NoticeTarget
    community: NotificationMode
    preset: NotificationMode
    override: Optional[NotificationMode]

    @property
    def notice(self) -> Notice:
        return CATALOG[self.target.key]

    @property
    def section(self) -> str:
        return section_for(self.target)

    @property
    def mine(self) -> NotificationMode:
        return self.override if self.override is not None else self.preset

    @property
    def effective(self) -> NotificationMode:
        return quieter(self.community, self.mine)

    @property
    def changed(self) -> bool:
        return self.override is not None and self.override != self.preset

    @property
    def opt_in(self) -> bool:
        return is_opt_in(self.target)


@dataclass(frozen=True)
class MyNotifications:
    preset: NotificationPreset
    rows: List[MyNotificationRow]

    def counts(self, preset: Optional[NotificationPreset] = None) -> Dict[NotificationMode, int]:
        """How many messages would ping, arrive silently or stay off under ``preset``.

        Without ``preset``: as things stand, overrides included.
        """
        counts = dict.fromkeys((PING, SILENT, OFF), 0)
        for row in self.rows:
            mode = row.effective if preset is None else quieter(row.community, preset_mode(preset, row.target))
            counts[mode] += 1
        return counts

    @property
    def changed(self) -> int:
        return sum(1 for r in self.rows if r.changed)


class NotificationPreferenceService:
    @staticmethod
    def _forget(user: User) -> None:
        tenant_id = get_current_tenant_id()
        if tenant_id is not None and user.discord_id:
            _cache.pop((tenant_id, str(user.discord_id)), None)

    @staticmethod
    async def _load_for_discord_id(tenant_id: int, discord_id: str) -> Tuple[NotificationPreset, Dict[str, str]]:
        hit = _cache.get((tenant_id, discord_id))
        if hit is not None and hit[0] > time.monotonic():
            return hit[1], hit[2]
        user = await UserRepository.get_by_discord_id(discord_id)
        pref = await NotificationPreferenceRepository.get(user.id) if user else None
        preset = pref.preset if pref else NotificationPreset.EVERYTHING
        overrides = dict(pref.overrides) if pref else {}
        _cache[(tenant_id, discord_id)] = (time.monotonic() + _CACHE_SECONDS, preset, overrides)
        return preset, overrides

    @classmethod
    async def mode_for(cls, discord_id: int, target: NoticeTarget) -> NotificationMode:
        """The recipient's own choice for ``target``, before the community's ceiling.

        Never raises: outside a community, for an unknown account, or on a failed
        read it answers ``PING``, which leaves the community's setting in charge,
        except for an opt-in message, which stays off until someone chose it.
        """
        fallback = OFF if is_opt_in(target) else PING
        tenant_id = get_current_tenant_id()
        if tenant_id is None or not discord_id:
            return fallback
        try:
            preset, overrides = await cls._load_for_discord_id(tenant_id, str(discord_id))
        except Exception:
            logger.exception('Reading notification preferences failed; using the community setting')
            return fallback
        chosen = overrides.get(_key(target))
        if chosen is not None:
            try:
                return NotificationMode(chosen)
            except ValueError:
                pass
        return preset_mode(preset, target)

    @staticmethod
    async def opted_in_user_ids(target: NoticeTarget) -> Set[int]:
        """Who turned an opt-in message on here, so a sender can skip everyone else
        without a lookup per person. The community's ceiling still applies in ``send_dm``."""
        rows = await NotificationPreferenceRepository.user_ids_choosing(_key(target))
        return {user_id for user_id, mode in rows if mode != OFF.value}

    @staticmethod
    async def _require(actor: Optional[User]) -> User:
        await AuthService.ensure(actor is not None, 'Sign in to change your notifications')
        assert actor is not None
        return actor

    @classmethod
    async def get_mine(cls, actor: Optional[User]) -> MyNotifications:
        """Every message that can reach ``actor`` here, with their choice and the community's.

        Staff alerts are listed only for people who can see the admin side.
        """
        actor = await cls._require(actor)
        pref = await NotificationPreferenceRepository.get(actor.id)
        preset = pref.preset if pref else NotificationPreset.EVERYTHING
        overrides = dict(pref.overrides) if pref else {}
        staff_side = await AuthService.can_view_admin(actor)
        rows = []
        for target in targets():
            if target.recipient in STAFF_RECIPIENTS and not staff_side:
                continue
            chosen = overrides.get(_key(target))
            override = NotificationMode(chosen) if chosen in NotificationMode._value2member_map_ else None
            if override is None and target == STAFF_COMMENT.to(PLAYERS) and not actor.dm_match_comments:
                override = OFF
            rows.append(MyNotificationRow(
                target, await NotificationSettingsService.mode_for(target),
                preset_mode(preset, target), override,
            ))
        rows.sort(key=lambda r: [s for s, _ in SECTIONS].index(r.section))
        return MyNotifications(preset, rows)

    @classmethod
    async def set_preset(cls, actor: Optional[User], preset: NotificationPreset | str) -> MyNotifications:
        """Switch ``actor``'s starting point. Their per-message choices stay."""
        actor = await cls._require(actor)
        try:
            preset = NotificationPreset(preset)
        except ValueError:
            raise ValueError('That is not a notification preset.') from None
        pref = await NotificationPreferenceRepository.get(actor.id)
        await NotificationPreferenceRepository.save(actor.id, preset, dict(pref.overrides) if pref else {})
        cls._forget(actor)
        return await cls.get_mine(actor)

    @classmethod
    async def set_mode(
        cls, actor: Optional[User], notice_key: str, recipient: NotificationRecipient | str,
        mode: Optional[NotificationMode | str],
    ) -> MyNotificationRow:
        """Choose how one message reaches ``actor``; ``None`` goes back to the preset."""
        actor = await cls._require(actor)
        notice = CATALOG.get(notice_key)
        try:
            recipient = NotificationRecipient(recipient)
            mode = NotificationMode(mode) if mode is not None else None
        except ValueError:
            raise ValueError('That is not a notification setting.') from None
        if notice is None or recipient not in notice.defaults:
            raise ValueError('That is not a notification setting.')
        target = notice.to(recipient)
        pref = await NotificationPreferenceRepository.get(actor.id)
        preset = pref.preset if pref else NotificationPreset.EVERYTHING
        overrides = dict(pref.overrides) if pref else {}
        if mode is None or mode == preset_mode(preset, target):
            overrides.pop(_key(target), None)
        else:
            overrides[_key(target)] = mode.value
        await NotificationPreferenceRepository.save(actor.id, preset, overrides)
        if target == STAFF_COMMENT.to(PLAYERS):
            # The older Profile switch for staff comments, still read by the
            # comment sender: keep it in step so the two never disagree.
            actor.dm_match_comments = mode is not OFF
            await save_fields(actor, 'dm_match_comments')
        cls._forget(actor)
        mine = await cls.get_mine(actor)
        return next(r for r in mine.rows if r.target == target)

    @classmethod
    async def reset(cls, actor: Optional[User]) -> MyNotifications:
        """Drop every per-message choice, keeping the preset."""
        actor = await cls._require(actor)
        pref = await NotificationPreferenceRepository.get(actor.id)
        if pref is not None:
            await NotificationPreferenceRepository.save(actor.id, pref.preset, {})
        if not actor.dm_match_comments:
            actor.dm_match_comments = True
            await save_fields(actor, 'dm_match_comments')
        cls._forget(actor)
        return await cls.get_mine(actor)
