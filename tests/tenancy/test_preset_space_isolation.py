"""Tenant isolation for preset spaces and the cross-community preset library.

``PresetSpace`` is global (its slug names presets everywhere), but a community
space belongs to one tenant, and the library is the one deliberate cross-tenant
read: it may show another community's *listed* presets and nothing else.
"""

from application.repositories import PresetLibraryRepository, PresetRepository
from application.tenant_context import tenant_scope
from models import Preset, PresetSpace, Tenant


async def test_each_community_gets_its_own_space_and_list(db):
    a = await Tenant.get(id=1)
    b = await Tenant.create(slug='beta', name='Beta')
    with tenant_scope(a.id):
        pa = await Preset.create(name='open', randomizer='alttpr', settings={}, listed=False)
    with tenant_scope(b.id):
        pb = await Preset.create(name='open', randomizer='alttpr', settings={}, listed=False)

    space_a = await PresetSpace.get(tenant_id=a.id)
    space_b = await PresetSpace.get(tenant_id=b.id)
    assert (pa.space_id, pb.space_id) == (space_a.id, space_b.id)
    assert space_b.slug == 'beta'

    with tenant_scope(a.id):
        assert [p.id for p in await PresetRepository().list_all()] == [pa.id]
    with tenant_scope(b.id):
        assert [p.id for p in await PresetRepository().list_all()] == [pb.id]


async def test_library_never_shows_another_communitys_unlisted_presets(db):
    b = await Tenant.create(slug='beta', name='Beta')
    with tenant_scope(b.id):
        hidden = await Preset.create(name='secret', randomizer='alttpr', settings={}, listed=False)
        shown = await Preset.create(name='public', randomizer='alttpr', settings={}, listed=True)
    with tenant_scope(1):
        mine = await Preset.create(name='ours', randomizer='alttpr', settings={}, listed=False)
        ids = {p.id for p in await PresetLibraryRepository.list_library(include_unlisted_of_tenant=1)}
    assert shown.id in ids and mine.id in ids
    assert hidden.id not in ids
