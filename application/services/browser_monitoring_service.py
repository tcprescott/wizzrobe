"""Browser Monitoring Service — super-admin control of browser-side Sentry.

Backs ``/platform`` → Monitoring: whether people's browsers report errors to
Sentry at all, whether those errors carry a session replay, what share is sent,
and the ignore rules that keep known noise out. Every change replaces the
in-memory snapshot (:mod:`application.utils.browser_monitoring`) that page
builds read; the page then pushes it to the tabs already open.

Platform-level, like log levels: no tenant context, audit rows land with
``tenant=NULL``.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from application.repositories import BrowserMonitoringRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.utils import browser_monitoring
from application.utils.browser_monitoring import (
    BUILTIN_RULES,
    MATCH_KINDS,
    SAMPLE_RATES,
    IgnoreRule,
    Snapshot,
)
from models import User

logger = logging.getLogger(__name__)

_MIN_PATTERN = 3
_MAX_PATTERN = 200


@dataclass(frozen=True)
class SettingsView:
    enabled: bool
    replay_on_error: bool
    sample_rate: float
    updated_by: Optional[str]
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class IgnoreRuleRow:
    """One ignore rule as the platform table shows it; ``id`` is None for a built-in."""

    id: Optional[int]
    pattern: str
    match: str
    created_by: Optional[str]
    created_at: Optional[datetime]

    @property
    def builtin(self) -> bool:
        return self.id is None


class BrowserMonitoringService:
    """Read and change the browser SDK's settings and ignore rules."""

    def __init__(self) -> None:
        self.repository = BrowserMonitoringRepository()
        self.audit_service = AuditService()

    async def load(self) -> Snapshot:
        """Rebuild the in-memory snapshot from the database. Startup, and after each change."""
        settings = await self.repository.get_settings()
        rules = tuple(IgnoreRule(r.pattern, r.match) for r in await self.repository.list_rules())
        snapshot = Snapshot(
            enabled=settings.enabled if settings else True,
            replay_on_error=settings.replay_on_error if settings else True,
            sample_rate=settings.sample_rate if settings else 1.0,
            rules=rules,
        )
        browser_monitoring.replace(snapshot)
        return snapshot

    async def get_settings(self, actor: Optional[User]) -> SettingsView:
        await AuthService.ensure_super_admin(actor)
        settings = await self.repository.get_settings()
        if settings is None:
            return SettingsView(True, True, 1.0, None, None)
        return SettingsView(
            enabled=settings.enabled,
            replay_on_error=settings.replay_on_error,
            sample_rate=settings.sample_rate,
            updated_by=settings.updated_by.username if settings.updated_by else None,
            updated_at=settings.updated_at,
        )

    async def update_settings(
        self, actor: Optional[User], *, enabled: bool, replay_on_error: bool, sample_rate: float,
    ) -> Snapshot:
        actor = await self._require_super_admin(actor)
        if sample_rate not in SAMPLE_RATES:
            raise ValueError('Pick one of the offered shares.')
        previous = browser_monitoring.current()
        await self.repository.save_settings(enabled, replay_on_error, sample_rate, actor)
        await self.audit_service.write_log(
            actor, AuditActions.BROWSER_MONITORING_UPDATED,
            {
                'enabled': enabled, 'replay_on_error': replay_on_error, 'sample_rate': sample_rate,
                'previous': {
                    'enabled': previous.enabled, 'replay_on_error': previous.replay_on_error,
                    'sample_rate': previous.sample_rate,
                },
            },
        )
        logger.warning(
            'Browser monitoring set to enabled=%s replay=%s sample=%s by %s',
            enabled, replay_on_error, sample_rate, actor.username,
        )
        return await self.load()

    async def list_rules(self, actor: Optional[User]) -> List[IgnoreRuleRow]:
        """The rules super-admins added, oldest first, then the built-in ones.

        Added rules lead because they're the ones anyone acts on; the built-ins
        are a long, fixed list.
        """
        await AuthService.ensure_super_admin(actor)
        rows = [
            IgnoreRuleRow(
                id=rule.id, pattern=rule.pattern, match=rule.match,
                created_by=rule.created_by.username if rule.created_by else None,
                created_at=rule.created_at,
            )
            for rule in await self.repository.list_rules()
        ]
        rows.extend(IgnoreRuleRow(None, r.pattern, r.match, None, None) for r in BUILTIN_RULES)
        return rows

    async def add_rule(self, actor: Optional[User], pattern: str, match: str) -> Snapshot:
        actor = await self._require_super_admin(actor)
        pattern = (pattern or '').strip()
        if match not in MATCH_KINDS:
            raise ValueError('Choose whether the rule matches the message or a script URL.')
        if len(pattern) < _MIN_PATTERN:
            raise ValueError(f'Use at least {_MIN_PATTERN} characters, or the rule would hide real errors.')
        if len(pattern) > _MAX_PATTERN:
            raise ValueError(f'Keep it to {_MAX_PATTERN} characters; a distinctive part of the message is enough.')
        folded = pattern.casefold()
        if any(r.match == match and r.pattern.casefold() == folded for r in BUILTIN_RULES):
            raise ValueError('A built-in rule already ignores that.')
        if await self.repository.find_rule(pattern, match) is not None:
            raise ValueError('That rule already exists.')
        rule = await self.repository.create_rule(pattern, match, actor)
        await self.audit_service.write_log(
            actor, AuditActions.BROWSER_IGNORE_RULE_ADDED,
            {'rule_id': rule.id, 'pattern': pattern, 'match': match},
        )
        logger.warning('Browser errors matching %r (%s) now ignored, added by %s', pattern, match, actor.username)
        return await self.load()

    async def remove_rule(self, actor: Optional[User], rule_id: int) -> Snapshot:
        actor = await self._require_super_admin(actor)
        rule = await self.repository.get_rule(rule_id)
        if rule is None:
            raise ValueError('That rule was already removed.')
        await self.repository.delete_rule(rule)
        await self.audit_service.write_log(
            actor, AuditActions.BROWSER_IGNORE_RULE_REMOVED,
            {'rule_id': rule_id, 'pattern': rule.pattern, 'match': rule.match},
        )
        logger.warning('Browser ignore rule %r (%s) removed by %s', rule.pattern, rule.match, actor.username)
        return await self.load()

    @staticmethod
    async def _require_super_admin(actor: Optional[User]) -> User:
        await AuthService.ensure_super_admin(actor)
        if actor is None:
            raise PermissionError('Super-admin privileges required')
        return actor
