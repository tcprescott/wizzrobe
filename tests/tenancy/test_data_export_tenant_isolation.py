"""Exports never cross communities: a person's export in one is invisible in another."""

from application.repositories import DataExportRepository
from application.services import DataExportService
from application.tenant_context import tenant_scope
from models import DataExport, DataExportKind
from tests.factories import make_user


async def test_data_exports_do_not_leak_across_tenants(two_tenants):
    a, b = two_tenants
    person = await make_user(discord_id=762, username='in-both')
    with tenant_scope(a.id):
        mine = await DataExportService().record_table_csv(person, 'a.csv', 'a.csv', b'secret,A\n')
        await DataExportRepository.create_queued(
            requested_by_id=person.id, kind=DataExportKind.TOURNAMENTS, title='A', params={},
        )
    with tenant_scope(b.id):
        assert await DataExportService().list_mine(person) == []
        assert await DataExportRepository.summary(mine.id) is None
        assert await DataExportRepository.get_by_id(mine.id) is None
        assert await DataExportRepository.count_pending_in_tenant() == 0
        assert await DataExportRepository.count_pending_for_requester(person.id) == 0

    assert (await DataExport.get(id=mine.id)).tenant_id == a.id
