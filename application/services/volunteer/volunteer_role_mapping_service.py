"""
VolunteerRoleMapping Service - Business Logic Layer

Maps volunteer positions onto app roles and keeps ``RoleSource.VOLUNTEER``
grants in step with who is assigned. A published assignment to any shift of a
mapped position confers the role, and it stays until the person holds no such
assignment (past shifts count). Drafts confer nothing until published.

The sync is local (no Discord round-trip), so every path that adds or removes
an assignment reconciles the people it touched straight after its own commit.
Positions belong to one event, so the roles they confer are held in it: a
reconcile covers the event in scope and leaves the others' grants alone.
"""

import logging
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from application.errors import require_found
from application.event_context import require_event_id
from application.feature_flags import requires_feature
from application.repositories import (
    UserRepository,
    UserRoleRepository,
    VolunteerAssignmentRepository,
    VolunteerPositionRepository,
    VolunteerRoleMappingRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.tenant_membership_service import TenantMembershipService
from application.tenant_context import require_tenant_id
from models import (
    FeatureFlag,
    Role,
    RoleSource,
    User,
    VolunteerPosition,
    VolunteerRoleMapping,
)

logger = logging.getLogger(__name__)


class VolunteerRoleMappingService:
    """Position-to-role mappings and the per-user reconcile behind them."""

    def __init__(self) -> None:
        self.mapping_repository = VolunteerRoleMappingRepository()
        self.position_repository = VolunteerPositionRepository()
        self.assignment_repository = VolunteerAssignmentRepository()
        self.role_repository = UserRoleRepository()
        self.user_repository = UserRepository()
        self.audit_service = AuditService()

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def list_mappings(self) -> List[VolunteerRoleMapping]:
        return await self.mapping_repository.list_all()

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def add_mapping(
        self, actor: User, position_id: int, app_role: Role,
    ) -> VolunteerRoleMapping:
        """Map a position onto a role, then grant it to everyone already on one."""
        await self._ensure_can_manage(actor)
        self._check_role(app_role)
        position = require_found(
            await self.position_repository.get_by_id(position_id), "Position"
        )
        if await self.mapping_repository.exists(position.id, app_role):
            raise ValueError("That position already grants this role.")
        mapping = await self._create(actor, position, app_role)
        await self.reconcile_all(actor)
        return mapping

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def add_mappings(
        self, actor: User, position_ids: Sequence[int], app_roles: Sequence[Role],
    ) -> Tuple[int, int]:
        """Map every position onto every role; ``(created, already_mapped)``.

        Existing pairs are skipped, every position and role is checked before
        the first write, and the reconcile runs once for the whole batch.
        """
        await self._ensure_can_manage(actor)
        if not position_ids or not app_roles:
            raise ValueError("Pick at least one position and one role.")
        for role in app_roles:
            self._check_role(role)
        positions = [
            require_found(await self.position_repository.get_by_id(pid), "Position")
            for pid in position_ids
        ]
        created = skipped = 0
        for position in positions:
            for role in app_roles:
                if await self.mapping_repository.exists(position.id, role):
                    skipped += 1
                    continue
                await self._create(actor, position, role)
                created += 1
        if created:
            await self.reconcile_all(actor)
        return created, skipped

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def remove_mapping(self, actor: User, mapping_id: int) -> None:
        """Drop a mapping, then take the role back from whoever only had it from here."""
        await self._ensure_can_manage(actor)
        mapping = require_found(await self.mapping_repository.get_by_id(mapping_id), "Mapping")
        await self._delete(actor, mapping)
        await self.reconcile_all(actor)

    @requires_feature(FeatureFlag.VOLUNTEERS)
    async def remove_mappings(self, actor: User, mapping_ids: Sequence[int]) -> int:
        """Drop each mapping that still exists, then reconcile once; returns how many."""
        await self._ensure_can_manage(actor)
        removed = 0
        for mapping_id in mapping_ids:
            mapping = await self.mapping_repository.get_by_id(mapping_id)
            if mapping is None:
                continue
            await self._delete(actor, mapping)
            removed += 1
        if removed:
            await self.reconcile_all(actor)
        return removed

    @staticmethod
    async def _ensure_can_manage(actor: User) -> None:
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            "Only Staff can manage volunteer role mappings.",
        )

    @staticmethod
    def _check_role(app_role: Role) -> None:
        if app_role not in Role.volunteer_mappable():
            raise ValueError(
                "A volunteer position can't grant that role. Coordinators decide "
                "who's on a shift, so only operational roles can be mapped."
            )

    async def _create(
        self, actor: User, position: VolunteerPosition, app_role: Role,
    ) -> VolunteerRoleMapping:
        mapping = await self.mapping_repository.create(position_id=position.id, app_role=app_role)
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_ROLE_MAPPING_ADDED,
            {'mapping_id': mapping.id, 'position_id': position.id,
             'position_name': position.name, 'app_role': app_role.value},
        )
        return mapping

    async def _delete(self, actor: User, mapping: VolunteerRoleMapping) -> None:
        details = {
            'mapping_id': mapping.id, 'position_id': mapping.position.id,
            'position_name': mapping.position.name, 'app_role': mapping.app_role.value,
        }
        await self.mapping_repository.delete(mapping)
        await self.audit_service.write_log(
            actor, AuditActions.VOLUNTEER_ROLE_MAPPING_REMOVED, details,
        )

    # feature-gate: exempt — soft integration point called by sibling services
    # after their own commit; skips (returns {}) when the flag is off.
    async def reconcile_all(self, actor: User) -> Dict[int, Dict[str, List[str]]]:
        """Reconcile everyone who is assigned or holds a volunteer-sourced role.

        Never raises, for the same reason ``reconcile_users`` doesn't.
        """
        try:
            user_ids = set(await self.assignment_repository.published_user_ids())
            user_ids.update(
                await self.role_repository.list_user_ids_by_source(RoleSource.VOLUNTEER, require_event_id())
            )
        except Exception:
            logger.exception('Volunteer role sync could not list who to reconcile')
            return {}
        return await self.reconcile_users(actor, user_ids)

    # feature-gate: exempt — soft integration point called by sibling services
    # after their own commit; skips (returns {}) when the flag is off.
    async def reconcile_users(
        self, actor: User, user_ids: Iterable[int],
    ) -> Dict[int, Dict[str, List[str]]]:
        """Bring each user's volunteer-sourced roles in line with their assignments.

        Never raises: the assignment change that triggered it has already
        committed, and a failed sync must not read as a failed assignment. The
        next change touching that person, or any mapping edit, repairs it.
        Returns ``{user_id: {'granted': [...], 'revoked': [...]}}`` for the
        users whose roles moved.
        """
        ids = {uid for uid in user_ids if uid is not None}
        if not ids:
            return {}
        try:
            if not await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS):
                return {}
            mappings = await self.mapping_repository.list_all()
            roles_by_position: Dict[int, Set[Role]] = {}
            mappable = set(Role.volunteer_mappable())
            for m in mappings:
                # Filter stored rows too: a mapping saved before the allow-list must grant nothing.
                if m.app_role in mappable:
                    roles_by_position.setdefault(m.position.id, set()).add(m.app_role)
            users = await self.user_repository.get_by_ids(sorted(ids))
            changed: Dict[int, Dict[str, List[str]]] = {}
            for user in users.values():
                result = await self._reconcile_user(actor, user, roles_by_position)
                if result['granted'] or result['revoked']:
                    changed[user.id] = result
            return changed
        except Exception:
            logger.exception('Volunteer role sync failed for users %s', sorted(ids))
            return {}

    async def _reconcile_user(
        self, actor: User, user: User, roles_by_position: Dict[int, Set[Role]],
    ) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {'granted': [], 'revoked': []}
        positions = await self.assignment_repository.published_position_ids_for_user(user.id)
        desired: Set[Role] = set()
        for position_id in positions:
            desired |= roles_by_position.get(position_id, set())

        event_id = require_event_id()
        held = await self.role_repository.list_for_user(user, event_id)
        current_all = {r.role for r in held}
        current_volunteer = {
            r.role for r in held
            if r.source == RoleSource.VOLUNTEER and r.event_id == event_id  # type: ignore[attr-defined]
        }
        tenant_id = require_tenant_id()

        granting = sorted(desired - current_all, key=lambda r: r.value)
        # A non-member here was removed after scheduling: old shifts must not re-admit them.
        if granting and not await TenantMembershipService().is_member(user):
            granting = []
        for role in granting:
            await self.role_repository.add(
                user, role, granted_by=actor, source=RoleSource.VOLUNTEER, event_id=event_id,
            )
            await self.audit_service.write_log(
                actor, AuditActions.ROLE_VOLUNTEER_SYNC_GRANTED,
                {'role': role.value, 'source': RoleSource.VOLUNTEER.value,
                 'target_user_id': user.id, 'tenant_id': tenant_id, 'event_id': event_id},
            )
            result['granted'].append(role.value)

        for role in sorted(current_volunteer - desired, key=lambda r: r.value):
            await self.role_repository.remove(user, role, event_id=event_id)
            await self.audit_service.write_log(
                actor, AuditActions.ROLE_VOLUNTEER_SYNC_REVOKED,
                {'role': role.value, 'source': RoleSource.VOLUNTEER.value,
                 'target_user_id': user.id, 'tenant_id': tenant_id, 'event_id': event_id},
            )
            result['revoked'].append(role.value)
        return result
