"""
NotificationPreference Repository - Data Access Layer

One person's preset and per-message notification choices in the current community.
"""

from typing import Dict, Optional

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
