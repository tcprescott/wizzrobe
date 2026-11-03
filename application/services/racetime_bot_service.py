"""Racetime Bot Service — platform administration of shared racetime bots.

A :class:`~models.RacetimeBot` is **global** (like the Discord token / VAPID
keys): one bot per game category, holding that category's OAuth credentials.
Only SUPER_ADMIN may manage them, and every mutation runs on ``/platform`` with
*no* tenant context, so the audit rows are platform-level (``tenant=NULL``).

The ``client_secret`` is a privileged secret. It is never returned to a
tenant-facing surface and never logged — :meth:`serialize` (used by the admin
tables) omits it, updates only rewrite it when a new value is supplied, and the
tenant-facing :meth:`list_authorized_for_tenant` exposes only id/category/name.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from application.errors import require_found
from application.repositories import RacetimeBotRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from models import BotStatus, RacetimeBot, RacetimeBotTenant, User


class RacetimeBotService:
    """SUPER_ADMIN CRUD + tenant authorization grants for racetime bots."""

    def __init__(self) -> None:
        self.repository = RacetimeBotRepository()
        self.audit_service = AuditService()

    @staticmethod
    def serialize(bot: RacetimeBot) -> Dict[str, Any]:
        """A dict safe for an admin table — the ``client_secret`` is omitted."""
        return {
            'id': bot.id,
            'category': bot.category,
            'client_id': bot.client_id,
            'name': bot.name,
            'description': bot.description or '',
            'is_active': bot.is_active,
            'handler_class': bot.handler_class or '',
            'status': bot.status,
            'status_message': bot.status_message or '',
        }

    async def list_bots(self, actor: Optional[User]) -> List[RacetimeBot]:
        await self._ensure_super_admin(actor)
        return await self.repository.list_all()

    async def get_bot(self, actor: Optional[User], bot_id: int) -> RacetimeBot:
        await self._ensure_super_admin(actor)
        return await self._require(bot_id)

    async def create_bot(
        self,
        actor: Optional[User],
        *,
        category: str,
        client_id: str,
        client_secret: str,
        name: str,
        description: Optional[str] = None,
        handler_class: Optional[str] = None,
        is_active: bool = True,
    ) -> RacetimeBot:
        await self._ensure_super_admin(actor)
        category = (category or '').strip()
        name = (name or '').strip()
        client_id = (client_id or '').strip()
        client_secret = (client_secret or '').strip()
        if not category:
            raise ValueError('A racetime category is required')
        if not name:
            raise ValueError('A bot name is required')
        if not client_id or not client_secret:
            raise ValueError('Both client id and client secret are required')
        await self._ensure_category_available(category)
        bot = await self.repository.create(
            category=category,
            client_id=client_id,
            client_secret=client_secret,
            name=name,
            description=(description or '').strip() or None,
            handler_class=(handler_class or '').strip() or None,
            is_active=is_active,
        )
        await self.audit_service.write_log(
            actor, AuditActions.RACETIME_BOT_CREATED,
            {'bot_id': bot.id, 'category': bot.category},
        )
        return bot

    async def update_bot(
        self,
        actor: Optional[User],
        bot_id: int,
        *,
        category: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        handler_class: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> RacetimeBot:
        await self._ensure_super_admin(actor)
        bot = await self._require(bot_id)
        changes: Dict[str, Any] = {}
        if category is not None:
            new_category = (category or '').strip()
            if not new_category:
                raise ValueError('A racetime category is required')
            if new_category != bot.category:
                await self._ensure_category_available(new_category, exclude_id=bot.id)
            changes['category'] = new_category
        if client_id is not None:
            new_client_id = (client_id or '').strip()
            if not new_client_id:
                raise ValueError('Client id cannot be empty')
            changes['client_id'] = new_client_id
        # Blank means unchanged, not cleared: the form never echoes the secret.
        if client_secret is not None and (client_secret or '').strip():
            changes['client_secret'] = client_secret.strip()
        if name is not None:
            new_name = (name or '').strip()
            if not new_name:
                raise ValueError('A bot name is required')
            changes['name'] = new_name
        if description is not None:
            changes['description'] = (description or '').strip() or None
        if handler_class is not None:
            changes['handler_class'] = (handler_class or '').strip() or None
        if is_active is not None:
            changes['is_active'] = is_active
        bot = await self.repository.update(bot, **changes)
        await self.audit_service.write_log(
            actor, AuditActions.RACETIME_BOT_UPDATED,
            # Never log the secret itself — only that it was among the fields set.
            {'bot_id': bot.id, 'changed_fields': list(changes.keys())},
        )
        return bot

    async def delete_bot(self, actor: Optional[User], bot_id: int) -> None:
        await self._ensure_super_admin(actor)
        bot = await self._require(bot_id)
        await self.audit_service.write_log(
            actor, AuditActions.RACETIME_BOT_DELETED,
            {'bot_id': bot.id, 'category': bot.category},
        )
        await self.repository.delete(bot)

    async def restart_connection(self, actor: Optional[User], bot_id: int) -> RacetimeBot:
        """Drop and respawn one bot's racetime connection, for every community using it."""
        await self._ensure_super_admin(actor)
        assert actor is not None
        await self._require(bot_id)
        # Lazy: racetimebot.manager imports this module.
        from racetimebot import get_racetime_manager

        await get_racetime_manager().restart(actor, bot_id)
        return await self._require(bot_id)

    async def list_grants(self, actor: Optional[User], bot_id: int) -> List[RacetimeBotTenant]:
        await self._ensure_super_admin(actor)
        await self._require(bot_id)
        return await self.repository.list_grants_for_bot(bot_id)

    async def grant_tenant(self, actor: Optional[User], bot_id: int, tenant_id: int) -> RacetimeBotTenant:
        await self._ensure_super_admin(actor)
        await self._require(bot_id)
        grant = await self.repository.get_grant(bot_id, tenant_id)
        if grant is not None:
            if not grant.is_active:
                grant = await self.repository.set_grant_active(grant, True)
        else:
            grant = await self.repository.create_grant(bot_id, tenant_id)
        await self.audit_service.write_log(
            actor, AuditActions.RACETIME_BOT_GRANTED,
            {'bot_id': bot_id, 'tenant_id': tenant_id},
        )
        return grant

    async def revoke_tenant(self, actor: Optional[User], bot_id: int, tenant_id: int) -> None:
        await self._ensure_super_admin(actor)
        grant = await self.repository.get_grant(bot_id, tenant_id)
        if grant is None:
            return
        await self.repository.delete_grant(grant)
        await self.audit_service.write_log(
            actor, AuditActions.RACETIME_BOT_REVOKED,
            {'bot_id': bot_id, 'tenant_id': tenant_id},
        )

    # Not super-admin-gated: the caller is the in-process connection loop acting as the system user.

    async def list_active_bots(self) -> List[RacetimeBot]:
        """Active bots the runtime should hold a connection for."""
        return await self.repository.list_active()

    async def get_runtime_bot(self, bot_id: int) -> Optional[RacetimeBot]:
        """Load a bot by id with no permission gate (runtime/restart use)."""
        return await self.repository.get_by_id(bot_id)

    async def record_connected(self, bot_id: int, actor: User) -> None:
        await self._write_status(
            bot_id, actor, BotStatus.CONNECTED, AuditActions.RACETIME_BOT_CONNECTED,
            mark_connected=True,
        )

    async def record_heartbeat(self, bot_id: int) -> None:
        """Refresh ``last_checked_at`` so a wedged-without-error task is visible.

        Deliberately un-audited: it fires on every liveness tick.
        """
        bot = await self.repository.get_by_id(bot_id)
        if bot is None:
            return
        await self.repository.update(bot, last_checked_at=datetime.now(timezone.utc))

    async def record_error(
        self, bot_id: int, actor: User, message: str, *, auth_failed: bool = False,
    ) -> None:
        await self._write_status(
            bot_id, actor, BotStatus.ERROR, AuditActions.RACETIME_BOT_ERROR,
            message=message, detail_extra={'auth_failed': auth_failed},
        )

    async def mark_disconnected(
        self, bot_id: int, actor: User, message: Optional[str] = None,
    ) -> None:
        await self._write_status(
            bot_id, actor, BotStatus.DISCONNECTED, AuditActions.RACETIME_BOT_DISCONNECTED,
            message=message,
        )

    async def list_authorized_for_tenant(self, tenant_id: int) -> List[RacetimeBot]:
        """Active bots a tenant may select on a tournament (no secret exposed).

        The tenant id is passed explicitly (the caller supplies
        ``require_tenant_id()``); a category the tenant was not granted, or an
        inactive bot/grant, never appears.
        """
        return await self.repository.list_active_for_tenant(tenant_id)

    async def is_authorized_for_tenant(self, bot_id: int, tenant_id: int) -> bool:
        return any(b.id == bot_id for b in await self.repository.list_active_for_tenant(tenant_id))

    async def _require(self, bot_id: int) -> RacetimeBot:
        return require_found(await self.repository.get_by_id(bot_id), 'Racetime bot')

    async def _ensure_category_available(
        self, category: str, *, exclude_id: Optional[int] = None,
    ) -> None:
        """Reject a category already taken by a different bot (create + rename)."""
        existing = await self.repository.get_by_category(category)
        if existing is not None and existing.id != exclude_id:
            raise ValueError(f"A bot for category '{category}' already exists")

    async def _write_status(
        self,
        bot_id: int,
        actor: User,
        status: str,
        audit_action: str,
        *,
        message: Optional[str] = None,
        mark_connected: bool = False,
        detail_extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Shared status-writer for the runtime health transitions.

        Loads the bot (no-op if gone), stamps ``last_checked_at`` (and
        ``last_connected_at`` on a successful connect), writes the cached status,
        and audits it. ``message`` is trimmed/capped into ``status_message``;
        a connect clears it.
        """
        now = datetime.now(timezone.utc)
        bot = await self.repository.get_by_id(bot_id)
        if bot is None:
            return
        fields: Dict[str, Any] = {'status': status, 'last_checked_at': now}
        if mark_connected:
            fields['status_message'] = None
            fields['last_connected_at'] = now
        else:
            fields['status_message'] = (message or '').strip()[:2000] or None
        await self.repository.update(bot, **fields)
        detail: Dict[str, Any] = {'bot_id': bot_id, 'category': bot.category}
        if detail_extra:
            detail.update(detail_extra)
        await self.audit_service.write_log(actor, audit_action, detail)

    @staticmethod
    async def _ensure_super_admin(actor: Optional[User]) -> None:
        await AuthService.ensure_super_admin(actor)
