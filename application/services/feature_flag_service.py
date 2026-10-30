"""Feature Flag Service — group-tiered per-tenant feature gating.

A feature flag exists only for a deliberately-gated feature (catalog in
``application/feature_flags.py``); flags default OFF. Availability is resolved
from a **live tier** with a per-tenant override on top:

1. **Group (tier).** A super-admin defines named ``FeatureFlagGroup`` bundles on
   ``/platform`` and assigns each tenant to one. A tenant's available flags derive
   from its group **live** — editing the group updates every tenant on it. A
   tenant with no group falls back to the single ``is_default`` group.
2. **Per-tenant override.** A super-admin may force one flag on/off for one tenant
   as an exception; the explicit override wins over the group.
3. **Enable tier.** Whenever a flag is available it is ON by default, and the
   community's STAFF may switch it off (a sticky per-tenant choice).

``effective``:
- ``available`` = override if set, else ``flag in (tenant group | default group)``.
- ``enabled``   = override if set, else ``True`` when available (community opt-out).
- ``is_enabled`` (what every gate reads) = available AND enabled.

The read helpers tolerate *no tenant in scope* (the platform surface) by
returning "off" instead of raising. Management writes are super-admin- or
STAFF-gated and audited.
"""

import contextvars
from typing import Any, Dict, List, Optional, Set

from application.errors import FeatureDisabledError, require_found
from application.feature_flags import FEATURE_FLAG_REGISTRY, all_specs, spec_for
from application.repositories.feature_flag_group_repository import FeatureFlagGroupRepository
from application.repositories.feature_flag_repository import TenantFeatureFlagRepository
from application.repositories.tenant_repository import TenantRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import get_current_tenant_id, require_tenant_id
from models import FeatureFlag, FeatureFlagGroup, Role, User

# Registry keys, for validating group flag lists and ignoring legacy keys.
_VALID_KEYS = {flag.value for flag in FeatureFlag}

# Per-request cache of the live flag set, keyed by tenant id. Resolving one flag
# costs three queries (override + tenant + group), and enforcement now sits on
# every gated service entry point, so an un-cached read would multiply queries by
# the number of guarded calls in a request. A contextvar (not a module dict) is
# what makes this safe: it is per-task, so one user's tenant state can never be
# read by another's request — the module-level-state trap that plain caching here
# would walk straight into. Writes clear it; ``BackgroundLoop`` runs each worker
# tick in a fresh context, so a worker re-reads flags every tick.
_flag_cache: contextvars.ContextVar[Optional[Dict[int, Set[FeatureFlag]]]] = (
    contextvars.ContextVar('feature_flag_cache', default=None)
)


def reset_flag_cache() -> None:
    """Drop the per-request live-flag cache.

    Called after any write that changes effective state (group edit, override,
    STAFF toggle) so a later read in the same request sees the new value, and
    available to tests that mutate flags mid-request.
    """
    _flag_cache.set(None)


