"""Integration tests for the GET /matches endpoint.

These tests use a fresh in-memory SQLite database (via the function-scoped
``db`` fixture) and exercise the real ORM query path. The full FastAPI app
is not mounted; we attach only the api router to keep the test app
lightweight and avoid the Discord bot startup.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from models import (
    Commentator,
    GeneratedSeeds,
    Match,
    MatchHardPresetOptIn,
    MatchPlayers,
    Preset,
    PresetOverride,
    Role,
    Stage,
    Tournament,
    Tracker,
    User,
)
from tests.api_helpers import client_for, create_community_member, create_user_token


@pytest.fixture
async def client(db, app):
    """Authenticated client: a fresh user with a (read-write) API token whose
    bearer header is sent on every request."""
    from application.services.api_token_service import ApiTokenService

    user = await create_community_member(discord_id=999999, username='api-test-user')
    _, raw_token = await ApiTokenService().create_token(user, name='test-suite')
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url='http://test',
        headers={'Authorization': f'Bearer {raw_token}'},
    ) as ac:
        yield ac


@pytest.fixture
async def anon_client(db, app):
    """Unauthenticated client (no bearer header)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as ac:
        yield ac


async def _seed_basic_data() -> dict:
    """Seed two tournaments, two stages, four matches with varied times.

    Returns a dict of created objects for use in assertions.
    """
    t1 = await Tournament.create(name='Cup A')
    t2 = await Tournament.create(name='Cup B')

    stage1 = await Stage.create(name='Stage 1')
    stage2 = await Stage.create(name='Stage 2')

    m1 = await Match.create(
        tournament=t1, stage=stage1,
        scheduled_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    m2 = await Match.create(
        tournament=t1, stage=stage2,
        scheduled_at=datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc),
    )
    m3 = await Match.create(
        tournament=t2,
        scheduled_at=datetime(2025, 1, 3, 12, 0, tzinfo=timezone.utc),
    )
    m4 = await Match.create(
        tournament=t2, stage=stage1,
        scheduled_at=datetime(2025, 1, 4, 12, 0, tzinfo=timezone.utc),
    )

    return {
        't1': t1, 't2': t2,
        'stage1': stage1, 'stage2': stage2,
        'm1': m1, 'm2': m2, 'm3': m3, 'm4': m4,
    }


class TestAuthRequired:
    async def test_missing_token_is_rejected(self, anon_client):
        response = await anon_client.get('/api/matches')
        assert response.status_code == 401

    async def test_invalid_token_is_rejected(self, anon_client):
        response = await anon_client.get(
            '/api/matches', headers={'Authorization': 'Bearer not-a-real-token'}
        )
        assert response.status_code == 401


class TestGetMatchesEmpty:
    async def test_empty_db_returns_empty_list(self, client):
        response = await client.get('/api/matches')
        assert response.status_code == 200
        assert response.json() == []


class TestResultShape:
    async def test_matches_ordered_by_scheduled_at(self, client):
        data = await _seed_basic_data()
        response = await client.get('/api/matches')
        assert response.status_code == 200
        ids = [m['id'] for m in response.json()]
        assert ids == [data['m1'].id, data['m2'].id, data['m3'].id, data['m4'].id]

    async def test_response_includes_tournament_metadata(self, client):
        data = await _seed_basic_data()
        response = await client.get('/api/matches')
        body = response.json()
        first = body[0]
        assert first['tournament']['id'] == data['t1'].id
        assert first['tournament']['name'] == 'Cup A'

    async def test_response_includes_stage_when_set(self, client):
        data = await _seed_basic_data()
        response = await client.get('/api/matches')
        body = response.json()
        by_id = {m['id']: m for m in body}
        assert by_id[data['m1'].id]['stage']['name'] == 'Stage 1'
        assert by_id[data['m3'].id]['stage'] is None


