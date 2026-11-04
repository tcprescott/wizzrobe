"""
Preset Library Repository - Data Access Layer

The public preset library reads across every community on purpose: a preset is
named ``space/preset`` and anyone signed in may browse, roll or copy one that
its owner listed. Every method that reads another community's rows says
"cross-tenant" so the scoping guardrail lets it through; writes stay stamped
with the current tenant.

Also owns the invariant that every community preset sits in its community's
space: a ``pre_save`` signal fills ``space_id`` from the preset's tenant, so the
many paths that create presets (editor, imports, fixtures) never have to.
"""

from typing import List, Optional

from tortoise.expressions import F, Q
from tortoise.signals import pre_save

from application.repositories._tenant import current_tenant_id
from models import Preset, PresetSpace, PresetSpaceKind, Tenant


async def community_space_id(tenant_id: int) -> int:
    """The id of a community's preset space, creating it on first use (cross-tenant by id)."""
    space = await PresetSpace.get_or_none(tenant_id=tenant_id)
    if space is not None:
        return space.id
    tenant = await Tenant.get(id=tenant_id)
    slug = tenant.slug
    if await PresetSpace.exists(slug=slug):
        slug = f"{slug}-{tenant_id}"
    space = await PresetSpace.create(slug=slug, kind=PresetSpaceKind.COMMUNITY, tenant_id=tenant_id)
    return space.id


@pre_save(Preset)
async def _stamp_space(sender, instance: Preset, using_db, update_fields) -> None:
    if instance.space_id is None and instance.tenant_id is not None:
        instance.space_id = await community_space_id(instance.tenant_id)


_RELATED = ('space', 'space__tenant', 'space__owner', 'copied_from', 'copied_from__space')


class PresetLibraryRepository:
    """Reads and the few writes behind the public preset library."""

    @staticmethod
    async def list_library(
        *, include_unlisted_of_tenant: Optional[int], randomizer: Optional[str] = None,
    ) -> List[Preset]:
        """Every listed preset in every space, cross-tenant by design.

        ``include_unlisted_of_tenant`` also returns that community's unlisted
        presets, for the staff who may see them.
        """
        visible = Q(listed=True)
        if include_unlisted_of_tenant is not None:
            visible = visible | Q(tenant_id=include_unlisted_of_tenant)
        qs = Preset.filter(visible, space_id__isnull=False)
        if randomizer:
            qs = qs.filter(randomizer=randomizer)
        return await qs.prefetch_related(*_RELATED).order_by('-roll_count', 'name')

    @staticmethod
    async def get(preset_id: int) -> Optional[Preset]:
        """One preset by id from any space (cross-tenant; the service decides visibility)."""
        return await Preset.get_or_none(id=preset_id).prefetch_related(*_RELATED)

    @staticmethod
    async def find_in_community(name: str, randomizer: str) -> Optional[Preset]:
        return await Preset.get_or_none(tenant_id=current_tenant_id(), randomizer=randomizer, name=name)

    @staticmethod
    async def create_copy(original: Preset, name: str, copied_at) -> Preset:
        return await Preset.create(
            tenant_id=current_tenant_id(),
            name=name,
            randomizer=original.randomizer,
            settings=original.settings,
            description=original.description,
            listed=False,
            copied_from_id=original.id,
            copied_at=copied_at,
        )

    @staticmethod
    async def count_roll(preset_id: int) -> None:
        """Bump a preset's roll count in place (cross-tenant: anyone may roll a listed preset)."""
        await Preset.filter(id=preset_id).update(roll_count=F('roll_count') + 1)
