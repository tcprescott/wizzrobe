"""
Matcherino Login Repository - Data Access Layer

The one stored Matcherino login per tenant that the check-in sync signs in as.
"""

from datetime import datetime
from typing import Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import MatcherinoLogin, User


class MatcherinoLoginRepository(TenantScopedRepository[MatcherinoLogin]):
    model = MatcherinoLogin

    @staticmethod
    async def get_current() -> Optional[MatcherinoLogin]:
        return await scoped(MatcherinoLogin.all()).prefetch_related('updated_by', 'alert_user').first()

    @staticmethod
    async def upsert(
        refresh_token: str, matcherino_user_id: Optional[str], updated_by: User,
    ) -> MatcherinoLogin:
        login, _ = await MatcherinoLogin.update_or_create(
            tenant_id=current_tenant_id(),
            defaults={
                'refresh_token': refresh_token,
                'matcherino_user_id': matcherino_user_id,
                'updated_by': updated_by,
                'alerted_at': None,
            },
        )
        return login

    @staticmethod
    async def delete_current() -> int:
        return await scoped(MatcherinoLogin.all()).delete()

    @staticmethod
    async def set_alert_user(user_id: Optional[int]) -> int:
        # A queryset update on purpose, so the login's saved time stays put.
        return await scoped(MatcherinoLogin.all()).update(alert_user_id=user_id, alerted_at=None)

    @staticmethod
    async def claim_alert(now: datetime) -> Optional[MatcherinoLogin]:
        """Mark the current failure as alerted; the login if this call won the claim.

        One conditional UPDATE, so two syncs failing at once send one DM.
        """
        claimed = await scoped(
            MatcherinoLogin.filter(alerted_at__isnull=True, alert_user_id__isnull=False)
        ).update(alerted_at=now)
        if not claimed:
            return None
        return await scoped(MatcherinoLogin.all()).prefetch_related('alert_user').first()

    @staticmethod
    async def clear_alert() -> int:
        return await scoped(MatcherinoLogin.filter(alerted_at__isnull=False)).update(alerted_at=None)
