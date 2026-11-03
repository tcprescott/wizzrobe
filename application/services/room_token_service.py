"""Room Token Service - Business Logic Layer

Issues, lists, revokes and resolves the unlisted keys that open the tournament
room's read-only seeds view (``/t/<slug>/room/<token>/seeds``).

Only the SHA-256 hash is stored; the URL is shown once, at issue. A token
authorizes exactly one page — it carries no user identity, so nothing it opens
depends on who is standing at the machine.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from application.errors import NotFoundError
from application.repositories.room_token_repository import RoomTokenRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from models import RoomToken, User

logger = logging.getLogger(__name__)

TOKEN_PREFIX = 'wizzrobe_room_'


def hash_token(raw_token: str) -> str:
    """The stored form of a raw token. Public so the dev seed can plant one."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def room_seeds_path(raw_token: str) -> str:
    """The tenant-relative page path a room token opens."""
    return f'/room/{raw_token}/seeds'


class RoomTokenService:
    """Service for tournament-room kiosk tokens."""

    def __init__(self) -> None:
        self.repository = RoomTokenRepository()
        self.audit_service = AuditService()

    async def issue(self, actor: User, label: str) -> Tuple[RoomToken, str]:
        """Issue a token for ``actor``'s community. Returns (record, raw token).

        The raw token is returned here and nowhere else — staff who lose it
        issue another and revoke this one.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can issue room tokens",
        )
        if not label or not label.strip():
            raise ValueError("Give the token a label so you can tell the machines apart.")

        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = await self.repository.create(
            label=label.strip(),
            token_hash=hash_token(raw_token),
            created_by=actor,
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.ROOM_TOKEN_CREATED,
            {'token_id': token.id, 'label': token.label},
        )
        return token, raw_token

    async def list_tokens(self, actor: User) -> List[RoomToken]:
        """Every token this community has issued, for the settings tab."""
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can view room tokens",
        )
        return await self.repository.list_all()

    async def revoke(self, actor: User, token_id: int) -> RoomToken:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can revoke room tokens",
        )
        token = await self.repository.get_by_id(token_id)
        if token is None or token.revoked_at is not None:
            raise NotFoundError("Room token not found")

        await self.repository.revoke(token, datetime.now(timezone.utc))
        await self.audit_service.write_log(
            actor,
            AuditActions.ROOM_TOKEN_REVOKED,
            {'token_id': token.id, 'label': token.label},
        )
        return token

    async def resolve(self, raw_token: str) -> Optional[RoomToken]:
        """The live token this string opens, or ``None``.

        Unknown, revoked, malformed and wrong-community all return ``None`` —
        the page renders the same plain 404 for every one of them, so guessing
        at a URL reveals nothing about which tokens exist. Touches
        ``last_used_at`` on success, which is how staff tell a machine is still
        using the token taped to it.
        """
        if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
            return None
        token = await self.repository.get_by_hash(hash_token(raw_token))
        if token is None or token.revoked_at is not None:
            return None
        await self.repository.touch_last_used(token, datetime.now(timezone.utc))
        return token
