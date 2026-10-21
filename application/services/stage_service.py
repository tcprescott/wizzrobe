"""
Stage Service - Business Logic Layer

Handles stage-related operations including creation, updates, and validation.
"""

from typing import Optional

from application.errors import require_found
from application.repositories import StageRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import require_tenant_id
from application.utils.safe_url import normalize_http_url
from models import Stage, User


class StageService:
    """Service for stage-related business operations."""

    def __init__(self) -> None:
        self.repository = StageRepository()
        self.audit_service = AuditService()

    async def create_stage(
        self,
        name: str,
        stream_url: Optional[str] = None,
        is_active: bool = True,
        actor: Optional[User] = None,
    ) -> Stage:
        await AuthService.ensure(
            await AuthService.can_manage_stages(actor),
            "User cannot manage stages",
        )

        if not name or not name.strip():
            raise ValueError("Stage name is required")
        if await self.repository.name_taken(name.strip()):
            raise ValueError(f"A stage named '{name.strip()}' already exists.")

        stage = await self.repository.create(
            name=name.strip(),
            stream_url=normalize_http_url(stream_url, 'The stream link'),
            is_active=is_active,
        )

        await self.audit_service.write_log(
            actor,
            AuditActions.STAGE_CREATED,
            {'stage_id': stage.id, 'name': stage.name},
        )

        return stage

    async def update_stage(
        self,
        stage: Stage,
        name: Optional[str] = None,
        stream_url: Optional[str] = None,
        is_active: Optional[bool] = None,
        actor: Optional[User] = None,
    ) -> Stage:
        await AuthService.ensure(
            await AuthService.can_manage_stages(actor),
            "User cannot manage stages",
        )

        if name is not None and (not name or not name.strip()):
            raise ValueError("Stage name cannot be empty")

        update_data = {}
        if name is not None:
            if await self.repository.name_taken(name.strip(), exclude_id=stage.id):
                raise ValueError(f"A stage named '{name.strip()}' already exists.")
            update_data['name'] = name.strip()
        if stream_url is not None:
            update_data['stream_url'] = normalize_http_url(stream_url, 'The stream link')
        if is_active is not None:
            update_data['is_active'] = is_active

        result = await self.repository.update(stage, **update_data)

        await self.audit_service.write_log(
            actor,
            AuditActions.STAGE_UPDATED,
            {'stage_id': stage.id, 'changed_fields': list(update_data.keys())},
        )

        return result

    async def delete_stage(self, stage: Stage, actor: Optional[User] = None) -> None:
        await AuthService.ensure(
            await AuthService.can_manage_stages(actor),
            "User cannot manage stages",
        )
        match_count = await self.repository.count_matches(stage)
        if match_count:
            raise ValueError(
                f"This stage has {match_count} match(es) assigned. "
                "Mark it inactive instead of deleting it."
            )
        stage_id = stage.id
        await stage.delete()
        await self.audit_service.write_log(
            actor,
            AuditActions.STAGE_DELETED,
            {'stage_id': stage_id},
        )

    async def get_all_stages(self, active_only: bool = False) -> list[Stage]:
        if active_only:
            return await Stage.filter(is_active=True, tenant_id=require_tenant_id())
        return await Stage.filter(tenant_id=require_tenant_id())

    async def get_stage_by_id(self, stage_id: int) -> Optional[Stage]:
        return await self.repository.get_by_id(stage_id)

    async def require_in_tenant(self, stage_id: Optional[int]) -> None:
        """Raise unless ``stage_id`` is this tenant's stage (``None`` passes).

        A stage is referenced by bare integer from REST bodies and admin
        dialogs, and ``Match.stage`` spans tenants — unchecked, one
        community could attach another's stage to a match and surface that stage's
        name on their schedule, its Discord embed and every API response. Callers
        that accept the id from a request must run this before persisting it.
        """
        if stage_id is not None:
            require_found(
                await self.repository.get_by_id(stage_id),
                f"Stage {stage_id}",
            )
