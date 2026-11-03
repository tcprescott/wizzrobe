"""The app's Event tab: hub, run sheet, Watch, and marking your own bracket sets."""

from datetime import datetime, timedelta, timezone

from models import BracketEntrant, Commentator, Match, MatchPlayers, Stage, Tournament
from tests.api._bracket_api import token_for
from tests.api_helpers import client_for, create_user_token
from tests.factories import make_member
from tests.services._bracket_overview_setup import native_elim


def _at(minutes: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


async def _setup():
    t = await Tournament.create(name='Cup', is_active=True, required_commentators=2)
    stage = await Stage.create(name='Stage 1', stream_url='https://twitch.tv/SpeedGaming2')
    alice = await make_member(discord_id=9501, username='alice')
    bob = await make_member(discord_id=9502, username='bob')
    live = await Match.create(tournament=t, stage=stage, scheduled_at=_at(-20), seated_at=_at(-15), started_at=_at(-10))
    nxt = await Match.create(tournament=t, stage=stage, scheduled_at=_at(60))
    off = await Match.create(tournament=t, scheduled_at=_at(90))
    for m in (live, nxt):
        await MatchPlayers.create(match=m, user=alice)
        await MatchPlayers.create(match=m, user=bob)
    return t, stage, alice, bob, live, nxt, off


class TestHub:
    async def test_stage_cards_up_next_and_tournaments(self, db, app):
        t, stage, alice, _, live, nxt, off = await _setup()
        await Tournament.create(name='Hidden test', is_active=True, is_test=True)

        async with client_for(app, await token_for(alice)) as c:
            body = (await c.get('/api/event/hub')).json()

        card = body['stages'][0]
        assert card['name'] == 'Stage 1'
        assert card['channel'] == 'speedgaming2' and card['channel_name'] == 'SpeedGaming2'
        assert card['on_now']['match_id'] == live.id
        assert [s['name'] for s in card['on_now']['sides']] == ['alice', 'bob']
        assert [m['match_id'] for m in card['up_next']] == [nxt.id]
        assert [r['match_id'] for r in body['up_next']] == [nxt.id, off.id]
        assert [c['name'] for c in body['tournaments']] == ['Cup']

    async def test_needs_a_token(self, db, app):
        async with client_for(app, None) as c:
            assert (await c.get('/api/event/hub')).status_code == 401


class TestRunSheet:
    async def test_rows_crew_and_signing_up(self, db, app):
        _, _, alice, _, live, nxt, off = await _setup()
        carol = await make_member(discord_id=9503, username='carol')
        await Commentator.create(match=nxt, user=alice, approved=True, tenant_id=nxt.tenant_id)
        params = {'start': _at(-60).isoformat(), 'end': _at(180).isoformat()}

        async with client_for(app, await token_for(carol)) as c:
            rows = (await c.get('/api/event/schedule', params=params)).json()['rows']
            assert [r['match_id'] for r in rows] == [live.id, nxt.id, off.id]
            assert rows[0]['status'] == 'live' and rows[0]['streamed'] is True
            comm = rows[1]['crew'][0]
            assert comm['role'] == 'commentator'
            assert (comm['needed'], comm['filled'], comm['names']) == (2, 1, ['alice'])
            assert comm['can_sign_up'] is True and comm['yours'] is None
            assert rows[2]['stage'] == '' and rows[2]['streamed'] is False

            assert (await c.post(f'/api/matches/{nxt.id}/crew', json={'role': 'commentator'})).status_code == 201
            assert (await c.post(f'/api/matches/{nxt.id}/watch')).status_code in (200, 201)
            row = next(r for r in (await c.get('/api/event/schedule', params=params)).json()['rows'] if r['match_id'] == nxt.id)
            assert row['crew'][0]['yours'] == 'pending' and row['crew'][0]['can_sign_up'] is False
            assert row['watching'] is True

    async def test_a_player_cannot_crew_their_own_match(self, db, app):
        _, _, alice, _, _, nxt, _ = await _setup()
        params = {'start': _at(30).isoformat(), 'end': _at(70).isoformat()}
        async with client_for(app, await token_for(alice)) as c:
            row = (await c.get('/api/event/schedule', params=params)).json()['rows'][0]
        assert row['match_id'] == nxt.id and row['playing'] is True
        assert row['crew'][0]['can_sign_up'] is False

    async def test_window_limits(self, db, app):
        _, raw = await create_user_token(username='lurker')
        async with client_for(app, raw) as c:
            too_long = await c.get('/api/event/schedule', params={'start': _at(0).isoformat(), 'end': _at(60 * 49).isoformat()})
            backwards = await c.get('/api/event/schedule', params={'start': _at(60).isoformat(), 'end': _at(0).isoformat()})
        assert too_long.status_code == 400 and backwards.status_code == 400


class TestWatch:
    async def test_stages_and_coming_up(self, db, app):
        t, stage, alice, bob, live, nxt, _ = await _setup()
        later = [await Match.create(tournament=t, stage=stage, scheduled_at=_at(60 * h)) for h in (3, 30)]

        async with client_for(app, await token_for(alice)) as c:
            body = (await c.get('/api/event/watch')).json()

        assert body['stages'][0]['on_now']['match_id'] == live.id
        shown = [body['stages'][0]['on_now']['match_id']] + [m['match_id'] for m in body['stages'][0]['up_next']]
        coming = [m['match_id'] for m in body['coming_up']]
        assert later[1].id in coming and not set(shown) & set(coming)
        assert all(m['stage'] == 'Stage 1' for m in body['coming_up'])


async def test_bracket_marks_your_own_entrant(db, app):
    tournament, *_ = await native_elim()
    alice = await make_member(discord_id=9510, username='alice')
    await BracketEntrant.filter(tournament=tournament, display_name='Alice').update(user_id=alice.id)
    mine = await BracketEntrant.get(tournament=tournament, display_name='Alice')

    async with client_for(app, await token_for(alice)) as c:
        body = (await c.get(f'/api/tournaments/{tournament.id}/bracket')).json()

    assert body['your_entrant_ids'] == [mine.id]
    assert {e['user_id'] for e in body['entrants']} == {None}
