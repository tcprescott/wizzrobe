"""
Preset Repository - Data Access Layer

Handles database operations for tenant-scoped seed-rolling presets.
"""

from typing import Any, Dict, List, Optional

from tortoise.expressions import Q

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import Preset, Tournament


class PresetRepository(TenantScopedRepository[Preset]):
    """Repository for Preset data access."""

    model = Preset

    async def get_by_natural_key(self, randomizer: str, name: str) -> Optional[Preset]:
        """Resolve a preset by its per-tenant natural key (randomizer, name)."""
        return await Preset.get_or_none(
            tenant_id=current_tenant_id(), randomizer=randomizer, name=name
        )

    async def list_all(self) -> List[Preset]:
        return await scoped(Preset.all()).order_by('randomizer', 'name')

    async def list_by_randomizer(self, randomizer: str) -> List[Preset]:
        return await scoped(Preset.filter(randomizer=randomizer)).order_by('name')

    async def tournaments_using_presets(self) -> List[Dict[str, Any]]:
        """Every tournament, in any event, that names a preset as its main or hard-mode one.

        Cross-event: presets are the community's, and one used last year is still used.
        """
        return await scoped(
            Tournament.filter(Q(preset_id__isnull=False) | Q(hard_preset_id__isnull=False))
        ).order_by('name').values('name', 'preset_id', 'hard_preset_id')
