"""
NotificationPreference Repository - Data Access Layer

One person's preset and per-message notification choices in the current community.
"""

from typing import Dict, List, Optional, Tuple

from application.repositories._tenant import current_tenant_id, scoped
from models import NotificationPreference, NotificationPreset


class NotificationPreferenceRepository:
    @staticmethod
    async def get(user_id: int) -> Optional[NotificationPreference]:
        return await scoped(NotificationPreference.filter(user_id=user_id)).first()

    @staticmethod
    async def save(user_id: int, preset: NotificationPreset, overrides: Dict[str, str]) -> None:
        updated = await scoped(NotificationPreference.filter(user_id=user_id)).update(
            preset=preset, overrides=overrides,
        )
        if not updated:
            await NotificationPreference.create(
                tenant_id=current_tenant_id(), user_id=user_id, preset=preset, overrides=overrides,
            )

    @staticmethod
    async def user_ids_choosing(key: str) -> List[Tuple[int, str]]:
        """Every (user id, mode) whose overrides name ``key`` in this community."""
        rows = await scoped(NotificationPreference.all()).values_list('user_id', 'overrides')
        return [(user_id, overrides[key]) for user_id, overrides in rows if key in (overrides or {})]
