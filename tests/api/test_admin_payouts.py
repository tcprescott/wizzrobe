"""REST tests for the app's Admin → Payouts screen (api/routers/admin_payouts.py)."""

from decimal import Decimal

import pytest

from application.utils.clients import sglive_client
from application.utils.clients.sglive_client import SgliveGame
from models import Role, Tournament, TournamentPayout
from tests.api_helpers import client_for, create_user_token


@pytest.fixture
def sglive(monkeypatch):
    games = [SgliveGame(slug='alttpr', name='ALTTPR Main', total=Decimal('1490.00'))]

    async def fake_fetch(url=sglive_client.DATA_URL):
        return games

    monkeypatch.setattr(sglive_client, 'fetch_games', fake_fetch)
    return games


class TestPayoutsScreen:
    async def test_board_says_what_each_split_needs(self, db, app):
        ready = await Tournament.create(name='ALTTPR Main', prize_pool=Decimal('1000.00'))
        unnamed = await Tournament.create(name='Super Metroid', prize_pool=Decimal('500.00'))
        await Tournament.create(name='Zelda 1')
        winner, _ = await create_user_token(username='dana')
        await TournamentPayout.create(tournament=ready, place=1, percentage=Decimal('60.00'), entrant=winner)
        await TournamentPayout.create(tournament=unnamed, place=1, percentage=Decimal('60.00'))
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])

        async with client_for(app, raw) as c:
            r = await c.get('/api/admin/payouts/board')
        assert r.status_code == 200, r.text
        rows = {t['name']: t for t in r.json()['tournaments']}
        assert rows['ALTTPR Main']['status'] == 'ready'
        assert rows['Super Metroid']['status'] == 'unnamed'
        assert (rows['Zelda 1']['status'], rows['Zelda 1']['has_pool']) == ('none', False)

    async def test_detail_carries_lines_and_entrants(self, db, app):
        t = await Tournament.create(name='ALTTPR Main', prize_pool=Decimal('1000.00'), prize_bonus=Decimal('100.00'))
        winner, _ = await create_user_token(username='dana')
        await TournamentPayout.create(tournament=t, place=1, percentage=Decimal('50.00'), entrant=winner)
        await TournamentPayout.create(tournament=t, place=2, amount=Decimal('100.00'))
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])

        async with client_for(app, raw) as c:
            r = await c.get(f'/api/admin/payouts/{t.id}')
            export = await c.get(f'/api/admin/payouts/{t.id}/export')
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body['pool'], body['bonus']) == ('1000.00', '100.00')
        assert [(line['place'], line['amount'], line['is_fixed']) for line in body['lines']] == [
            (1, '550.00', False), (2, '100.00', True),
        ]
        assert body['entrants'] == [{'id': winner.id, 'name': 'dana'}]
        assert '1st place' in export.json()['text']

    async def test_a_plain_member_is_refused(self, db, app):
        t = await Tournament.create(name='ALTTPR Main')
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get(f'/api/admin/payouts/{t.id}')).status_code == 403
            assert (await c.get('/api/admin/payouts/board')).json()['tournaments'] == []

    async def test_link_sglive_suggests_then_fills_the_pool(self, db, app, sglive):
        t = await Tournament.create(name='ALTTPR Main')
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            links = (await c.get('/api/admin/payouts/sglive')).json()
            assert links['games'][0]['tournament_id'] == t.id and links['games'][0]['suggested'] is True
            assert (await c.put('/api/admin/payouts/sglive', json={'links': {'alttpr': t.id}})).status_code == 204
            body = (await c.get(f'/api/admin/payouts/{t.id}')).json()
            assert (body['sglive_slug'], body['pool']) == ('alttpr', '1490.00')
            assert (await c.post('/api/admin/payouts/sglive/sync')).json() == {'missing': []}
            assert (await c.delete(f'/api/admin/payouts/{t.id}/sglive')).status_code == 204
            assert (await c.get(f'/api/admin/payouts/{t.id}')).json()['sglive_slug'] is None
