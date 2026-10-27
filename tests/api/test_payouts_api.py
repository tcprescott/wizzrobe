"""REST API tests for the payout endpoints (api/routers/payouts.py).

Reads are gated with the writes: an unannounced split is admin data, so a plain
member gets 403 rather than a preview of who is being paid what.
"""

from decimal import Decimal

from application.tenant_context import tenant_scope
from models import FeatureFlag, Role, TenantFeatureFlag, Tournament, TournamentPayout
from tests.api_helpers import client_for, create_user_token


async def _tournament(**kwargs) -> Tournament:
    return await Tournament.create(name='ALTTPR Main', **kwargs)


class TestRead:
    async def test_staff_read_a_split_with_its_amounts(self, db, app):
        tournament = await _tournament(
            prize_pool=Decimal('1000.00'), prize_bonus=Decimal('100.00'),
        )
        winner, _ = await create_user_token(username='jem')
        await TournamentPayout.create(
            tournament=tournament, place=1, percentage=Decimal('50.00'), entrant=winner,
        )
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])

        async with client_for(app, raw) as c:
            body = (await c.get(f'/api/tournaments/{tournament.id}/payouts')).json()

        assert body['total'] == '1100.00'
        assert body['lines'][0]['amount'] == '550.00'
        assert body['lines'][0]['entrant_id'] == winner.id

    async def test_a_plain_member_is_403(self, db, app):
        tournament = await _tournament()
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get(
                f'/api/tournaments/{tournament.id}/payouts'
            )).status_code == 403

    async def test_unauthenticated_is_401(self, db, app):
        tournament = await _tournament()
        async with client_for(app) as c:
            assert (await c.get(
                f'/api/tournaments/{tournament.id}/payouts'
            )).status_code == 401

    async def test_an_unknown_tournament_is_404(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/tournaments/9999/payouts')).status_code == 404

    async def test_the_flag_being_off_is_404(self, db, app):
        tournament = await _tournament()
        await TenantFeatureFlag.filter(
            tenant_id=1, flag=FeatureFlag.PAYOUTS.value,
        ).update(available=False, enabled=False)
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.get(
                f'/api/tournaments/{tournament.id}/payouts'
            )).status_code == 404


class TestWrite:
    async def test_staff_replace_the_split(self, db, app):
        tournament = await _tournament(prize_pool=Decimal('1000.00'))
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])

        async with client_for(app, raw) as c:
            resp = await c.put(
                f'/api/tournaments/{tournament.id}/payouts',
                json={'lines': [{'place': 1, 'percentage': '60'},
                                {'place': 2, 'percentage': '40'}]},
            )

        assert resp.status_code == 200
        assert [line['amount'] for line in resp.json()['lines']] == ['600.00', '400.00']

    async def test_a_fixed_amount_line(self, db, app):
        tournament = await _tournament(prize_pool=Decimal('1490.00'))
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])

        async with client_for(app, raw) as c:
            resp = await c.put(
                f'/api/tournaments/{tournament.id}/payouts',
                json={'lines': [{'place': 1, 'amount': '650'},
                                {'place': 2, 'percentage': '10'}]},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert [(line['amount'], line['is_fixed']) for line in body['lines']] == [
            ('650.00', True), ('149.00', False),
        ]
        assert body['lines'][0]['percentage'] is None
        assert body['allocated_amount'] == '799.00'

    async def test_a_line_with_both_or_neither_is_422(self, db, app):
        tournament = await _tournament()
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            for line in ({'place': 1}, {'place': 1, 'percentage': '10', 'amount': '10'}):
                assert (await c.put(
                    f'/api/tournaments/{tournament.id}/payouts', json={'lines': [line]},
                )).status_code == 422

    async def test_shares_over_the_pool_are_400(self, db, app):
        tournament = await _tournament()
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.put(
                f'/api/tournaments/{tournament.id}/payouts',
                json={'lines': [{'place': 1, 'percentage': '80'},
                                {'place': 2, 'percentage': '40'}]},
            )).status_code == 400

    async def test_setting_the_pool(self, db, app):
        tournament = await _tournament()
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.put(
                f'/api/tournaments/{tournament.id}/prize-pool',
                json={'prize_pool': '1000', 'prize_bonus': '100'},
            )
        assert resp.status_code == 200
        assert resp.json()['total'] == '1100.00'

    async def test_a_read_only_token_cannot_write(self, db, app):
        tournament = await _tournament()
        _, raw = await create_user_token(
            username='staff', roles=[Role.STAFF], read_only=True,
        )
        async with client_for(app, raw) as c:
            assert (await c.put(
                f'/api/tournaments/{tournament.id}/payouts',
                json={'lines': [{'place': 1, 'percentage': '100'}]},
            )).status_code == 403

    async def test_a_plain_member_cannot_write(self, db, app):
        tournament = await _tournament()
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.put(
                f'/api/tournaments/{tournament.id}/prize-pool',
                json={'prize_pool': '5'},
            )).status_code == 403


class TestTenantIsolation:
    async def test_another_communitys_split_is_404(self, app, two_tenants):
        tenant_a, tenant_b = two_tenants
        with tenant_scope(tenant_b.id):
            theirs = await Tournament.create(name='B Cup', prize_pool=Decimal('50'))
        with tenant_scope(tenant_a.id):
            _, raw = await create_user_token(username='staff', roles=[Role.STAFF])

        async with client_for(app, raw) as c:
            assert (await c.get(
                f'/api/tournaments/{theirs.id}/payouts'
            )).status_code == 404