class FeatureFlagService:
    """Reads + super-admin group/override management of per-tenant feature flags."""

    def __init__(self) -> None:
        self.repository = TenantFeatureFlagRepository()
        self.group_repository = FeatureFlagGroupRepository()
        self.audit_service = AuditService()

    async def _group_flag_keys(self, tenant_id: int) -> Set[str]:
        """The flags a tenant's group grants (its group, else the default group)."""
        tenant = await TenantRepository.get_by_id(tenant_id)
        group: Optional[FeatureFlagGroup] = None
        if tenant is not None and tenant.feature_group_id is not None:
            group = await self.group_repository.get_by_id(tenant.feature_group_id)
        if group is None:
            group = await self.group_repository.get_default()
        if group is None:
            return set()
        return {key for key in (group.flags or []) if key in _VALID_KEYS}

    @staticmethod
    def _effective_available(flag_value: str, override, group_keys: Set[str]) -> bool:
        if override is not None and override.available is not None:
            return override.available
        return flag_value in group_keys

    @staticmethod
    def _effective_enabled(available: bool, override) -> bool:
        if not available:
            return False
        if override is not None and override.enabled is not None:
            return override.enabled
        return True  # available ⇒ on by default; community may opt out

    async def is_enabled(self, flag: FeatureFlag) -> bool:
        """Whether ``flag`` is live for the ambient tenant (available AND enabled).

        Reads the cached live set, so N flag checks in one request cost one pass.
        """
        return flag in await self.enabled_flags()

    async def enabled_flags(self) -> Set[FeatureFlag]:
        """The set of flags live for the ambient tenant, in one pass.

        Memoized per request (see ``_flag_cache``); the uncached resolution is
        :meth:`_resolve_enabled_flags`.
        """
        tid = get_current_tenant_id()
        if tid is None:
            return set()
        cache = _flag_cache.get()
        if cache is None:
            cache = {}
            _flag_cache.set(cache)
        if tid not in cache:
            cache[tid] = await self._resolve_enabled_flags(tid)
        return cache[tid]

    async def _resolve_enabled_flags(self, tenant_id: int) -> Set[FeatureFlag]:
        """Compute the live flag set for ``tenant_id`` from the database."""
        overrides = await self.repository.map_for_tenant(tenant_id)
        group_keys = await self._group_flag_keys(tenant_id)
        live: Set[FeatureFlag] = set()
        for flag in FEATURE_FLAG_REGISTRY:
            override = overrides.get(flag.value)
            available = self._effective_available(flag.value, override, group_keys)
            if self._effective_enabled(available, override):
                live.add(flag)
        return live

    async def ensure_enabled(self, flag: FeatureFlag) -> None:
        """Raise :class:`FeatureDisabledError` unless ``flag`` is live here.

        The service-layer half of feature gating: entry surfaces hide a disabled
        feature, and this makes the service refuse to act on it even when reached
        by a caller that forgot (or never had) a gate — a new page, a Discord
        handler, a worker. Guard a service's public entry methods with
        :func:`application.feature_flags.requires_feature` rather than calling
        this by hand where a decorator will do.
        """
        if not await self.is_enabled(flag):
            raise FeatureDisabledError(
                f"The {spec_for(flag).label} feature is not enabled for this community."
            )

    async def list_for_tenant_admin(self, actor: Optional[User]) -> List[Dict[str, Any]]:
        """Every flag's effective state for the ambient tenant (admin Features tab)."""
        await self._ensure_staff(actor)
        tid = get_current_tenant_id()
        overrides = await self.repository.map_for_tenant(tid) if tid is not None else {}
        group_keys = await self._group_flag_keys(tid) if tid is not None else set()
        result = []
        for spec in all_specs():
            override = overrides.get(spec.flag.value)
            available = self._effective_available(spec.flag.value, override, group_keys)
            enabled = self._effective_enabled(available, override)
            result.append({
                'flag': spec.flag.value,
                'label': spec.label,
                'description': spec.description,
                'category': spec.category,
                'available': available,
                'enabled': enabled,
                'live': available and enabled,
            })
        return result

    async def current_tenant_group_name(self) -> Optional[str]:
        """The display name of the ambient tenant's tier (its group, else default)."""
        tid = get_current_tenant_id()
        if tid is None:
            return None
        tenant = await TenantRepository.get_by_id(tid)
        if tenant is not None and tenant.feature_group_id is not None:
            group = await self.group_repository.get_by_id(tenant.feature_group_id)
            if group is not None:
                return group.name
        default = await self.group_repository.get_default()
        return f'{default.name} (default)' if default is not None else None

    async def set_tenant_enabled(
        self, actor: Optional[User], flag: FeatureFlag, enabled: bool
    ) -> None:
        await self._ensure_staff(actor)
        # A scoped write: fail loud if no tenant is in scope (the safety net),
        # rather than silently attempting a NULL-tenant insert.
        tid = require_tenant_id()
        override = await self.repository.get_for_tenant(tid, flag.value)
        group_keys = await self._group_flag_keys(tid)
        if not self._effective_available(flag.value, override, group_keys):
            raise ValueError(
                f"The {spec_for(flag).label} feature is not available for this "
                'community. Ask a platform administrator to enable it.'
            )
        await self.repository.set_override(tid, flag.value, enabled=enabled)
        reset_flag_cache()
        await self.audit_service.write_log(
            actor,
            AuditActions.FEATURE_FLAG_ENABLED if enabled else AuditActions.FEATURE_FLAG_DISABLED,
            {'flag': flag.value},
        )

    async def list_for_tenant(self, actor: Optional[User], tenant_id: int) -> List[Dict[str, Any]]:
        """Every flag's state for a chosen tenant, for the /platform surface.

        Exposes both the group-derived availability and the per-tenant override so
        the UI can render an Inherit / Force-on / Force-off tri-state.
        """
        await self._ensure_super_admin(actor)
        overrides = await self.repository.map_for_tenant(tenant_id)
        group_keys = await self._group_flag_keys(tenant_id)
        result = []
        for spec in all_specs():
            override = overrides.get(spec.flag.value)
            override_available = override.available if override is not None else None
            group_available = spec.flag.value in group_keys
            available = self._effective_available(spec.flag.value, override, group_keys)
            enabled = self._effective_enabled(available, override)
            result.append({
                'flag': spec.flag.value,
                'label': spec.label,
                'description': spec.description,
                'category': spec.category,
                'group_available': group_available,
                'override': override_available,   # None = inherit, True/False = forced
                'available': available,
                'enabled': enabled,
                'live': available and enabled,
            })
        return result

    async def set_availability(
        self, actor: Optional[User], tenant_id: int, flag: FeatureFlag, available: Optional[bool]
    ) -> None:
        """Set a per-tenant availability override.

        ``available``: ``True``/``False`` forces the flag on/off for this tenant;
        ``None`` clears the override so availability inherits from the group again.
        The community's ``enabled`` choice is untouched.
        """
        await self._ensure_super_admin(actor)
        await self.repository.set_override(tenant_id, flag.value, available=available)
        reset_flag_cache()
        await self.audit_service.write_log(
            actor, AuditActions.FEATURE_FLAG_AVAILABILITY_SET,
            {'tenant_id': tenant_id, 'flag': flag.value, 'available': available},
        )

    async def list_groups(self, actor: Optional[User]) -> List[FeatureFlagGroup]:
        await self._ensure_super_admin(actor)
        return await self.group_repository.list_all()

    async def get_group(self, actor: Optional[User], group_id: int) -> FeatureFlagGroup:
        await self._ensure_super_admin(actor)
        return await self._require_group(group_id)

    async def list_groups_with_counts(self, actor: Optional[User]) -> List[Dict[str, Any]]:
        """Groups plus how many tenants are assigned to each (for the /platform table)."""
        await self._ensure_super_admin(actor)
        groups = await self.group_repository.list_all()
        return [
            {
                'id': g.id,
                'name': g.name,
                'description': g.description or '',
                'flags': self._clean_flags(g.flags),
                'is_default': g.is_default,
                'tenant_count': await self.group_repository.count_tenants(g.id),
            }
            for g in groups
        ]

    async def create_group(
        self,
        actor: Optional[User],
        *,
        name: str,
        flags: List[str],
        description: Optional[str] = None,
        is_default: bool = False,
    ) -> FeatureFlagGroup:
        await self._ensure_super_admin(actor)
        name = (name or '').strip()
        if not name:
            raise ValueError('A group name is required')
        if await self.group_repository.get_by_name(name) is not None:
            raise ValueError(f"A group named '{name}' already exists")
        clean_flags = self._clean_flags(flags)
        group = await self.group_repository.create(
            name=name,
            description=(description or '').strip() or None,
            flags=clean_flags,
            is_default=is_default,
        )
        if is_default:
            # Becoming the default tier changes availability for every *ungrouped*
            # tenant, so the cached live set is stale even though no tenant row moved.
            await self.group_repository.clear_default(exclude_id=group.id)
            reset_flag_cache()
        await self.audit_service.write_log(
            actor, AuditActions.FEATURE_GROUP_CREATED,
            {'group_id': group.id, 'name': group.name, 'flags': clean_flags, 'is_default': is_default},
        )
        return group

    async def update_group(
        self,
        actor: Optional[User],
        group_id: int,
        *,
        name: Optional[str] = None,
        flags: Optional[List[str]] = None,
        description: Optional[str] = None,
        is_default: Optional[bool] = None,
    ) -> FeatureFlagGroup:
        await self._ensure_super_admin(actor)
        group = await self._require_group(group_id)
        changes: Dict[str, Any] = {}
        if name is not None:
            new_name = (name or '').strip()
            if not new_name:
                raise ValueError('A group name is required')
            existing = await self.group_repository.get_by_name(new_name)
            if existing is not None and existing.id != group.id:
                raise ValueError(f"A group named '{new_name}' already exists")
            changes['name'] = new_name
        if description is not None:
            changes['description'] = (description or '').strip() or None
        if flags is not None:
            changes['flags'] = self._clean_flags(flags)
        if is_default is not None:
            if group.is_default and not is_default:
                # Un-defaulting the sole default group would leave every ungrouped
                # tenant with no availability tier — refuse unless another exists.
                await self._ensure_another_default_exists(group.id)
            changes['is_default'] = is_default
        group = await self.group_repository.update(group, **changes)
        reset_flag_cache()
        if changes.get('is_default'):
            await self.group_repository.clear_default(exclude_id=group.id)
        await self.audit_service.write_log(
            actor, AuditActions.FEATURE_GROUP_UPDATED,
            {'group_id': group.id, 'changed_fields': list(changes.keys()), **changes},
        )
        return group

    async def delete_group(self, actor: Optional[User], group_id: int) -> None:
        await self._ensure_super_admin(actor)
        group = await self._require_group(group_id)
        if group.is_default:
            # Deleting the sole default group would leave every ungrouped tenant
            # with no availability tier, silently disabling group-derived
            # features platform-wide — refuse unless another default exists.
            await self._ensure_another_default_exists(group.id)
        # Reassign tenants on this group to ungrouped (→ default fallback). The FK
        # is ON DELETE SET NULL too, but doing it explicitly keeps behavior
        # identical across backends (SQLite in tests doesn't enforce FKs).
        await TenantRepository.clear_feature_group(group.id)
        name = group.name
        await self.group_repository.delete(group)
        reset_flag_cache()
        # Audit after the delete commits (audit-after-state-change convention).
        await self.audit_service.write_log(
            actor, AuditActions.FEATURE_GROUP_DELETED,
            {'group_id': group_id, 'name': name},
        )

    async def assign_tenant_group(
        self, actor: Optional[User], tenant_id: int, group_id: Optional[int]
    ) -> None:
        """Assign a tenant to a group (or ``None`` → ungrouped, default fallback)."""
        await self._ensure_super_admin(actor)
        if group_id is not None:
            await self._require_group(group_id)
        await TenantRepository.set_feature_group(tenant_id, group_id)
        reset_flag_cache()
        await self.audit_service.write_log(
            actor, AuditActions.FEATURE_GROUP_ASSIGNED,
            {'tenant_id': tenant_id, 'group_id': group_id},
        )

    @staticmethod
    def _clean_flags(flags: Optional[List[str]]) -> List[str]:
        """Registry-valid keys only, de-duplicated, in registry order."""
        wanted = set(flags or [])
        return [flag.value for flag in FeatureFlag if flag.value in wanted]

    async def _require_group(self, group_id: int) -> FeatureFlagGroup:
        group = await self.group_repository.get_by_id(group_id)
        return require_found(group, 'Feature group')

    async def _ensure_another_default_exists(self, exclude_id: int) -> None:
        """Raise unless a default group other than ``exclude_id`` exists.

        Guards operations (delete, un-default) that would otherwise leave the
        platform with zero default groups, silently turning off every
        group-derived feature for ungrouped tenants.
        """
        groups = await self.group_repository.list_all()
        if not any(g.is_default and g.id != exclude_id for g in groups):
            raise ValueError(
                'Cannot leave the platform without a default feature group. '
                'Assign another group as the default first.'
            )

    @staticmethod
    async def _ensure_super_admin(actor: Optional[User]) -> None:
        await AuthService.ensure(
            await AuthService.is_super_admin(actor),
            'Only super-admins can manage feature availability',
        )

    @staticmethod
    async def _ensure_staff(actor: Optional[User]) -> None:
        allowed = await AuthService.is_super_admin(actor) or await AuthService.has_role(
            actor, Role.STAFF
        )
        await AuthService.ensure(allowed, 'Only staff can manage community features')
