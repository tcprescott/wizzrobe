"""Rolling seeds into an async-qualifier pool's permalinks.

Split from ``test_async_qualifier_service.py`` for length. These are the tests
about the *roll* rather than about the qualifier lifecycle: what happens when the
randomizer's credential is missing, and what happens when the randomizer cannot
fill a pool at all.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.async_qualifier.async_qualifier_service import AsyncQualifierService
from models import AsyncQualifierPermalink, Role, User, UserRole


async def test_roll_permalinks_blocked_when_credential_missing(db):
    # Not dk64r: it's refused earlier for being async. A fresh tenant with ASYNC_QUALIFIERS on,
    # so the missing credential, not a sibling's credential or the flag guard, is what refuses.
    from application.tenant_context import tenant_scope
    from models import FeatureFlag, Preset, Tenant, TenantFeatureFlag

    service = AsyncQualifierService()
    b = await Tenant.create(name='NoDK', slug='no-dk-q')
    await TenantFeatureFlag.create(
        tenant_id=b.id, flag=FeatureFlag.ASYNC_QUALIFIERS.value,
        available=True, enabled=True,
    )
    with tenant_scope(b.id):
        staff = await User.create(discord_id=900500, username='qstaff')
        await UserRole.create(user=staff, role=Role.STAFF)
        preset = await Preset.create(
            name='OOT', randomizer='ootr', settings={'settings_string': 'x'},
        )
        now = datetime.now(timezone.utc)
        q = await service.create_qualifier(
            staff, name='Q', opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=1), runs_per_pool=1,
        )
        pool = await service.create_pool(staff, q.id, name='Pool A', preset_id=preset.id)

        with pytest.raises(ValueError, match='OoT Randomizer API key is not configured'):
            await service.roll_permalinks(staff, pool.id, count=2)

        assert await AsyncQualifierPermalink.filter(pool_id=pool.id).count() == 0


async def test_roll_permalinks_refuses_async_randomizer(db):
    from application.tenant_context import tenant_scope
    from models import FeatureFlag, Preset, Tenant, TenantFeatureFlag

    service = AsyncQualifierService()
    b = await Tenant.create(name='AsyncQ', slug='async-q')
    await TenantFeatureFlag.create(
        tenant_id=b.id, flag=FeatureFlag.ASYNC_QUALIFIERS.value,
        available=True, enabled=True,
    )
    with tenant_scope(b.id):
        staff = await User.create(discord_id=900501, username='qstaff2')
        await UserRole.create(user=staff, role=Role.STAFF)
        preset = await Preset.create(
            name='DK', randomizer='dk64r', settings={'settings_string': 'x'},
        )
        now = datetime.now(timezone.utc)
        q = await service.create_qualifier(
            staff, name='Q', opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=1), runs_per_pool=1,
        )
        pool = await service.create_pool(staff, q.id, name='Pool A', preset_id=preset.id)

        with pytest.raises(ValueError, match='rolls seeds asynchronously'):
            await service.roll_permalinks(staff, pool.id, count=2)

        from models import AsyncQualifierPermalink
        assert await AsyncQualifierPermalink.all().count() == 0


async def test_rolled_permalinks_keep_the_seed_code(db, monkeypatch):
    from application.services._seedgen_types import RolledSeed
    from application.services.seedgen_service import SeedGenerationService
    from application.tenant_context import tenant_scope
    from application.utils.seed_provider import ProviderCall
    from models import FeatureFlag, GeneratedSeeds, Preset, Tenant, TenantFeatureFlag

    async def fake_call(self, randomizer, preset, surface=None):
        return ProviderCall(
            value=RolledSeed(url='https://seed.test/1', code=['Bow', 'Hookshot']),
            provider=randomizer, operation='generate_seed',
        )

    monkeypatch.setattr(SeedGenerationService, 'generate_seed_call', fake_call)
    service = AsyncQualifierService()
    b = await Tenant.create(name='Codes', slug='codes-q')
    await TenantFeatureFlag.create(
        tenant_id=b.id, flag=FeatureFlag.ASYNC_QUALIFIERS.value,
        available=True, enabled=True,
    )
    with tenant_scope(b.id):
        staff = await User.create(discord_id=900501, username='qstaff2')
        await UserRole.create(user=staff, role=Role.STAFF)
        preset = await Preset.create(name='Z3', randomizer='alttpr', settings={})
        now = datetime.now(timezone.utc)
        q = await service.create_qualifier(
            staff, name='Q', opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=1), runs_per_pool=1,
        )
        pool = await service.create_pool(staff, q.id, name='Pool A', preset_id=preset.id)

        await service.roll_permalinks(staff, pool.id, count=1)

        seed = await GeneratedSeeds.get(seed_url='https://seed.test/1')
        assert seed.seed_code == ['Bow', 'Hookshot']
