"""
NotificationSetting Repository - Data Access Layer

A community's overrides of the notification catalogue's defaults.
"""

from typing import Dict, Optional, Tuple

from application.repositories._tenant import current_tenant_id, scoped
from models import NotificationMode, NotificationRecipient, NotificationSetting


class NotificationSettingRepository:
    @staticmethod
    async def overrides() -> Dict[Tuple[str, NotificationRecipient], NotificationMode]:
        rows = await scoped(NotificationSetting.all())
        return {(r.notice, r.recipient): r.mode for r in rows}

    @staticmethod
    async def upsert(
        notice: str, recipient: NotificationRecipient, mode: NotificationMode,
        updated_by_id: Optional[int],
    ) -> None:
        updated = await scoped(NotificationSetting.filter(
            notice=notice, recipient=recipient,
        )).update(mode=mode, updated_by_id=updated_by_id)
        if not updated:
            await NotificationSetting.create(
                tenant_id=current_tenant_id(), notice=notice, recipient=recipient,
                mode=mode, updated_by_id=updated_by_id,
            )

    @staticmethod
    async def delete(notice: str, recipient: NotificationRecipient) -> int:
        return await scoped(NotificationSetting.filter(notice=notice, recipient=recipient)).delete()

    @staticmethod
    async def delete_all() -> int:
        return await scoped(NotificationSetting.all()).delete()
