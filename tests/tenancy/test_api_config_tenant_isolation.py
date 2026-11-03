"""Cross-tenant isolation for the system-config REST endpoints.

`SystemConfiguration.name` is unique *per tenant*, so the `/api/config` reads
must scope to the caller's tenant (derived from their token). Regression test:
before scoping, `GET /api/config` leaked every tenant's config and
`GET /api/config/{key}` could `MultipleObjectsReturned`-500 or return another
tenant's value when the same key existed in more than one tenant.
"""


from application.tenant_context import tenant_scope
from models import Role, SystemConfiguration, Tenant
from tests.api_helpers import client_for, create_user_token


async def test_config_reads_are_tenant_scoped(db, app):
    _, raw = await create_user_token(roles=[Role.STAFF])
    await SystemConfiguration.create(name='shared_key', value='tenant1-value')
    await SystemConfiguration.create(name='tenant1_only', value='a')

    tenant_b = await Tenant.create(name='Tenant B', slug='tenant-b')
    with tenant_scope(tenant_b.id):
        await SystemConfiguration.create(name='shared_key', value='tenant2-value', tenant_id=tenant_b.id)
        await SystemConfiguration.create(name='tenant2_only', value='b', tenant_id=tenant_b.id)

    async with client_for(app, raw) as c:
        listed = await c.get('/api/config')
        assert listed.status_code == 200
        names = {e['name'] for e in listed.json()}
        assert names == {'shared_key', 'tenant1_only'}
        assert 'tenant2_only' not in names

        got = await c.get('/api/config/shared_key')
        assert got.status_code == 200
        assert got.json()['value'] == 'tenant1-value'

        assert (await c.get('/api/config/tenant2_only')).status_code == 404
