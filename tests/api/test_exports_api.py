"""/api/exports: queue, poll, download; and nobody reads another person's export."""

import io
import zipfile

from application.services.exports import data_export_worker
from application.tenant_context import tenant_scope
from models import Role, Tournament
from tests.api_helpers import client_for, create_user_token


async def test_staff_queue_poll_and_download_a_tournament_export(db, app):
    _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    await Tournament.create(name='Zelda Cup')
    async with client_for(app, raw) as c:
        queued = await c.post('/api/exports/tournaments', json={})
        assert queued.status_code == 202, queued.text
        export = queued.json()
        assert export['status'] == 'queued'
        assert export['has_json'] is True

        assert (await c.get(f"/api/exports/{export['id']}/download")).status_code == 400

        await data_export_worker._tick()
        assert (await c.get(f"/api/exports/{export['id']}")).json()['status'] == 'ready'

        zipped = await c.get(f"/api/exports/{export['id']}/download")
        assert zipped.status_code == 200
        assert zipped.headers['content-type'] == 'application/zip'
        assert 'tournaments.json' in zipfile.ZipFile(io.BytesIO(zipped.content)).namelist()

        doc = (await c.get(f"/api/exports/{export['id']}/download", params={'format': 'json'})).json()
        assert doc['tournaments'][0]['name'] == 'Zelda Cup'

        assert [e['id'] for e in (await c.get('/api/exports')).json()['items']] == [export['id']]


async def test_a_player_cannot_export_or_read_others_exports(db, app):
    _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
    _, player_raw = await create_user_token(username='player', discord_id=4242)
    await Tournament.create(name='Zelda Cup')
    async with client_for(app, staff_raw) as c:
        export_id = (await c.post('/api/exports/tournaments', json={})).json()['id']
    async with client_for(app, player_raw) as c:
        assert (await c.post('/api/exports/tournaments', json={})).status_code == 403
        assert (await c.get(f'/api/exports/{export_id}')).status_code == 404
        assert (await c.get('/api/exports')).json()['items'] == []


async def test_other_communities_tournaments_are_not_exportable(db, app, two_tenants):
    _, b = two_tenants
    _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
    with tenant_scope(b.id):
        other = await Tournament.create(name='B only', tenant_id=b.id)
    async with client_for(app, raw) as c:
        refused = await c.post('/api/exports/tournaments', json={'tournament_ids': [other.id]})
        assert refused.status_code == 403
