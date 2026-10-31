"""OAuth Handoff Repository — data access for pending cross-host login handoffs.

``OAuthHandoff`` is **global** (no tenant column): the handoff runs before any
community session exists. Nothing here is tenant-scoped.
"""

from datetime import datetime
from typing import Optional

from models import OAuthHandoff


class OAuthHandoffRepository:
    """Unscoped storage for :class:`~models.OAuthHandoff`."""

    @staticmethod
    async def create(nonce: str, host: str, payload: dict, expires_at: datetime) -> None:
        await OAuthHandoff.create(nonce=nonce, host=host, payload=payload, expires_at=expires_at)

    @staticmethod
    async def delete_expired(now: datetime) -> int:
        return await OAuthHandoff.filter(expires_at__lte=now).delete()

    @staticmethod
    async def take(nonce: str) -> Optional[OAuthHandoff]:
        """Return and delete the row for ``nonce``; ``None`` if absent or another caller took it first."""
        row = await OAuthHandoff.get_or_none(nonce=nonce)
        if row is None:
            return None
        deleted = await OAuthHandoff.filter(nonce=nonce).delete()
        return row if deleted else None

    @staticmethod
    async def delete_all() -> None:
        await OAuthHandoff.all().delete()
