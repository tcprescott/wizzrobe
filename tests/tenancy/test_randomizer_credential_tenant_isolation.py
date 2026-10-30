"""Cross-tenant isolation (leak) test for ``RandomizerCredential``.

Randomizer credentials are the strongest reason multitenancy has to hold here:
one community must never roll a seed on another's key. Each tenant sees only its
own rows, and a tenant with no key of its own gets the "not configured" refusal
rather than a neighbour's value.
"""

import pytest

from application.repositories.randomizer_credential_repository import (
    RandomizerCredentialRepository,
)
from application.services.randomizer_credential_service import RandomizerCredentialService
from application.services.seedgen_service import SeedGenerationService
from application.tenant_context import tenant_scope
from models import RandomizerCredential


async def test_credential_reads_are_isolated(two_tenants):
    a, b = two_tenants
    repo = RandomizerCredentialRepository()
    with tenant_scope(a.id):
        ca = await RandomizerCredential.create(
            randomizer='ootr', key='api_key', value='tenant-a-value',
        )
    with tenant_scope(b.id):
        cb = await RandomizerCredential.create(
            randomizer='ootr', key='api_key', value='tenant-b-value',
        )

    with tenant_scope(a.id):
        assert [c.id for c in await repo.list_all()] == [ca.id]
        assert (await repo.get_by_natural_key('ootr', 'api_key')).value == 'tenant-a-value'
        assert await repo.get_by_id(cb.id) is None
    with tenant_scope(b.id):
        assert [c.id for c in await repo.list_all()] == [cb.id]
        assert (await repo.get_by_natural_key('ootr', 'api_key')).value == 'tenant-b-value'
        assert await repo.get_by_id(ca.id) is None


async def test_a_tenant_never_rolls_on_another_tenants_key(two_tenants):
    a, b = two_tenants
    service = SeedGenerationService()
    with tenant_scope(a.id):
        await RandomizerCredential.create(
            randomizer='dk64r', key='api_key', value='tenant-a-only',
        )

    with tenant_scope(a.id):
        assert await service._credential('dk64r', 'api_key') == 'tenant-a-only'
    with tenant_scope(b.id):
        with pytest.raises(ValueError, match='DK64 Randomizer API key is not configured'):
            await service._credential('dk64r', 'api_key')


async def test_selector_availability_is_per_tenant(two_tenants):
    a, b = two_tenants
    credentials = RandomizerCredentialService()
    with tenant_scope(a.id):
        await RandomizerCredential.create(
            randomizer='ootr', key='api_key', value='tenant-a-only',
        )

    with tenant_scope(a.id):
        available = SeedGenerationService.available_randomizers(
            await credentials.configured_randomizers()
        )
        assert 'ootr' in available
    with tenant_scope(b.id):
        available = SeedGenerationService.available_randomizers(
            await credentials.configured_randomizers()
        )
        assert 'ootr' not in available