class TestFilters:
    async def test_filter_by_single_match_id(self, client):
        data = await _seed_basic_data()
        response = await client.get(f'/api/matches?match_id={data["m2"].id}')
        body = response.json()
        assert [m['id'] for m in body] == [data['m2'].id]

    async def test_filter_by_multiple_match_ids(self, client):
        data = await _seed_basic_data()
        response = await client.get(
            f'/api/matches?match_id={data["m1"].id}&match_id={data["m4"].id}'
        )
        body = response.json()
        assert sorted(m['id'] for m in body) == sorted([data['m1'].id, data['m4'].id])

    async def test_filter_by_stage_id(self, client):
        data = await _seed_basic_data()
        response = await client.get(f'/api/matches?stage_id={data["stage1"].id}')
        body = response.json()
        assert sorted(m['id'] for m in body) == sorted([data['m1'].id, data['m4'].id])

    async def test_filter_by_multiple_stages(self, client):
        data = await _seed_basic_data()
        response = await client.get(
            f'/api/matches?stage_id={data["stage1"].id}'
            f'&stage_id={data["stage2"].id}'
        )
        body = response.json()
        assert sorted(m['id'] for m in body) == sorted(
            [data['m1'].id, data['m2'].id, data['m4'].id]
        )

    async def test_filter_by_tournament_id(self, client):
        data = await _seed_basic_data()
        response = await client.get(f'/api/matches?tournament_id={data["t2"].id}')
        body = response.json()
        assert sorted(m['id'] for m in body) == sorted([data['m3'].id, data['m4'].id])

    async def test_filter_by_start_date_inclusive(self, client):
        await _seed_basic_data()
        response = await client.get('/api/matches?start_date=2025-01-02T00:00:00')
        body = response.json()
        assert len(body) == 3
        for m in body:
            assert m['scheduled_at'] >= '2025-01-02'

    async def test_filter_by_end_date_inclusive(self, client):
        await _seed_basic_data()
        # Using a clearly-after cutoff avoids timezone-aware vs naive
        # comparison ambiguity at the boundary.
        response = await client.get('/api/matches?end_date=2025-01-02T23:59:59')
        body = response.json()
        assert len(body) == 2

    async def test_filter_by_date_range(self, client):
        data = await _seed_basic_data()
        response = await client.get(
            '/api/matches?start_date=2025-01-02T00:00:00&end_date=2025-01-03T23:59:59'
        )
        body = response.json()
        assert sorted(m['id'] for m in body) == sorted([data['m2'].id, data['m3'].id])

    async def test_filters_combine_with_and(self, client):
        data = await _seed_basic_data()
        response = await client.get(
            f'/api/matches?tournament_id={data["t2"].id}'
            f'&stage_id={data["stage1"].id}'
        )
        body = response.json()
        assert [m['id'] for m in body] == [data['m4'].id]


class TestLimit:
    async def test_default_limit_is_applied(self, client):
        t = await Tournament.create(name='Big')
        for i in range(150):
            await Match.create(
                tournament=t,
                scheduled_at=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            )
        response = await client.get('/api/matches')
        assert len(response.json()) == 100

    async def test_custom_limit_below_cap(self, client):
        t = await Tournament.create(name='Big')
        for i in range(10):
            await Match.create(
                tournament=t,
                scheduled_at=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i),
            )
        response = await client.get('/api/matches?limit=5')
        assert len(response.json()) == 5

    async def test_limit_above_500_is_rejected(self, client):
        response = await client.get('/api/matches?limit=501')
        assert response.status_code == 422

    async def test_limit_below_1_is_rejected(self, client):
        response = await client.get('/api/matches?limit=0')
        assert response.status_code == 422


# The /matches endpoint must expose only approved crew. Filtering happens in
# the route handler (api.get_matches) because Pydantic v2 serializes the
# response via attribute access and never invokes a custom from_orm.


