"""The app's admin Match Schedule: the board, a match's pane, rewind and call off."""

from datetime import datetime, timedelta, timezone

from models import Match, MatchPlayers, Role, Stage, Tournament
from tests.api_helpers import client_for, create_community_member, create_user_token


def _at(hours: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


async def _match(t: Tournament, at: datetime, *names: str, **fields) -> Match:
    match = await Match.create(tournament=t, scheduled_at=at, **fields)
    for name in names:
        await MatchPlayers.create(match=match, user=await create_community_member(username=name))
    return match


def _rows(body: dict) -> dict:
    return {r['id']: (g['label'], r) for g in body['groups'] for r in g['rows']}


class TestBoard:
    async def test_staff_see_every_match_grouped(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        live = await _match(cup, _at(-0.5), 'ann', 'bob', started_at=_at(-0.4))
        later = await _match(cup, _at(2), 'cat', 'dan')
        empty = await _match(cup, _at(3))
        async with client_for(app, raw) as c:
            resp = await c.get('/api/admin/match-schedule', params={'tz': 'UTC'})
        assert resp.status_code == 200
        body = resp.json()
        assert [g['label'] for g in body['groups']] == ['Live now', 'Up next', 'Waiting for players']
        rows = _rows(body)
        assert rows[live.id][1]['status'] == 'Live'
        assert [w['name'] for w in rows[live.id][1]['winners']] == ['ann', 'bob']
        assert rows[later.id][1]['next_step']['event'] == 'seat'
        assert rows[later.id][1]['can'] == {'edit': True, 'run': True, 'confirm': True, 'stage': True, 'crew': True}
        assert rows[empty.id][0] == 'Waiting for players'
        assert body['can_create'] is True
        assert body['scoped_tournaments'] is None

    async def test_search_and_day(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        today = await _match(cup, datetime.now(timezone.utc).replace(hour=12, minute=0), 'ann', 'bob')
        await _match(cup, datetime.now(timezone.utc).replace(hour=12) + timedelta(days=3), 'cat', 'dan')
        async with client_for(app, raw) as c:
            body = (await c.get('/api/admin/match-schedule', params={'q': 'ANN'})).json()
            assert list(_rows(body)) == [today.id]
            day = today.scheduled_at.date().isoformat()
            body = (await c.get('/api/admin/match-schedule', params={'day': day, 'tz': 'UTC'})).json()
            assert list(_rows(body)) == [today.id]

    async def test_tournament_admin_sees_only_their_tournaments(self, db, app):
        ta, raw = await create_user_token(username='ta')
        mine = await Tournament.create(name='Mine', is_active=True)
        other = await Tournament.create(name='Other', is_active=True)
        await mine.admins.add(ta)
        own = await _match(mine, _at(2), 'ann', 'bob')
        hidden = await _match(other, _at(2), 'cat', 'dan')
        async with client_for(app, raw) as c:
            body = (await c.get('/api/admin/match-schedule')).json()
            assert list(_rows(body)) == [own.id]
            assert body['scoped_tournaments'] == 1
            assert [t['name'] for t in body['tournaments']] == ['Mine']
            assert (await c.get(f'/api/admin/match-schedule/matches/{hidden.id}')).status_code == 404

    async def test_broadcast_tech_runs_only_stage_matches(self, db, app):
        _, raw = await create_user_token(username='bt', roles=[Role.STREAM_MANAGER])
        cup = await Tournament.create(name='Cup', is_active=True)
        stage = await Stage.create(name='Stage 1')
        on_stage = await _match(cup, _at(1), 'ann', 'bob', stage=stage)
        floor = await _match(cup, _at(2), 'cat', 'dan')
        async with client_for(app, raw) as c:
            rows = _rows((await c.get('/api/admin/match-schedule')).json())
        assert rows[on_stage.id][1]['can']['run'] is True
        assert rows[floor.id][1]['can']['run'] is False
        assert rows[floor.id][1]['can']['stage'] is True
        assert rows[floor.id][1]['can']['edit'] is False

    async def test_a_player_is_refused(self, db, app):
        _, raw = await create_user_token(username='pl')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/match-schedule')).status_code == 403


class TestMatchPane:
    async def test_checklist_and_rewind(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        match = await _match(cup, _at(1), 'ann', 'bob', seated_at=_at(-0.1))
        async with client_for(app, raw) as c:
            body = (await c.get(f'/api/admin/match-schedule/matches/{match.id}')).json()
            assert body['state'] == 'Checked In'
            assert body['state_since'] is not None
            assert [i['key'] for i in body['checklist']][:3] == ['players', 'stations', 'stage']
            assert [r['step'] for r in body['rewind_to']] == ['scheduled']
            resp = await c.post(f'/api/matches/{match.id}/rewind', json={'to_step': 'scheduled'})
            assert resp.status_code == 200
            assert (await Match.get(id=match.id)).seated_at is None
            assert (await c.post(f'/api/matches/{match.id}/rewind', json={'to_step': 'scheduled'})).status_code == 400

    async def test_call_off(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        match = await _match(cup, _at(1), 'ann', 'bob')
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/matches/{match.id}/cancel', json={'reason': 'Venue closed'})
        assert resp.status_code == 204
        assert not await Match.filter(id=match.id).exists()

    async def test_a_player_cannot_call_off(self, db, app):
        _, raw = await create_user_token(username='pl')
        cup = await Tournament.create(name='Cup', is_active=True)
        match = await _match(cup, _at(1), 'ann', 'bob')
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/matches/{match.id}/cancel', json={})).status_code == 403
