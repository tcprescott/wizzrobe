"""Race Room Profile Service — reusable racetime room settings (SYNC_ADMIN).

A :class:`~models.RaceRoomProfile` bundles the racetime ``startrace`` parameters
a community reuses across tournaments (goal, chat/streaming rules, timers). It is
tenant-scoped; all mutations are gated by :meth:`AuthService.can_manage_sync` and
audited. The PR 4/6 room-creation flow reads these values when it opens a room.
"""

from typing import Any, Dict, List, Optional

from application.errors import require_found
from application.repositories import RaceRoomProfileRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from models import RaceRoomProfile, User

_BOOL_FIELDS = (
    'invitational', 'unlisted', 'auto_start', 'allow_comments',
    'allow_midrace_chat', 'allow_non_entrant_chat', 'streaming_required',
)
_INT_FIELDS = ('chat_message_delay', 'start_delay', 'time_limit')


class RaceRoomProfileService:
    """CRUD for tenant-scoped reusable race-room settings."""

    def __init__(self) -> None:
        self.repository = RaceRoomProfileRepository()
        self.audit_service = AuditService()

    async def list_profiles(self, actor: Optional[User]) -> List[RaceRoomProfile]:
        await self._ensure(actor)
        return await self.repository.list_all()

    async def list_selectable(self) -> List[RaceRoomProfile]:
        """All of the tenant's profiles, for a tournament's profile select
        (read-only; no management gate — anyone editing a tournament may pick
        one)."""
        return await self.repository.list_all()

    async def get_profile(self, actor: Optional[User], profile_id: int) -> RaceRoomProfile:
        await self._ensure(actor)
        return await self._require(profile_id)

    async def create_profile(
        self, actor: Optional[User], *, name: str, **fields: Any
    ) -> RaceRoomProfile:
        await self._ensure(actor)
        name = (name or '').strip()
        if not name:
            raise ValueError('A profile name is required')
        if await self.repository.get_by_name(name) is not None:
            raise ValueError(f"A race room profile named '{name}' already exists")
        clean = self._clean(fields)
        clean['name'] = name
        profile = await self.repository.create(**clean)
        await self.audit_service.write_log(
            actor, AuditActions.RACE_ROOM_PROFILE_CREATED,
            {'profile_id': profile.id, 'name': name},
        )
        return profile

    async def update_profile(
        self, actor: Optional[User], profile_id: int, *, name: Optional[str] = None, **fields: Any
    ) -> RaceRoomProfile:
        await self._ensure(actor)
        profile = await self._require(profile_id)
        clean = self._clean(fields)
        if name is not None:
            new_name = (name or '').strip()
            if not new_name:
                raise ValueError('A profile name is required')
            if new_name != profile.name:
                existing = await self.repository.get_by_name(new_name)
                if existing is not None and existing.id != profile.id:
                    raise ValueError(f"A race room profile named '{new_name}' already exists")
            clean['name'] = new_name
        profile = await self.repository.update(profile, **clean)
        await self.audit_service.write_log(
            actor, AuditActions.RACE_ROOM_PROFILE_UPDATED,
            {'profile_id': profile.id, 'changed_fields': list(clean.keys())},
        )
        return profile

    async def delete_profile(self, actor: Optional[User], profile_id: int) -> None:
        await self._ensure(actor)
        profile = await self._require(profile_id)
        await self.audit_service.write_log(
            actor, AuditActions.RACE_ROOM_PROFILE_DELETED,
            {'profile_id': profile.id, 'name': profile.name},
        )
        await self.repository.delete(profile)

    @staticmethod
    def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
        clean: Dict[str, Any] = {}
        if 'goal' in fields:
            goal = fields['goal']
            clean['goal'] = (goal or '').strip() or None if isinstance(goal, str) else goal
        for key in _BOOL_FIELDS:
            if key in fields and fields[key] is not None:
                clean[key] = bool(fields[key])
        for key in _INT_FIELDS:
            if key in fields and fields[key] is not None:
                value = int(fields[key])
                if value < 0:
                    raise ValueError(f'{key} cannot be negative')
                clean[key] = value
        return clean

    async def _require(self, profile_id: int) -> RaceRoomProfile:
        return require_found(await self.repository.get_by_id(profile_id), 'Race room profile')

    @staticmethod
    async def _ensure(actor: Optional[User]) -> None:
        await AuthService.ensure_can_manage_sync(actor)
