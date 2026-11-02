"""The app's admin Match Schedule: the board, Needs you, a match's pane, comments, rewind and call off."""

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


class TestNeedsYou:
    async def test_requests_results_and_late_matches(self, db, app):
        from models import MatchRescheduleRequest, RescheduleRequestKind

        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        late = await _match(cup, _at(-0.5), 'ann', 'bob')
        done = await _match(cup, _at(-2), 'cat', 'dan', started_at=_at(-1.9), finished_at=_at(-1.5))
        moving = await _match(cup, _at(5), 'eve', 'fay')
        asker = (await moving.players.all().prefetch_related('user'))[0].user
        request = await MatchRescheduleRequest.create(
            match=moving, requested_by=asker, kind=RescheduleRequestKind.RESCHEDULE,
            proposed_at=_at(6), reason='Train is late', original_scheduled_at=moving.scheduled_at,
        )
        async with client_for(app, raw) as c:
            body = (await c.get('/api/admin/match-schedule')).json()
        needs = [(n['kind'], n['match_id']) for n in body['needs_you']]
        assert needs == [('request', moving.id), ('result', done.id), ('late', late.id)]
        req = body['needs_you'][0]['request']
        assert req['id'] == request.id
        assert req['requested_by'] == asker.preferred_name
        assert req['reason'] == 'Train is late'
        assert body['needs_you'][1]['has_result'] is False

    async def test_broadcast_tech_gets_no_requests(self, db, app):
        from models import MatchRescheduleRequest, RescheduleRequestKind

        _, raw = await create_user_token(username='bt', roles=[Role.STREAM_MANAGER])
        cup = await Tournament.create(name='Cup', is_active=True)
        moving = await _match(cup, _at(5), 'eve', 'fay')
        asker = (await moving.players.all().prefetch_related('user'))[0].user
        await MatchRescheduleRequest.create(
            match=moving, requested_by=asker, kind=RescheduleRequestKind.CANCEL, reason='Sick',
        )
        async with client_for(app, raw) as c:
            body = (await c.get('/api/admin/match-schedule')).json()
        assert body['needs_you'] == []


