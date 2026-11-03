"""The app's Home and Schedule reads, picking a time, and running late."""

from datetime import date, datetime, timedelta, timezone

import pytest

from models import Event, Match, MatchAcknowledgment, MatchPlayers, Role, Tournament
from tests.api._bracket_api import draft, enroll, staff_token, token_for, tournament
from tests.api_helpers import client_for, create_user_token
from tests.factories import make_member


@pytest.fixture(autouse=True)
async def _event_runs_this_week(db):
    """Without dates the window falls back to one day, so a match before 06:00 drops out."""
    today = date.today()
    await Event.filter(is_default=True).update(
        starts_on=today - timedelta(days=2), ends_on=today + timedelta(days=3),
    )


def _soon(minutes: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


async def _match_at(when: datetime, *players, **fields):
    t = await Tournament.create(name='Cup', is_active=True)
    match = await Match.create(tournament=t, scheduled_at=when, **fields)
    for p in players:
        await MatchPlayers.create(match=match, user=p)
    return match


class TestHome:
    async def test_next_up_and_acknowledge(self, db, app):
        alice = await make_member(discord_id=9401, username='alice')
        bob = await make_member(discord_id=9402, username='bob')
        match = await _match_at(_soon(60), alice, bob)
        await MatchAcknowledgment.create(match=match, user=alice, tenant_id=match.tenant_id)

        async with client_for(app, await token_for(alice)) as c:
            body = (await c.get('/api/me/home')).json()
            assert body['next_up']['match_id'] == match.id
            assert body['next_up']['title'] == 'vs bob'
            assert body['next_up']['status'] == 'scheduled'
            kinds = [n['kind'] for n in body['needs_you']]
            assert 'acknowledge' in kinds

            assert (await c.post(f'/api/matches/{match.id}/acknowledge')).status_code == 200
            body = (await c.get('/api/me/home')).json()
            assert 'acknowledge' not in [n['kind'] for n in body['needs_you']]

    async def test_someone_with_nothing_gets_an_empty_home(self, db, app):
        _, raw = await create_user_token(username='lurker')
        async with client_for(app, raw) as c:
            body = (await c.get('/api/me/home')).json()
            assert body['next_up'] is None
            assert body['today'] == []

    async def test_needs_a_token(self, db, app):
        async with client_for(app, None) as c:
            assert (await c.get('/api/me/home')).status_code == 401


class TestSchedule:
    async def test_upcoming_and_past(self, db, app):
        alice = await make_member(discord_id=9411, username='alice')
        bob = await make_member(discord_id=9412, username='bob')
        later = await _match_at(_soon(120), alice, bob)
        done = await Match.create(
            tournament_id=later.tournament_id, scheduled_at=_soon(-240), finished_at=_soon(-200),
        )
        await MatchPlayers.create(match=done, user=alice)
        await MatchPlayers.create(match=done, user=bob)

        async with client_for(app, await token_for(alice)) as c:
            upcoming = (await c.get('/api/me/schedule')).json()['items']
            assert [i['match_id'] for i in upcoming] == [later.id]
            past = (await c.get('/api/me/schedule?past=true')).json()['items']
            assert [i['match_id'] for i in past] == [done.id]

    async def test_someone_elses_match_is_not_listed(self, db, app):
        alice = await make_member(discord_id=9421, username='alice')
        bob = await make_member(discord_id=9422, username='bob')
        carol = await make_member(discord_id=9423, username='carol')
        await _match_at(_soon(60), alice, bob)
        async with client_for(app, await token_for(carol)) as c:
            assert (await c.get('/api/me/schedule')).json()['items'] == []


class TestRunningLate:
    async def test_player_says_late(self, db, app):
        alice = await make_member(discord_id=9431, username='alice')
        bob = await make_member(discord_id=9432, username='bob')
        match = await _match_at(_soon(10), alice, bob)
        async with client_for(app, await token_for(alice)) as c:
            home = (await c.get('/api/me/home')).json()
            assert home['next_up']['can_report_late'] is True
            resp = await c.post(f'/api/matches/{match.id}/running-late', json={'minutes': 10})
            assert resp.status_code == 200
            home = (await c.get('/api/me/home')).json()
            assert home['next_up']['late_minutes'] == 10

    async def test_refuses_an_odd_number_and_outsiders(self, db, app):
        alice = await make_member(discord_id=9441, username='alice')
        bob = await make_member(discord_id=9442, username='bob')
        carol = await make_member(discord_id=9443, username='carol')
        match = await _match_at(_soon(10), alice, bob)
        async with client_for(app, await token_for(alice)) as c:
            resp = await c.post(f'/api/matches/{match.id}/running-late', json={'minutes': 7})
            assert resp.status_code == 400
        async with client_for(app, await token_for(carol)) as c:
            resp = await c.post(f'/api/matches/{match.id}/running-late', json={'minutes': 10})
            assert resp.status_code in (400, 403, 404)


class TestPickATime:
    async def _ready_matchup(self, app):
        _, staff = await staff_token()
        t = await tournament()
        alice = await make_member(discord_id=9451, username='alice')
        bob = await make_member(discord_id=9452, username='bob')
        async with client_for(app, staff) as c:
            bracket_id = await draft(c, t)
            await enroll(c, bracket_id, t, ('Alice', 'Bob'), user_ids=[alice.id, bob.id])
            await c.post(f'/api/brackets/{bracket_id}/start')
        return alice, bob

    async def test_list_check_and_book(self, db, app):
        alice, bob = await self._ready_matchup(app)
        async with client_for(app, await token_for(alice)) as c:
            listed = (await c.get('/api/me/bookable')).json()
            assert len(listed) == 1
            row = listed[0]
            assert row['source'] == 'bracket' and row['opponent'] == 'Bob'
            home = (await c.get('/api/me/home')).json()
            assert [n['kind'] for n in home['needs_you']].count('book') == 1

            at = (_soon(24 * 60)).replace(minute=0, second=0, microsecond=0).isoformat()
            check = await c.get(f"/api/me/bookable/bracket/{row['key']}/check", params={'at': at})
            assert check.status_code == 200
            assert check.json()['refusal'] is None

            booked = await c.post(f"/api/me/bookable/bracket/{row['key']}/book", json={'scheduled_at': at})
            assert booked.status_code == 200, booked.text
            assert (await c.get('/api/me/bookable')).json() == []

    async def test_someone_else_cannot_book_it(self, db, app):
        alice, _ = await self._ready_matchup(app)
        async with client_for(app, await token_for(alice)) as c:
            key = (await c.get('/api/me/bookable')).json()[0]['key']
        carol = await make_member(discord_id=9459, username='carol')
        async with client_for(app, await token_for(carol)) as c:
            resp = await c.post(
                f'/api/me/bookable/bracket/{key}/book', json={'scheduled_at': _soon(1440).isoformat()},
            )
            assert resp.status_code == 400

    async def test_unknown_source_is_rejected(self, db, app):
        _, raw = await create_user_token(username='x', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.get('/api/me/bookable/myspace/1/check', params={'at': _soon(60).isoformat()})
            assert resp.status_code == 422
