from datetime import datetime, timezone
from typing import List, Optional

from tortoise.exceptions import IntegrityError

from application.repositories._save import save_fields
from models import DevicePushToken, User


class DevicePushTokenRepository:
    """Repository for the store apps' native push tokens."""

    async def get_by_id(self, token_id: int) -> Optional[DevicePushToken]:
        return await DevicePushToken.get_or_none(id=token_id)

    async def get_by_token(self, token: str) -> Optional[DevicePushToken]:
        return await DevicePushToken.get_or_none(token=token)

    async def list_for_user(self, user: User) -> List[DevicePushToken]:
        return await DevicePushToken.filter(user=user).order_by('created_at').all()

    async def list_for_discord_id(self, discord_id: int) -> List[DevicePushToken]:
        return await DevicePushToken.filter(user__discord_id=discord_id).all()

    async def upsert(
        self, user: User, *, platform: str, token: str, device_name: Optional[str],
    ) -> DevicePushToken:
        row = await self.get_by_token(token)
        if row is None:
            try:
                return await DevicePushToken.create(
                    user=user, platform=platform, token=token, device_name=device_name,
                )
            except IntegrityError:
                # Lost a check-then-insert race on the unique token; update the row that won.
                row = await self.get_by_token(token)
                if row is None:
                    raise
        row.user = user
        row.platform = platform
        row.device_name = device_name  # type: ignore[assignment]
        await save_fields(row, 'user', 'platform', 'device_name')
        return row

    async def delete(self, row: DevicePushToken) -> None:
        await row.delete()

    async def touch_last_used(self, row: DevicePushToken) -> None:
        row.last_used_at = datetime.now(timezone.utc)
        await save_fields(row, 'last_used_at')