class TestApprovedOnlyCrew:
    async def test_only_approved_commentators_returned(self, client):
        t = await Tournament.create(name='T')
        match = await Match.create(
            tournament=t,
            scheduled_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        approved_user = await User.create(discord_id=1, username='approved')
        unapproved_user = await User.create(discord_id=2, username='unapproved')
        await Commentator.create(match=match, user=approved_user, approved=True)
        await Commentator.create(match=match, user=unapproved_user, approved=False)

        response = await client.get('/api/matches')
        body = response.json()
        commentators = body[0]['commentators']
        assert len(commentators) == 1
        assert commentators[0]['user']['username'] == 'approved'

    async def test_only_approved_trackers_returned(self, client):
        t = await Tournament.create(name='T')
        match = await Match.create(
            tournament=t,
            scheduled_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        approved_user = await User.create(discord_id=10, username='approved-tracker')
        unapproved_user = await User.create(discord_id=11, username='unapproved-tracker')
        await Tracker.create(match=match, user=approved_user, approved=True)
        await Tracker.create(match=match, user=unapproved_user, approved=False)

        response = await client.get('/api/matches')
        trackers = response.json()[0]['trackers']
        assert len(trackers) == 1
        assert trackers[0]['user']['username'] == 'approved-tracker'

    async def test_unapproved_commentators_excluded(self, client):
        """Only the approved commentator is returned; the pending one is hidden."""
        t = await Tournament.create(name='T')
        match = await Match.create(
            tournament=t,
            scheduled_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        approved_user = await User.create(discord_id=21, username='ok')
        unapproved_user = await User.create(discord_id=22, username='pending')
        await Commentator.create(match=match, user=approved_user, approved=True)
        await Commentator.create(match=match, user=unapproved_user, approved=False)

        response = await client.get('/api/matches')
        commentators = response.json()[0]['commentators']
        assert len(commentators) == 1
        assert commentators[0]['user']['username'] == 'ok'


class TestRelatedData:
    async def test_players_are_included(self, client):
        t = await Tournament.create(name='T')
        match = await Match.create(
            tournament=t,
            scheduled_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        u1 = await User.create(discord_id=100, username='p1')
        u2 = await User.create(discord_id=101, username='p2')
        await MatchPlayers.create(match=match, user=u1, finish_rank=1)
        await MatchPlayers.create(match=match, user=u2, finish_rank=2)

        response = await client.get('/api/matches')
        players = response.json()[0]['players']
        assert len(players) == 2
        ranks = sorted(p['finish_rank'] for p in players)
        assert ranks == [1, 2]

    async def test_generated_seed_is_included_when_present(self, client):
        t = await Tournament.create(name='T')
        seed = await GeneratedSeeds.create(seed_url='https://example.com/seed/1')
        await Match.create(
            tournament=t,
            generated_seed=seed,
            scheduled_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        response = await client.get('/api/matches')
        body = response.json()[0]
        assert body['generated_seed']['seed_url'] == 'https://example.com/seed/1'

    async def test_generated_seed_is_none_when_absent(self, client):
        t = await Tournament.create(name='T')
        await Match.create(
            tournament=t,
            scheduled_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        response = await client.get('/api/matches')
        assert response.json()[0]['generated_seed'] is None


class TestMatchCrewEndpoint:
    """``GET /matches/{id}/crew`` — unlike the match read, this shows pending
    signups, which is why it sits behind the admin gate."""

    async def _match_with_mixed_crew(self):
        tournament = await Tournament.create(name='T')
        match = await Match.create(
            tournament=tournament,
            scheduled_at=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        approved = await User.create(discord_id=301, username='approved')
        pending = await User.create(discord_id=302, username='pending')
        await Commentator.create(match=match, user=approved, approved=True)
        await Commentator.create(match=match, user=pending, approved=False)
        await Tracker.create(match=match, user=pending, approved=False)
        return match

    async def test_admin_sees_pending_signups(self, db, app):
        match = await self._match_with_mixed_crew()
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.get(f'/api/matches/{match.id}/crew')
            assert resp.status_code == 200
            body = resp.json()
            assert body['match_id'] == match.id
            assert {
                (m['user']['username'], m['approved']) for m in body['commentators']
            } == {('approved', True), ('pending', False)}
            assert [t['user']['username'] for t in body['trackers']] == ['pending']

    async def test_the_match_read_still_hides_them(self, db, app):
        match = await self._match_with_mixed_crew()
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            body = (await c.get(f'/api/matches/{match.id}')).json()
            assert [m['user']['username'] for m in body['commentators']] == ['approved']
            assert body['trackers'] == []

    async def test_a_plain_member_is_forbidden(self, db, app):
        match = await self._match_with_mixed_crew()
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get(f'/api/matches/{match.id}/crew')).status_code == 403

    async def test_unknown_match_is_404(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/matches/9999/crew')).status_code == 404

    async def test_a_crew_coordinator_reads_only_their_tournaments_crew(self, db, app):
        """Pending crew are the approver's view, so a coordinator of another
        tournament is refused, like approving them would be."""
        match = await self._match_with_mixed_crew()
        own, raw_own = await create_user_token(username='own_cc')
        await (await match.tournament).crew_coordinators.add(own)
        other, raw_other = await create_user_token(username='other_cc')
        await (await Tournament.create(name='Other')).crew_coordinators.add(other)
        async with client_for(app, raw_own) as c:
            assert (await c.get(f'/api/matches/{match.id}/crew')).status_code == 200
        async with client_for(app, raw_other) as c:
            assert (await c.get(f'/api/matches/{match.id}/crew')).status_code == 403

    async def test_single_tab_admin_roles_are_forbidden(self, db, app):
        match = await self._match_with_mixed_crew()
        for role in (Role.EQUIPMENT_MANAGER, Role.VOLUNTEER_COORDINATOR):
            _, raw = await create_user_token(username=role.value, roles=[role])
            async with client_for(app, raw) as c:
                assert (await c.get(f'/api/matches/{match.id}/crew')).status_code == 403, role


class TestHardPresetStaysOffTheApi:
    """The opt-in must not become readable one API call away from the page.

    The same failure the async-qualifier score banding had to avoid: fixing the
    page alone leaves the leak reachable over REST. ``MatchResponse`` lists its
    fields explicitly, so this is a pin on that list rather than a filter — if
    somebody adds the field, this fails.
    """

    async def _match_with_an_opt_in(self):
        standard = await Preset.create(
            name='Standard', randomizer='alttpr', settings={'mode': 'open'},
        )
        hard = await Preset.create(
            name='Hard Mode', randomizer='alttpr', settings={'pool': 'hard'},
        )
        tournament = await Tournament.create(
            name='T', preset=standard, hard_preset=hard,
        )
        match = await Match.create(
            tournament=tournament,
            scheduled_at=datetime(2025, 1, 15, 19, 30, tzinfo=timezone.utc),
            preset_override=PresetOverride.HARD,
        )
        opter = await User.create(discord_id=7701, username='opter')
        other = await User.create(discord_id=7702, username='other')
        await MatchPlayers.create(match=match, user=opter)
        await MatchPlayers.create(match=match, user=other)
        await MatchHardPresetOptIn.create(match=match, user=opter)
        return match

    async def test_the_match_read_carries_no_opt_in_field(self, db, app):
        match = await self._match_with_an_opt_in()
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            body = (await c.get(f'/api/matches/{match.id}')).json()

        serialized = json.dumps(body)
        assert 'opted' not in serialized
        assert 'hard_preset' not in serialized
        assert 'preset_override' not in serialized
        # The roster itself is public and stays so; what must not appear is any
        # field distinguishing the player who opted in from the one who did not.
        assert serialized.count('finish_rank') == 2
