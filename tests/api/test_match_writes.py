"""Tests for Phase 3 match & crew write endpoints.

Verifies that endpoints route through the service layer with the token user as
actor, inheriting its permission checks (Staff/TA gates, read-only rejection).
"""


from models import (
    Commentator,
    Match,
    MatchPlayers,
    Role,
    Station,
    StationSide,
    Tournament,
    User,
)
from tests.api_helpers import client_for, create_community_member, create_user_token


async def _seeded_match():
    """A match with two players, built through the ORM.

    A proctor token cannot ``POST /api/matches``, so the lifecycle tests below
    cannot use ``_create_match`` to set up their fixture.
    """
    t, p1, p2 = await _tournament_and_players()
    match = await Match.create(tournament=t, scheduled_at=None)
    await MatchPlayers.create(match=match, user=p1)
    await MatchPlayers.create(match=match, user=p2)
    return match


async def _tournament_and_players():
    t = await Tournament.create(name='Cup', is_active=True)
    p1 = await create_community_member(discord_id=101, username='p1')
    p2 = await create_community_member(discord_id=102, username='p2')
    return t, p1, p2


async def _create_match(client, t, p1, p2, **overrides):
    payload = {
        'tournament_id': t.id,
        'scheduled_date': '2026-06-10',
        'scheduled_time': '18:00',
        'player_ids': [p1.id, p2.id],
    }
    payload.update(overrides)
    return await client.post('/api/matches', json=payload)


class TestCreateMatch:
    async def test_staff_can_create(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            resp = await _create_match(c, t, p1, p2)
            assert resp.status_code == 201
            body = resp.json()
            assert len(body['players']) == 2

    async def test_non_staff_is_forbidden(self, db, app):
        _, raw = await create_user_token(username='nobody')
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            resp = await _create_match(c, t, p1, p2)
            assert resp.status_code == 403

    async def test_read_only_token_is_forbidden(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF], read_only=True)
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            resp = await _create_match(c, t, p1, p2)
            assert resp.status_code == 403


