"""Re-reading linked Challonge brackets on a timer, mixed into ``ChallongeService``.

Challonge has no webhooks, so a result reported on Challonge's own site reaches
Wizzrobe only on the next sync. Each community chooses how often its brackets
are re-read (off by default): every sync costs Challonge API requests, about
two per tournament. Finished brackets are skipped.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Dict, Optional, Tuple

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories import ChallongeRepository, TenantRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import require_tenant_id
from application.utils.clients.challonge_client import ChallongeAPIError
from models import FeatureFlag, Tournament, User

AUTO_SYNC_CONFIG_KEY = 'challonge_auto_sync_minutes'
AUTO_SYNC_CHOICES: Tuple[int, ...] = (0, 15, 30, 60, 120, 360)
REQUESTS_PER_SYNC = 2

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoSyncEstimate:
    tournaments: int
    requests_per_day: int


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ChallongeAutoSyncMixin:
    """Collaborators come from ``ChallongeService``; see its ``__init__``."""

    repository: ChallongeRepository
    audit_service: AuditService
    _sync_tournament: Callable[..., Awaitable[Dict[str, int]]]

    @requires_feature(FeatureFlag.CHALLONGE)
    async def auto_sync_minutes(self) -> int:
        tenant = await TenantRepository.get_by_id(require_tenant_id())
        value = (tenant.config or {}).get(AUTO_SYNC_CONFIG_KEY) if tenant else None
        return value if isinstance(value, int) and value in AUTO_SYNC_CHOICES else 0

    @requires_feature(FeatureFlag.CHALLONGE)
    async def set_auto_sync_minutes(self, minutes: int, actor: User) -> None:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            'Only staff can change how often Challonge brackets sync',
        )
        if minutes not in AUTO_SYNC_CHOICES:
            raise ValueError('Pick one of the listed sync intervals.')
        before = await self.auto_sync_minutes()
        if minutes == before:
            return
        tenant = require_found(await TenantRepository.get_by_id(require_tenant_id()), 'Tenant')
        config = dict(tenant.config or {})
        config[AUTO_SYNC_CONFIG_KEY] = minutes
        await TenantRepository.update(tenant, config=config)
        await self.audit_service.write_log(actor, AuditActions.CHALLONGE_AUTO_SYNC_UPDATED, {
            'minutes_before': before, 'minutes': minutes,
        })

    @requires_feature(FeatureFlag.CHALLONGE)
    async def auto_sync_estimate(self, minutes: int) -> AutoSyncEstimate:
        """Roughly how many API requests a day ``minutes`` would cost right now."""
        count = await self.repository.live_auto_sync_count()
        per_day = count * REQUESTS_PER_SYNC * (24 * 60 // minutes) if minutes else 0
        return AutoSyncEstimate(count, per_day)

    @requires_feature(FeatureFlag.CHALLONGE)
    async def sync_due_brackets(self, actor: User) -> int:
        """Sync each unfinished linked bracket last synced an interval or more ago. For the worker.

        One bracket failing (deleted on Challonge, an expired grant) doesn't stop
        the rest. Returns how many synced.
        """
        minutes = await self.auto_sync_minutes()
        if not minutes:
            return 0
        due_before = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        synced = 0
        for tournament in await self.repository.auto_sync_tournaments():
            if not self._auto_sync_due(tournament, due_before):
                continue
            try:
                await self._sync_tournament(tournament, actor, force=True)
                synced += 1
            except (ValueError, ChallongeAPIError) as e:
                logger.warning('Challonge auto-sync failed for tournament %s: %s', tournament.id, e)
        return synced

    @staticmethod
    def _auto_sync_due(tournament: Tournament, due_before: datetime) -> bool:
        last = _as_utc(tournament.challonge_last_synced_at)
        return last is None or last <= due_before
