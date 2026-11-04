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

from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id
from models import Preset, PresetSpace, PresetSpaceEditor, PresetSpaceKind, Tenant


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


class PresetSpaceRepository:
    """Personal preset spaces and their co-editors. Spaces are global, so every read is cross-tenant."""

    @staticmethod
    async def get(space_id: int) -> Optional[PresetSpace]:
        return await PresetSpace.get_or_none(id=space_id).prefetch_related('owner')

    @staticmethod
    async def owned_by(user_id: int) -> Optional[PresetSpace]:
        return await PresetSpace.get_or_none(owner_id=user_id, kind=PresetSpaceKind.USER)

    @staticmethod
    async def editable_by(user_id: int) -> List[PresetSpace]:
        """The personal spaces a player owns or co-edits, their own first (cross-tenant)."""
        spaces = await PresetSpace.filter(
            Q(owner_id=user_id) | Q(editors__user_id=user_id), kind=PresetSpaceKind.USER,
        ).distinct().prefetch_related('owner')
        return sorted(spaces, key=lambda s: (s.owner_id != user_id, s.slug))

    @staticmethod
    async def slug_taken(slug: str, *, except_id: Optional[int] = None) -> bool:
        qs = PresetSpace.filter(slug=slug)
        if except_id is not None:
            qs = qs.exclude(id=except_id)
        return await qs.exists()

    @staticmethod
    async def create_personal(slug: str, owner_id: int) -> PresetSpace:
        return await PresetSpace.create(slug=slug, kind=PresetSpaceKind.USER, owner_id=owner_id, tenant_id=None)

    @staticmethod
    async def rename(space: PresetSpace, slug: str) -> PresetSpace:
        space.slug = slug
        await save_fields(space, 'slug')
        return space

    @staticmethod
    async def is_editor(space_id: int, user_id: int) -> bool:
        return await PresetSpaceEditor.exists(space_id=space_id, user_id=user_id)

    @staticmethod
    async def editors(space_id: int) -> List[PresetSpaceEditor]:
        return await PresetSpaceEditor.filter(space_id=space_id).prefetch_related('user').order_by('created_at')

    @staticmethod
    async def count_editors(space_id: int) -> int:
        return await PresetSpaceEditor.filter(space_id=space_id).count()

    @staticmethod
    async def add_editor(space_id: int, user_id: int) -> PresetSpaceEditor:
        editor, _ = await PresetSpaceEditor.get_or_create(space_id=space_id, user_id=user_id)
        return editor

    @staticmethod
    async def remove_editor(space_id: int, user_id: int) -> int:
        return await PresetSpaceEditor.filter(space_id=space_id, user_id=user_id).delete()

    @staticmethod
    async def count_presets(space_id: int) -> int:
        return await Preset.filter(space_id=space_id).count()

    @staticmethod
    async def find_preset(space_id: int, randomizer: str, name: str) -> Optional[Preset]:
        return await Preset.get_or_none(space_id=space_id, randomizer=randomizer, name=name)

    @staticmethod
    async def create_preset(space_id: int, **fields) -> Preset:
        """A preset in a personal space: no community, always in the library (cross-tenant)."""
        return await Preset.create(space_id=space_id, tenant_id=None, listed=True, **fields)

    @staticmethod
    async def update_preset(preset: Preset, **changes) -> Preset:
        for key, value in changes.items():
            setattr(preset, key, value)
        await save_fields(preset, *changes.keys())
        return preset

    @staticmethod
    async def delete_preset(preset: Preset) -> None:
        await preset.delete()