class TestLifecycle:
    async def test_seat_start_finish_confirm(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']

            seated = await c.post(f'/api/matches/{mid}/seat')
            assert seated.status_code == 200
            assert seated.json()['seated_at'] is not None

            assert (await c.post(f'/api/matches/{mid}/start')).status_code == 200
            finished = await c.post(f'/api/matches/{mid}/finish')
            assert finished.status_code == 200
            assert finished.json()['finished_at'] is not None

            # /finish does not record a winner, and confirming advances the
            # bracket, so the result has to be posted before /confirm will pass.
            winner = await MatchPlayers.filter(match_id=mid).first()
            assert (await c.post(
                f'/api/matches/{mid}/result', json={'winner_id': winner.id},
            )).status_code == 200
            assert (await c.post(f'/api/matches/{mid}/confirm')).status_code == 200

    async def test_confirm_without_a_recorded_result_is_400(self, db, app):
        """Regression: confirming used to check only ``finished_at``, so this
        path advanced the bracket on an empty result."""
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            await c.post(f'/api/matches/{mid}/seat')
            await c.post(f'/api/matches/{mid}/start')
            assert (await c.post(f'/api/matches/{mid}/finish')).status_code == 200

            resp = await c.post(f'/api/matches/{mid}/confirm')
            assert resp.status_code == 400
            assert 'No result has been recorded' in resp.json()['detail']

        assert (await Match.get(id=mid)).confirmed_at is None

    async def test_finish_before_start_is_400(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            # Must be seated+started first; finishing immediately is a 400.
            assert (await c.post(f'/api/matches/{mid}/finish')).status_code == 400

    async def test_record_result(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            winner = await MatchPlayers.filter(match_id=mid).first()
            resp = await c.post(f'/api/matches/{mid}/result', json={'winner_id': winner.id})
            assert resp.status_code == 200
            ranks = {p['id']: p['finish_rank'] for p in resp.json()['players']}
            assert ranks[winner.id] == 1

    async def test_seat_rejects_a_match_with_no_players(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, _, _ = await _tournament_and_players()
        match = await Match.create(tournament=t, scheduled_at=None)
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/matches/{match.id}/seat')
            assert resp.status_code == 400
            assert 'no players' in resp.json()['detail']
        await match.refresh_from_db()
        assert match.seated_at is None

    async def test_delete_match(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        async with client_for(app, raw) as c:
            mid = (await _create_match(c, t, p1, p2)).json()['id']
            assert (await c.delete(f'/api/matches/{mid}')).status_code == 204
            assert (await c.get(f'/api/matches/{mid}')).status_code == 404


class TestProctorLifecycleBoundary:
    """A PROCTOR token runs a match but may not confirm it.

    Confirming advances the native bracket and pushes to Challonge, so it gates
    on ``can_confirm_match`` (staff / tournament admin) while every other
    lifecycle action gates on ``can_run_match``, which admits PROCTOR.
    """

    async def test_proctor_token_cannot_confirm(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        match = await _seeded_match()
        async with client_for(app, raw) as c:
            await c.post(f'/api/matches/{match.id}/seat')
            await c.post(f'/api/matches/{match.id}/start')
            assert (await c.post(f'/api/matches/{match.id}/finish')).status_code == 200
            # Recorded, so the 403 below is about the role and not the missing
            # result the confirm guard would otherwise reject first.
            winner = await MatchPlayers.filter(match_id=match.id).first()
            assert (await c.post(
                f'/api/matches/{match.id}/result', json={'winner_id': winner.id},
            )).status_code == 200

            resp = await c.post(f'/api/matches/{match.id}/confirm')
            assert resp.status_code == 403

        await match.refresh_from_db()
        assert match.confirmed_at is None

    async def test_proctor_token_can_seat_start_finish(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        match = await _seeded_match()
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/matches/{match.id}/seat')).status_code == 200
            assert (await c.post(f'/api/matches/{match.id}/start')).status_code == 200
            assert (await c.post(f'/api/matches/{match.id}/finish')).status_code == 200

    async def test_proctor_token_can_assign_stations_and_record_result(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        match = await _seeded_match()
        players = await MatchPlayers.filter(match_id=match.id).order_by('id')
        async with client_for(app, raw) as c:
            stations = await c.post(
                f'/api/matches/{match.id}/stations',
                json={'assignments': {str(players[0].id): '1', str(players[1].id): '2'}},
            )
            assert stations.status_code == 200

            result = await c.post(
                f'/api/matches/{match.id}/result', json={'winner_id': players[0].id}
            )
            assert result.status_code == 200
            ranks = {p['id']: p['finish_rank'] for p in result.json()['players']}
            assert ranks[players[0].id] == 1

    async def test_proctor_token_can_draw_a_seating(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        match = await _seeded_match()
        players = await MatchPlayers.filter(match_id=match.id).order_by('id')
        await Station.create(name='1', side=StationSide.LEFT, position=1)
        await Station.create(name='2', side=StationSide.RIGHT, position=1)
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/matches/{match.id}/stations/suggest')
            assert resp.status_code == 200
            body = resp.json()
            assert sorted(body['assignments'].values()) == ['1', '2']
            assert set(body['assignments']) == {str(p.id) for p in players}
            assert body['relaxations'] == []

    async def test_a_seating_draw_without_a_pool_is_a_400(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        match = await _seeded_match()
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/matches/{match.id}/stations/suggest')
            assert resp.status_code == 400

    async def test_a_seating_draw_for_a_missing_match_is_a_404(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        async with client_for(app, raw) as c:
            assert (await c.post('/api/matches/9999/stations/suggest')).status_code == 404

    async def test_a_read_only_token_cannot_draw_a_seating(self, db, app):
        _, raw = await create_user_token(
            username='proc', roles=[Role.PROCTOR], read_only=True,
        )
        match = await _seeded_match()
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/matches/{match.id}/stations/suggest')
            assert resp.status_code == 403

    async def test_a_role_less_token_cannot_draw_a_seating(self, db, app):
        _, raw = await create_user_token(username='nobody')
        match = await _seeded_match()
        await Station.create(name='1', side=StationSide.LEFT, position=1)
        await Station.create(name='2', side=StationSide.RIGHT, position=1)
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/matches/{match.id}/stations/suggest')
            assert resp.status_code == 403

    async def test_proctor_token_passes_the_seed_gate(self, db, app):
        """The tournament has no generator, so /seed 400s on configuration — the
        point is that it is not the 'no permission' rejection a denied gate gives."""
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        match = await _seeded_match()
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/matches/{match.id}/seed')
            assert resp.status_code == 400
            assert 'permission' not in resp.json()['detail'].lower()


class TestReviewFlag:
    """``POST /matches/{id}/review`` — the dispute flag over REST.

    Both directions on one route, but two different gates: flagging is the
    proctor's own action, clearing is the admin's.
    """

    async def _finished(self, client, match):
        await client.post(f'/api/matches/{match.id}/seat')
        await client.post(f'/api/matches/{match.id}/start')
        await client.post(f'/api/matches/{match.id}/finish')
        winner = await MatchPlayers.filter(match_id=match.id).first()
        await client.post(f'/api/matches/{match.id}/result', json={'winner_id': winner.id})

    async def test_flag_round_trips_onto_the_match_response(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        match = await _seeded_match()
        async with client_for(app, raw) as c:
            await self._finished(c, match)

            resp = await c.post(
                f'/api/matches/{match.id}/review', json={'needs_review': True},
            )
            assert resp.status_code == 200
            assert resp.json()['needs_review'] is True

            fetched = await c.get(f'/api/matches/{match.id}')
            assert fetched.json()['needs_review'] is True

    async def test_flagging_an_unfinished_match_is_400(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        match = await _seeded_match()
        async with client_for(app, raw) as c:
            resp = await c.post(
                f'/api/matches/{match.id}/review', json={'needs_review': True},
            )
            assert resp.status_code == 400
            assert 'Only a finished match' in resp.json()['detail']

    async def test_proctor_cannot_clear_the_flag(self, db, app):
        _, proc_raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        match = await _seeded_match()
        async with client_for(app, proc_raw) as c:
            await self._finished(c, match)
            await c.post(
                f'/api/matches/{match.id}/review', json={'needs_review': True},
            )

            resp = await c.post(
                f'/api/matches/{match.id}/review', json={'needs_review': False},
            )
            assert resp.status_code == 403

        await match.refresh_from_db()
        assert match.needs_review is True

    async def test_staff_clears_the_flag(self, db, app):
        _, proc_raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
        match = await _seeded_match()
        async with client_for(app, proc_raw) as c:
            await self._finished(c, match)
            await c.post(
                f'/api/matches/{match.id}/review', json={'needs_review': True},
            )

        async with client_for(app, staff_raw) as c:
            resp = await c.post(
                f'/api/matches/{match.id}/review', json={'needs_review': False},
            )
            assert resp.status_code == 200
            assert resp.json()['needs_review'] is False

    async def test_confirming_clears_the_flag_over_rest(self, db, app):
        _, proc_raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
        match = await _seeded_match()
        async with client_for(app, proc_raw) as c:
            await self._finished(c, match)
            await c.post(
                f'/api/matches/{match.id}/review', json={'needs_review': True},
            )

        async with client_for(app, staff_raw) as c:
            assert (await c.post(f'/api/matches/{match.id}/confirm')).status_code == 200
            body = (await c.get(f'/api/matches/{match.id}')).json()
            assert body['needs_review'] is False


class TestCrewAndAck:
    async def test_signup_approve_acknowledge(self, db, app):
        _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
        caster, caster_raw = await create_user_token(username='caster')
        t, p1, p2 = await _tournament_and_players()

        async with client_for(app, staff_raw) as staff, client_for(app, caster_raw) as caster_c:
            mid = (await _create_match(staff, t, p1, p2)).json()['id']

            signup = await caster_c.post(f'/api/matches/{mid}/crew', json={'role': 'commentator'})
            assert signup.status_code == 201

            crew = await Commentator.filter(match_id=mid, user_id=caster.id).first()
            self_approve = await caster_c.post(
                f'/api/crew/commentator/{crew.id}/approval', json={'approved': True}
            )
            assert self_approve.status_code == 403

            assert (await staff.post(
                f'/api/crew/commentator/{crew.id}/approval', json={'approved': True}
            )).status_code == 200
            assert (await caster_c.post(
                f'/api/crew/commentator/{crew.id}/acknowledge'
            )).status_code == 200

    async def test_player_acknowledges_match(self, db, app):
        _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t, p1, p2 = await _tournament_and_players()
        _, p1_raw = await ApiToken_for(p1)

        async with client_for(app, staff_raw) as staff, client_for(app, p1_raw) as player:
            mid = (await _create_match(staff, t, p1, p2)).json()['id']
            resp = await player.post(f'/api/matches/{mid}/acknowledge')
            assert resp.status_code == 200


async def ApiToken_for(user: User):
    """Mint a token for an already-created user."""
    from application.services.api_token_service import ApiTokenService
    return await ApiTokenService().create_token(user, name='test')


class TestBroadcastTechStageBoundary:
    """A Broadcast Tech runs a match only while it is on a stage.

    Proctor Station shows them stage matches only; this is the server half, so
    a token holder can't reach an off-stage match the board leaves out.
    """

    async def test_seats_a_match_on_a_stage(self, db, app):
        from models import Stage

        _, raw = await create_user_token(username='tech', roles=[Role.STREAM_MANAGER])
        match = await _seeded_match()
        stage = await Stage.create(name='Main Stage', is_active=True)
        await Match.filter(id=match.id).update(stage_id=stage.id)
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/matches/{match.id}/seat')).status_code == 200

    async def test_is_refused_an_off_stage_match(self, db, app):
        _, raw = await create_user_token(username='tech', roles=[Role.STREAM_MANAGER])
        match = await _seeded_match()
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/matches/{match.id}/seat')).status_code == 403
        await match.refresh_from_db()
        assert match.seated_at is None
