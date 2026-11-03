"""The app's Event settings panes over REST: staff read and save, everyone else is refused."""

from tests.api._bracket_api import staff_token
from tests.api_helpers import client_for, create_user_token


async def test_dates_and_hours_round_trip(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        r = await c.put('/api/event-settings/dates', json={'starts_on': '2027-10-21', 'ends_on': '2027-10-24'})
        assert r.status_code == 200
        body = r.json()
        assert body['starts_on'] == '2027-10-21'
        assert [d['day'] for d in body['days']] == ['2027-10-21', '2027-10-22', '2027-10-23', '2027-10-24']
        assert body['timezone_label']

        bad = await c.put('/api/event-settings/dates', json={'starts_on': '2027-10-24', 'ends_on': '2027-10-21'})
        assert bad.status_code == 400

        r = await c.put('/api/event-settings/hours', json={'days': [
            {'day': '2027-10-21', 'opens': '10:00', 'closes': '22:00'},
            {'day': '2027-10-22', 'opens': None, 'closes': None},
        ]})
        days = {d['day']: d for d in r.json()['days']}
        assert days['2027-10-21']['opens'] == '10:00' and days['2027-10-21']['closes'] == '22:00'
        assert days['2027-10-22']['opens'] is None

        backwards = await c.put('/api/event-settings/hours', json={'days': [
            {'day': '2027-10-21', 'opens': '22:00', 'closes': '10:00'},
        ]})
        assert backwards.status_code == 400


async def test_capacity_crew_tiers_and_clock(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        r = await c.put('/api/event-settings/capacity', json={'max_players': 40, 'max_stages': None})
        assert r.json() == {'max_players': 40, 'max_stages': None}
        assert (await c.put('/api/event-settings/capacity', json={'max_players': 0})).status_code == 422

        r = await c.put('/api/event-settings/crew', json={'broadcasters': True})
        assert r.json() == {'broadcasters': True}

        r = await c.put('/api/event-settings/reward-tiers', json={'text': '16, 8'})
        assert r.json() == {'text': '8, 16', 'tiers': [8.0, 16.0]}
        assert (await c.put('/api/event-settings/reward-tiers', json={'text': 'lots'})).status_code == 400

        tz = (await c.get('/api/event-settings/timezone')).json()
        assert tz['follows_community'] is True
        r = await c.put('/api/event-settings/timezone', json={'follows_community': False, 'mode': 'pinned', 'name': 'Europe/Berlin'})
        assert r.json()['follows_community'] is False and r.json()['name'] == 'Europe/Berlin'
        r = await c.put('/api/event-settings/timezone', json={'follows_community': True})
        assert r.json()['follows_community'] is True


async def test_stations_and_room_screens(db, app):
    _, raw = await staff_token()
    async with client_for(app, raw) as c:
        r = await c.put('/api/event-settings/station-format', json={'format': 'structured'})
        assert r.json()['format'] == 'structured' and len(r.json()['options']) == 4

        r = await c.post('/api/event-settings/stations', json={'name': 'A1', 'side': 'left', 'position': 1})
        assert r.status_code == 201
        station = r.json()
        assert (await c.post('/api/event-settings/stations', json={'name': 'A1'})).status_code == 400

        r = await c.patch(f"/api/event-settings/stations/{station['id']}", json={'side': None, 'is_active': False})
        assert r.json()['side'] is None and r.json()['is_active'] is False and r.json()['position'] == 1

        assert [s['name'] for s in (await c.get('/api/event-settings/stations')).json()] == ['A1']
        assert (await c.delete(f"/api/event-settings/stations/{station['id']}")).status_code == 204
        assert (await c.get('/api/event-settings/stations')).json() == []

        r = await c.post('/api/event-settings/room-screens', json={'label': 'Room A desk PC'})
        assert r.status_code == 201
        issued = r.json()
        assert '/room/' in issued['url'] and issued['screen']['issued_by']
        assert (await c.delete(f"/api/event-settings/room-screens/{issued['screen']['id']}")).status_code == 204
        screens = (await c.get('/api/event-settings/room-screens')).json()
        assert screens[0]['revoked'] is True


async def test_players_cant_read_or_save_event_settings(db, app):
    _, raw = await create_user_token(username='player')
    async with client_for(app, raw) as c:
        assert (await c.get('/api/event-settings/dates')).status_code == 403
        assert (await c.put('/api/event-settings/crew', json={'broadcasters': True})).status_code == 403