class TestComments:
    async def test_staff_note_stays_with_staff(self, db, app):
        _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        match = await _match(cup, _at(1), 'ann', 'bob')
        _, outsider_raw = await create_user_token(username='outsider')
        async with client_for(app, staff_raw) as c:
            resp = await c.post(f'/api/matches/{match.id}/comments', json={'body': 'Hold Stage 3', 'kind': 'staff_private'})
            assert resp.status_code == 201
            note = resp.json()
            assert note['own'] is True and note['can_delete'] is True and note['author'] == 'boss'
            resp = await c.post(f'/api/matches/{match.id}/comments', json={'body': 'See you at 1', 'kind': 'staff_to_players'})
            assert resp.status_code == 201
            assert [x['kind'] for x in (await c.get(f'/api/matches/{match.id}/comments')).json()] == [
                'staff_private', 'staff_to_players',
            ]
        async with client_for(app, outsider_raw) as c:
            assert (await c.get(f'/api/matches/{match.id}/comments')).json() == []
            resp = await c.post(f'/api/matches/{match.id}/comments', json={'body': 'hi', 'kind': 'staff_private'})
            assert resp.status_code == 400

    async def test_delete(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        match = await _match(cup, _at(1), 'ann', 'bob')
        async with client_for(app, raw) as c:
            note = (await c.post(f'/api/matches/{match.id}/comments', json={'body': 'x', 'kind': 'staff_private'})).json()
            assert (await c.delete(f'/api/match-comments/{note["id"]}')).status_code == 204
            assert (await c.get(f'/api/matches/{match.id}/comments')).json() == []


class TestMatchForm:
    async def test_form_lists_tournaments_with_who_is_enrolled(self, db, app):
        from models import TournamentPlayers

        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        await Tournament.create(name='Old', is_active=False)
        ann = await create_community_member(username='ann')
        await TournamentPlayers.create(tournament=cup, user=ann)
        async with client_for(app, raw) as c:
            body = (await c.get('/api/admin/match-schedule/form')).json()
        assert [t['name'] for t in body['tournaments']] == ['Cup']
        assert body['tournaments'][0]['enrolled'] == [ann.id]
        assert body['values']['player_ids'] == []

    async def test_create_hold_a_slot_then_edit_it(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        stage = await Stage.create(name='Stage 1')
        ann = await create_community_member(username='ann')
        bob = await create_community_member(username='bob')
        at = _at(3).replace(minute=0, second=0, microsecond=0)
        async with client_for(app, raw) as c:
            resp = await c.post('/api/admin/match-schedule/matches', json={
                'tournament_id': cup.id, 'scheduled_at': at.isoformat(), 'title': 'Final', 'stage_id': stage.id,
            })
            assert resp.status_code == 201, resp.text
            match_id = resp.json()['match_id']
            match = await Match.get(id=match_id)
            assert match.title == 'Final' and match.stage_id == stage.id and match.scheduled_at == at
            later = at + timedelta(hours=1)
            resp = await c.put(f'/api/admin/match-schedule/matches/{match_id}', json={
                'tournament_id': cup.id, 'scheduled_at': later.isoformat(), 'player_ids': [ann.id, bob.id],
                'title': 'Final', 'is_stream_candidate': True,
            })
            assert resp.status_code == 200, resp.text
            form = (await c.get('/api/admin/match-schedule/form', params={'match_id': match_id})).json()
        assert sorted(form['values']['player_ids']) == sorted([ann.id, bob.id])
        assert form['values']['stage_id'] is None
        assert form['values']['is_stream_candidate'] is True
        assert (await Match.get(id=match_id)).scheduled_at == later

    async def test_a_player_cannot_open_the_form(self, db, app):
        _, raw = await create_user_token(username='pl')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/match-schedule/form')).status_code == 403

    async def test_check_time_reports_a_clash(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup = await Tournament.create(name='Cup', is_active=True)
        busy = await _match(cup, _at(4), 'ann', 'bob')
        ann = (await busy.players.all().prefetch_related('user'))[0].user
        async with client_for(app, raw) as c:
            body = (await c.post('/api/admin/match-schedule/check-time', json={
                'tournament_id': cup.id, 'player_ids': [ann.id], 'scheduled_at': busy.scheduled_at.isoformat(),
            })).json()
        assert body['window'] is None
        assert body['clashes']


class TestUnbooked:
    async def _setup(self):
        from models import ChallongeMatch, ChallongeMatchState, ChallongeParticipant

        cup = await Tournament.create(name='Cup', is_active=True, challonge_tournament_id='T1')
        ann = await create_community_member(username='ann')
        bob = await create_community_member(username='bob')
        p1 = await ChallongeParticipant.create(tournament=cup, challonge_participant_id='a', name='Ann', user=ann)
        p2 = await ChallongeParticipant.create(tournament=cup, challonge_participant_id='b', name='Bob', user=bob)
        cm = await ChallongeMatch.create(
            tournament=cup, challonge_match_id='m1', round=1, state=ChallongeMatchState.OPEN,
            participant1=p1, participant2=p2,
        )
        return cup, cm

    async def test_list_and_book(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        cup, cm = await self._setup()
        at = _at(5).replace(minute=0, second=0, microsecond=0)
        async with client_for(app, raw) as c:
            items = (await c.get('/api/admin/match-schedule/unbooked')).json()
            assert [(i['source'], i['key'], i['state']) for i in items] == [('challonge', cm.id, 'ready')]
            assert items[0]['players'] == ['Ann', 'Bob']
            resp = await c.post(f'/api/admin/match-schedule/unbooked/challonge/{cm.id}/book', json={'scheduled_at': at.isoformat()})
            assert resp.status_code == 201, resp.text
            assert (await Match.get(id=resp.json()['match_id'])).scheduled_at == at
            assert (await c.get('/api/admin/match-schedule/unbooked')).json() == []

    async def test_a_player_sees_nothing(self, db, app):
        _, raw = await create_user_token(username='pl')
        await self._setup()
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/match-schedule/unbooked')).status_code == 403
