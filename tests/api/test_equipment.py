"""REST API tests for the equipment endpoints (api/routers/equipment.py).

Reads take any token, matching the home Equipment tab — the surface these
mirror is ungated by role. ``private_notes`` is the exception and is asserted
here per-caller, because it is the only field the web hides from a plain member.
Writes are re-gated in ``EquipmentService``.
"""

from application.tenant_context import tenant_scope
from models import (
    Equipment,
    EquipmentLoan,
    EquipmentStatus,
    FeatureFlag,
    Role,
    TenantFeatureFlag,
)
from tests.api_helpers import client_for, create_user_token


async def _asset(asset_number=1, name='Console', **kwargs):
    return await Equipment.create(asset_number=asset_number, name=name, **kwargs)


class TestReads:
    async def test_list_is_for_equipment_managers(self, db, app):
        _, raw = await create_user_token(username='em', roles=[Role.EQUIPMENT_MANAGER])
        await _asset(1, 'CRT')
        await _asset(2, 'SNES')
        async with client_for(app, raw) as c:
            resp = await c.get('/api/equipment')
            assert resp.status_code == 200
            assert {a['name'] for a in resp.json()} == {'CRT', 'SNES'}

    async def test_list_is_refused_to_a_plain_member(self, db, app):
        """The register is the admin tab's; members look one asset up from its QR page."""
        _, raw = await create_user_token(username='plain')
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/equipment')).status_code == 403
            assert (await c.get(f'/api/equipment/{asset.id}')).status_code == 200

    async def test_private_notes_are_hidden_from_a_plain_member(self, db, app):
        _, raw = await create_user_token(username='plain')
        asset = await _asset(1, 'CRT', private_notes='cracked case')
        async with client_for(app, raw) as c:
            detail = (await c.get(f'/api/equipment/{asset.id}')).json()
            assert detail['private_notes'] is None

    async def test_private_notes_are_returned_to_staff(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        asset = await _asset(1, 'CRT', private_notes='cracked case')
        async with client_for(app, raw) as c:
            detail = (await c.get(f'/api/equipment/{asset.id}')).json()
            assert detail['private_notes'] == 'cracked case'

    async def test_status_filter_narrows_the_inventory(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        await _asset(1, 'CRT')
        await _asset(2, 'Retired CRT', status=EquipmentStatus.RETIRED)
        async with client_for(app, raw) as c:
            resp = await c.get('/api/equipment', params={'status': 'retired'})
            assert [a['name'] for a in resp.json()] == ['Retired CRT']

    async def test_unknown_status_is_422(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/equipment', params={'status': 'nope'})).status_code == 422

    async def test_detail_reports_the_current_loan(self, db, app):
        borrower, raw = await create_user_token(username='vol', roles=[Role.VOLUNTEER])
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/equipment/{asset.id}/checkout')).status_code == 201
            detail = (await c.get(f'/api/equipment/{asset.id}')).json()
            assert detail['status'] == 'checked_out'
            assert detail['borrower_name'] == borrower.preferred_name
            assert detail['current_loan']['borrower_id'] == borrower.id
            assert detail['current_loan']['checked_in_at'] is None

    async def test_detail_has_no_current_loan_once_checked_in(self, db, app):
        _, raw = await create_user_token(username='mgr', roles=[Role.EQUIPMENT_MANAGER])
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            await c.post(f'/api/equipment/{asset.id}/checkout')
            await c.post(f'/api/equipment/{asset.id}/checkin')
            detail = (await c.get(f'/api/equipment/{asset.id}')).json()
            assert detail['current_loan'] is None
            assert detail['borrower_name'] is None

    async def test_loan_history_is_newest_first_and_bounded(self, db, app):
        _, raw = await create_user_token(username='mgr', roles=[Role.EQUIPMENT_MANAGER])
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            for _ in range(2):
                await c.post(f'/api/equipment/{asset.id}/checkout')
                await c.post(f'/api/equipment/{asset.id}/checkin')
            history = (await c.get(f'/api/equipment/{asset.id}/loans')).json()
            assert len(history) == 2
            assert history[0]['checked_out_at'] >= history[1]['checked_out_at']
            bounded = (
                await c.get(f'/api/equipment/{asset.id}/loans', params={'limit': 1})
            ).json()
            assert len(bounded) == 1

    async def test_my_checkouts_lists_only_your_own(self, db, app):
        _, mine = await create_user_token(username='a', roles=[Role.VOLUNTEER])
        _, theirs = await create_user_token(username='b', roles=[Role.VOLUNTEER])
        asset = await _asset(1, 'CRT')
        async with client_for(app, mine) as c:
            await c.post(f'/api/equipment/{asset.id}/checkout')
            rows = (await c.get('/api/equipment/me/checkouts')).json()
            assert [row['name'] for row in rows] == ['CRT']
        async with client_for(app, theirs) as c:
            assert (await c.get('/api/equipment/me/checkouts')).json() == []

    async def test_unknown_asset_is_404(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/equipment/9999')).status_code == 404

    async def test_unauthenticated_is_401(self, db, app):
        async with client_for(app) as c:
            assert (await c.get('/api/equipment')).status_code == 401


class TestWrites:
    async def test_manager_creates_updates_and_deletes(self, db, app):
        _, raw = await create_user_token(username='mgr', roles=[Role.EQUIPMENT_MANAGER])
        async with client_for(app, raw) as c:
            created = await c.post('/api/equipment', json={'name': 'CRT', 'private_notes': 'n'})
            assert created.status_code == 201
            body = created.json()
            assert body['asset_number'] == 1
            assert body['private_notes'] == 'n'

            updated = await c.put(
                f"/api/equipment/{body['id']}",
                json={'name': 'CRT 20in', 'status': 'retired'},
            )
            assert updated.status_code == 200
            assert updated.json()['name'] == 'CRT 20in'
            assert updated.json()['status'] == 'retired'
            assert updated.json()['private_notes'] is None

            assert (await c.delete(f"/api/equipment/{body['id']}")).status_code == 204
            assert (await c.get('/api/equipment')).json() == []

    async def test_bulk_create_numbers_assets_consecutively(self, db, app):
        _, raw = await create_user_token(username='mgr', roles=[Role.EQUIPMENT_MANAGER])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/equipment/bulk', json={'name': 'Controller', 'count': 3})
            assert resp.status_code == 201
            body = resp.json()
            assert [a['asset_number'] for a in body] == [1, 2, 3]
            assert all(a['id'] for a in body)

    async def test_bulk_count_is_bounded(self, db, app):
        _, raw = await create_user_token(username='mgr', roles=[Role.EQUIPMENT_MANAGER])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/equipment/bulk', json={'name': 'X', 'count': 500})
            assert resp.status_code == 422

    async def test_plain_member_cannot_manage_assets(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.post('/api/equipment', json={'name': 'CRT'})).status_code == 403

    async def test_read_only_token_cannot_write(self, db, app):
        _, raw = await create_user_token(
            username='mgr', roles=[Role.EQUIPMENT_MANAGER], read_only=True,
        )
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            assert (await c.post('/api/equipment', json={'name': 'X'})).status_code == 403
            assert (await c.post(f'/api/equipment/{asset.id}/checkout')).status_code == 403

    async def test_naming_another_borrower_is_refused_for_a_non_manager(self, db, app):
        _, raw = await create_user_token(username='vol', roles=[Role.VOLUNTEER])
        other, _ = await create_user_token(username='other')
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            resp = await c.post(
                f'/api/equipment/{asset.id}/checkout', json={'borrower_id': other.id},
            )
            assert resp.status_code == 403

    async def test_a_member_with_no_roles_borrows_to_themselves(self, db, app):
        """The QR-code path over HTTP: no roles, no body, checked out to them."""
        player, raw = await create_user_token(username='player')
        asset = await _asset(1, 'RetroTINK 2X')
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/equipment/{asset.id}/checkout')
            assert resp.status_code == 201
            assert resp.json()['borrower_id'] == player.id

    async def test_manager_checks_out_on_behalf_of_someone_else(self, db, app):
        _, raw = await create_user_token(username='mgr', roles=[Role.EQUIPMENT_MANAGER])
        borrower, _ = await create_user_token(username='player')
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            resp = await c.post(
                f'/api/equipment/{asset.id}/checkout', json={'borrower_id': borrower.id},
            )
            assert resp.status_code == 201
            assert resp.json()['borrower_id'] == borrower.id
            assert resp.json()['checked_in_at'] is None

    async def test_double_checkout_is_400(self, db, app):
        _, raw = await create_user_token(username='mgr', roles=[Role.EQUIPMENT_MANAGER])
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            await c.post(f'/api/equipment/{asset.id}/checkout')
            assert (await c.post(f'/api/equipment/{asset.id}/checkout')).status_code == 400

    async def test_checkin_without_an_open_loan_is_400(self, db, app):
        _, raw = await create_user_token(username='mgr', roles=[Role.EQUIPMENT_MANAGER])
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/equipment/{asset.id}/checkin')).status_code == 400

    async def test_volunteer_cannot_check_in(self, db, app):
        _, raw = await create_user_token(username='vol', roles=[Role.VOLUNTEER])
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            await c.post(f'/api/equipment/{asset.id}/checkout')
            assert (await c.post(f'/api/equipment/{asset.id}/checkin')).status_code == 403

    async def test_deleting_a_checked_out_asset_is_400(self, db, app):
        _, raw = await create_user_token(username='mgr', roles=[Role.EQUIPMENT_MANAGER])
        asset = await _asset(1, 'CRT')
        async with client_for(app, raw) as c:
            await c.post(f'/api/equipment/{asset.id}/checkout')
            assert (await c.delete(f'/api/equipment/{asset.id}')).status_code == 400


class TestFeatureGate:
    async def test_router_404s_when_the_flag_is_off(self, db, app):
        await TenantFeatureFlag.filter(
            tenant_id=1, flag=FeatureFlag.EQUIPMENT.value,
        ).update(available=False, enabled=False)
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/equipment')).status_code == 404


class TestTenantIsolation:
    async def test_another_community_can_neither_see_nor_borrow(self, app, two_tenants):
        tenant_a, tenant_b = two_tenants
        with tenant_scope(tenant_a.id):
            asset_a = await _asset(1, 'A-CRT')
        with tenant_scope(tenant_b.id):
            _, token_b = await create_user_token(username='b-staff', roles=[Role.STAFF])

        async with client_for(app, token_b) as c:
            assert (await c.get('/api/equipment')).json() == []
            assert (await c.get(f'/api/equipment/{asset_a.id}')).status_code == 404
            assert (await c.post(f'/api/equipment/{asset_a.id}/checkout')).status_code == 404
        assert await EquipmentLoan.all().count() == 0
