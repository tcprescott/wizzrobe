"""
Volunteer Position Service - Business Logic Layer

CRUD for coordinator-defined volunteer positions/jobs.
"""

from typing import List, Optional

from application.event_context import event_scoped
from application.repositories import VolunteerPositionRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.volunteer.volunteer_role_mapping_service import (
    VolunteerRoleMappingService,
)
from application.tenant_context import require_tenant_id
from models import User, VolunteerPosition


class VolunteerPositionService:
    """Manage the arbitrary, coordinator-defined position list."""

    def __init__(self) -> None:
        self.repository = VolunteerPositionRepository()
        self.audit_service = AuditService()
        self.role_mapping_service = VolunteerRoleMappingService()

    @staticmethod
    def _validate_stagger(
        shift_length_minutes: Optional[int], stagger_minutes: Optional[int],
    ) -> None:
        """Validate a position's stagger config (both unset = fixed blocks)."""
        if shift_length_minutes is None and stagger_minutes is None:
            return
        if shift_length_minutes is None or stagger_minutes is None:
            raise ValueError(
                "Set both shift length and stagger interval, or leave both blank."
            )
        if shift_length_minutes <= 0 or stagger_minutes <= 0:
            raise ValueError("Shift length and stagger interval must be positive.")
        if stagger_minutes > shift_length_minutes:
            raise ValueError(
                "Stagger interval cannot exceed the shift length (it would leave coverage gaps)."
            )

    async def list_all(self) -> List[VolunteerPosition]:
        return await self.repository.list_all()

    async def list_active(self) -> List[VolunteerPosition]:
        return await self.repository.list_active()

    async def list_for_viewer(
        self, viewer: Optional[User], active_only: bool = False,
    ) -> List[VolunteerPosition]:
        await AuthService.ensure(
            await AuthService.can_view_volunteer_schedule(viewer),
            "You don't have access to the volunteer schedule.",
        )
        return await (self.repository.list_active() if active_only else self.repository.list_all())

    async def get(self, position_id: int) -> Optional[VolunteerPosition]:
        return await self.repository.get_by_id(position_id)

    async def create(
        self,
        actor: User,
        name: str,
        description: Optional[str] = None,
        color: Optional[str] = None,
        display_order: int = 0,
        is_active: bool = True,
        shift_length_minutes: Optional[int] = None,
        stagger_minutes: Optional[int] = None,
        staff_alerts: bool = False,
    ) -> VolunteerPosition:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can manage positions.",
        )
        name = (name or '').strip()
        if not name:
            raise ValueError("Position name is required.")
        if await event_scoped(VolunteerPosition.filter(name=name)).exists():
            raise ValueError(f"A position named '{name}' already exists.")
        self._validate_stagger(shift_length_minutes, stagger_minutes)
        position = await self.repository.create(
            name=name, description=description, color=color,
            display_order=display_order, is_active=is_active,
            shift_length_minutes=shift_length_minutes, stagger_minutes=stagger_minutes,
            staff_alerts=staff_alerts,
        )
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_POSITION_CREATED,
            {'position_id': position.id, 'name': name},
        )
        return position

    async def update(self, actor: User, position: VolunteerPosition, **fields) -> VolunteerPosition:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can manage positions.",
        )
        if not fields:
            return position
        if 'name' in fields:
            new_name = (fields['name'] or '').strip()
            if not new_name:
                raise ValueError("Position name is required.")
            if await VolunteerPosition.filter(name=new_name, tenant_id=require_tenant_id()).exclude(id=position.id).exists():
                raise ValueError(f"A position named '{new_name}' already exists.")
            fields['name'] = new_name
        if 'shift_length_minutes' in fields or 'stagger_minutes' in fields:
            self._validate_stagger(
                fields.get('shift_length_minutes', position.shift_length_minutes),
                fields.get('stagger_minutes', position.stagger_minutes),
            )
        position = await self.repository.update(position, **fields)
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_POSITION_UPDATED,
            {'position_id': position.id, 'fields': sorted(fields.keys())},
        )
        return position

    async def delete(self, actor: User, position: VolunteerPosition) -> None:
        await AuthService.ensure(
            await AuthService.can_manage_volunteers(actor),
            "Only volunteer coordinators can manage positions.",
        )
        position_id = position.id
        await self.repository.delete(position)
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_POSITION_DELETED, {'position_id': position_id},
        )
        # The cascade took its shifts, assignments and mappings with it.
        await self.role_mapping_service.reconcile_all(actor)
