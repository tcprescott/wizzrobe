"""The app's Admin → Tournaments screen: board, players, drop, bracket tools, round schedule."""

from datetime import datetime, timedelta, timezone

from models import Match, MatchPlayers, Tournament, TournamentPlayers, User
from tests.api._bracket_api import staff_token
from tests.api_helpers import client_for, create_user_token


async def _ta_token(tourney: Tournament, staff_raw: str, app, username='ta'):
    user, raw = await create_user_token(username=username)
    async with client_for(app, staff_raw) as c:
        assert (await c.post(f'/api/tournaments/{tourney.id}/admins', json={'user_id': user.id})).status_code == 200
    return user, raw


class TestBoard:
    async def test_staff_see_every_tournament_with_counts(self, db, app):
        _, raw = await staff_token()
        cup = await Tournament.create(name='Cup', is_active=False)
        await Tournament.create(name='Alpha', signups_open_at=datetime.now(timezone.utc) - timedelta(days=1))
        player = await User.create(discord_id=4001, username='p1')
        await TournamentPlayers.create(tournament=cup, user=player)
        async with client_for(app, raw) as c:
            body = (await c.get('/api/admin/tournaments/board')).json()
        assert body['can_create'] is True
        names = [r['name'] for r in body['rows']]
        assert names == ['Alpha', 'Cup']
        alpha, cup_row = body['rows']
        assert alpha['signup_state'] == 'open'
        assert cup_row['player_count'] == 1
        assert cup_row['can_delete'] is True and alpha['can_delete'] is False
        assert cup_row['bracket'] is None and cup_row['has_round_schedule'] is False

    async def test_a_tournament_admin_sees_only_their_own(self, db, app):
        _, staff_raw = await staff_token()
        mine = await Tournament.create(name='Mine')
        await Tournament.create(name='Theirs')
        _, raw = await _ta_token(mine, staff_raw, app)
        async with client_for(app, raw) as c:
            body = (await c.get('/api/admin/tournaments/board')).json()
            assert [r['name'] for r in body['rows']] == ['Mine']
            assert body['can_create'] is False and body['linkable_sites'] == []
            other = await Tournament.get(name='Theirs')
            assert (await c.get(f'/api/admin/tournaments/{other.id}')).status_code == 403
            assert (await c.get(f'/api/admin/tournaments/{mine.id}/deletion-preview')).status_code == 403

    async def test_a_plain_member_is_refused(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/admin/tournaments/board')).status_code == 403


class TestPlayers:
    async def test_players_people_and_drop(self, db, app):
        _, raw = await staff_token()
        cup = await Tournament.create(name='Cup')
        p1, _ = await create_user_token(username='zed')
        p2, _ = await create_user_token(username='amy')
        for p in (p1, p2):
            await TournamentPlayers.create(tournament=cup, user=p)
        match = await Match.create(tournament=cup, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1))
        for p in (p1, p2):
            await MatchPlayers.create(match=match, user=p)
        async with client_for(app, raw) as c:
            players = (await c.get(f'/api/admin/tournaments/{cup.id}/players')).json()
            assert [p['user']['name'] for p in players] == ['amy', 'zed']
            people = (await c.get(f'/api/admin/tournaments/{cup.id}/people')).json()
            assert {'amy', 'zed'} <= {p['name'] for p in people}

            plan = (await c.get(f'/api/admin/tournaments/{cup.id}/players/{p1.id}/drop-plan')).json()
            assert [m['id'] for m in plan['head_to_head']] == [match.id]
            assert plan['on_bracket_site'] is False

            result = await c.post(
                f'/api/admin/tournaments/{cup.id}/players/{p1.id}/drop',
                json={'reason': 'withdrew', 'forfeit_matches': False},
            )
            assert result.status_code == 200, result.text
            assert result.json()['called_off'] == 1
            players = (await c.get(f'/api/admin/tournaments/{cup.id}/players')).json()
            assert [p['user']['name'] for p in players] == ['amy']

    async def test_deletion_preview(self, db, app):
        _, raw = await staff_token()
        cup = await Tournament.create(name='Cup', is_active=False)
        await Match.create(tournament=cup)
        async with client_for(app, raw) as c:
            body = (await c.get(f'/api/admin/tournaments/{cup.id}/deletion-preview')).json()
        assert body['matches'] == 1 and body['players'] == 0


class TestBracketTools:
    async def test_unlinked_tournament_says_so(self, db, app):
        _, raw = await staff_token()
        cup = await Tournament.create(name='Cup')
        async with client_for(app, raw) as c:
            for call in (
                c.post(f'/api/admin/tournaments/{cup.id}/bracket/sync'),
                c.get(f'/api/admin/tournaments/{cup.id}/bracket/entrants'),
                c.delete(f'/api/admin/tournaments/{cup.id}/bracket'),
            ):
                resp = await call
                assert resp.status_code == 400, resp.text
                assert "isn't linked" in resp.json()['detail']
            assert (await c.get(f'/api/admin/tournaments/{cup.id}/bracket/startgg')).status_code == 400
            assert (await c.post('/api/admin/tournaments/sync')).json() == []

    async def test_link_refuses_a_link_for_the_other_site(self, db, app):
        _, raw = await staff_token()
        cup = await Tournament.create(name='Cup')
        async with client_for(app, raw) as c:
            board = (await c.get('/api/admin/tournaments/board')).json()
            sites = {s['key'] for s in board['linkable_sites']}
            if 'challonge' in sites:
                resp = await c.put(f'/api/admin/tournaments/{cup.id}/bracket',
                                   json={'site': 'challonge', 'bracket': 'https://start.gg/tournament/x/event/y'})
                assert resp.status_code == 400
                assert 'start.gg' in resp.json()['detail']

    async def test_a_tournament_admin_cannot_link(self, db, app):
        _, staff_raw = await staff_token()
        cup = await Tournament.create(name='Cup')
        _, raw = await _ta_token(cup, staff_raw, app)
        async with client_for(app, raw) as c:
            resp = await c.put(f'/api/admin/tournaments/{cup.id}/bracket', json={'site': 'challonge', 'bracket': 'abc'})
            assert resp.status_code == 403


class TestRoundSchedule:
    async def test_daily_hours_need_both_ends(self, db, app):
        _, raw = await staff_token()
        cup = await Tournament.create(name='Cup', challonge_tournament_id='123')
        async with client_for(app, raw) as c:
            resp = await c.put(f'/api/admin/tournaments/{cup.id}/round-schedule',
                               json={'stages': {'e1': {'daily_from': '10:00'}}})
            assert resp.status_code == 400
            ok = await c.put(f'/api/admin/tournaments/{cup.id}/round-schedule',
                             json={'stages': {'e1': {'best_of': 3}}})
            assert ok.status_code == 204, ok.text
        await cup.refresh_from_db()
        assert cup.config['round_schedule']['e1']['best_of'] == 3

