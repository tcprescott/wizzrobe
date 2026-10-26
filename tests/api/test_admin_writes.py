"""Tests for Phase 4 user/role & tournament admin write endpoints.

Focus: tokens inherit their user's permissions/scope (Staff gates, and a
Tournament Admin token can edit only its own tournament)."""


from models import Role, Tournament, User
from tests.api_helpers import client_for, create_user_token


class TestUserAndRoleWrites:
    async def test_staff_creates_user(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/users', json={'username': 'newbie', 'discord_id': 5050})
            assert resp.status_code == 201
            assert resp.json()['username'] == 'newbie'

    async def test_non_staff_cannot_create_user(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            resp = await c.post('/api/users', json={'username': 'x', 'discord_id': 6060})
            assert resp.status_code == 403

    async def test_self_update_changes_display_name(self, db, app):
        _, raw = await create_user_token(username='me')
        async with client_for(app, raw) as c:
            resp = await c.patch('/api/users/me', json={'display_name': 'Cool Name'})
            assert resp.status_code == 200
            assert resp.json()['display_name'] == 'Cool Name'

    async def test_grant_and_revoke_role(self, db, app):
        _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
        target = await User.create(discord_id=321, username='target')
        async with client_for(app, staff_raw) as c:
            granted = await c.post(f'/api/users/{target.id}/roles', json={'role': 'proctor'})
            assert granted.status_code == 200
            detail = await c.get(f'/api/users/{target.id}')
            assert 'proctor' in detail.json()['roles']

            revoked = await c.delete(f'/api/users/{target.id}/roles/proctor')
            assert revoked.status_code == 204

    async def test_non_staff_cannot_grant_role(self, db, app):
        _, raw = await create_user_token(username='plain')
        target = await User.create(discord_id=999, username='target')
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/users/{target.id}/roles', json={'role': 'staff'})
            assert resp.status_code == 403


class TestTournamentWrites:
    async def test_staff_crud_tournament(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            created = await c.post('/api/tournaments', json={'name': 'New Cup'})
            assert created.status_code == 201
            tid = created.json()['id']

            updated = await c.patch(f'/api/tournaments/{tid}', json={'name': 'Renamed Cup'})
            assert updated.status_code == 200
            assert updated.json()['name'] == 'Renamed Cup'

            # Both delete gates apply to the API too: an active tournament is
            # refused, and so is a wrong confirmation phrase.
            active = await c.delete(
                f'/api/tournaments/{tid}?confirmation=permanently+delete'
            )
            assert active.status_code == 400

            await c.patch(f'/api/tournaments/{tid}', json={'is_active': False})
            assert (await c.delete(f'/api/tournaments/{tid}')).status_code == 422
            wrong = await c.delete(f'/api/tournaments/{tid}?confirmation=delete')
            assert wrong.status_code == 400

            deleted = await c.delete(
                f'/api/tournaments/{tid}?confirmation=permanently+delete'
            )
            assert deleted.status_code == 204
            assert (await c.get(f'/api/tournaments/{tid}')).status_code == 404

    async def test_staff_set_a_stage_best_of(self, db, app):
        from models import ChallongeMatch, ChallongeMatchState, ChallongeParticipant
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        t = await Tournament.create(name='Cup', challonge_tournament_id='C1')
        a = await ChallongeParticipant.create(tournament=t, challonge_participant_id='1', name='A')
        b = await ChallongeParticipant.create(tournament=t, challonge_participant_id='2', name='B')
        for n, r in enumerate((1, 1, 2)):
            await ChallongeMatch.create(
                tournament=t, challonge_match_id=f'm{n}', round=r,
                state=ChallongeMatchState.OPEN, participant1=a, participant2=b,
            )
        async with client_for(app, raw) as c:
            listed = await c.get(f'/api/tournaments/{t.id}/round-schedule')
            assert listed.status_code == 200
            assert [s['key'] for s in listed.json()] == ['e1', 'e0']
            put = await c.put(f'/api/tournaments/{t.id}/round-schedule/e0/best-of', json={'best_of': 3})
            assert put.status_code == 200 and put.json()['best_of'] == 3
            assert (await c.put(
                f'/api/tournaments/{t.id}/round-schedule/e0/best-of', json={'best_of': 2},
            )).status_code == 422
            assert (await c.put(
                f'/api/tournaments/{t.id}/round-schedule/gf/best-of', json={'best_of': 3},
            )).status_code == 400
        await t.refresh_from_db()
        assert t.config['round_schedule'] == {'e0': {'best_of': 3}}

    async def test_non_staff_cannot_create_tournament(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.post('/api/tournaments', json={'name': 'X'})).status_code == 403

    async def test_create_and_clear_tournament_days(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            created = await c.post('/api/tournaments', json={
                'name': 'Days Cup',
                'event_start_date': '2025-10-20',
                'event_end_date': '2025-10-22',
                'tournament_hours': {'2025-10-20': ['09:00', '17:00']},
            })
            assert created.status_code == 201
            body = created.json()
            tid = body['id']
            assert body['event_start_date'] == '2025-10-20'
            assert body['event_end_date'] == '2025-10-22'
            assert body['tournament_hours'] == {'2025-10-20': {'open': '09:00', 'close': '17:00'}}

            # Explicit null clears an override back to inheriting the tenant setting.
            cleared = await c.patch(f'/api/tournaments/{tid}', json={
                'event_start_date': None, 'tournament_hours': None,
            })
            assert cleared.status_code == 200
            assert cleared.json()['event_start_date'] is None
            assert cleared.json()['tournament_hours'] is None
            # An untouched field is preserved (only start/hours were sent).
            assert cleared.json()['event_end_date'] == '2025-10-22'

    async def test_create_tournament_days_rejects_bad_window(self, db, app):
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        async with client_for(app, raw) as c:
            resp = await c.post('/api/tournaments', json={
                'name': 'Bad Cup',
                'event_start_date': '2025-10-22',
                'event_end_date': '2025-10-20',
            })
            assert resp.status_code == 400

    async def test_tournament_admin_token_scope(self, db, app):
        """A token inherits its user's Tournament Admin scope: the TA can edit
        their tournament; an unrelated user cannot."""
        _, staff_raw = await create_user_token(username='boss', roles=[Role.STAFF])
        ta_user, ta_raw = await create_user_token(username='ta')
        _, outsider_raw = await create_user_token(username='outsider')
        t = await Tournament.create(name='Cup')

        async with client_for(app, staff_raw) as staff, \
                client_for(app, ta_raw) as ta, \
                client_for(app, outsider_raw) as outsider:
            # Staff grants TA membership.
            added = await staff.post(f'/api/tournaments/{t.id}/admins', json={'user_id': ta_user.id})
            assert added.status_code == 200

            # TA can edit their tournament...
            edited = await ta.patch(f'/api/tournaments/{t.id}', json={'name': 'TA Edit'})
            assert edited.status_code == 200

            # ...but an unrelated user cannot.
            blocked = await outsider.patch(f'/api/tournaments/{t.id}', json={'name': 'Nope'})
            assert blocked.status_code == 403


class TestTournamentConfigAndRoster:
    async def test_update_sets_and_clears_configuration(self, db, app):
        from models import Preset
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        preset = await Preset.create(name='sgl2026', randomizer='ffr', settings={})
        t = await Tournament.create(name='FFR')
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/tournaments/{t.id}', json={
                'preset_id': preset.id, 'is_test': True, 'stage_reminder_minutes': 10,
                'allow_player_match_requests': False,
            })
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body['preset_id'] == preset.id
            assert body['seed_generator'] == 'ffr'
            assert body['is_test'] is True
            assert body['stage_reminder_minutes'] == 10
            assert body['allow_player_match_requests'] is False

            cleared = await c.patch(f'/api/tournaments/{t.id}', json={'preset_id': None})
            assert cleared.status_code == 200
            assert cleared.json()['preset_id'] is None
            assert cleared.json()['is_test'] is True

    async def test_staff_manage_the_roster(self, db, app):
        from tests.api_helpers import create_community_member
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        player = await create_community_member(username='runner', dm_notifications=False)
        t = await Tournament.create(name='Cup')
        async with client_for(app, raw) as c:
            added = await c.post(f'/api/tournaments/{t.id}/players', json={'user_id': player.id})
            assert added.status_code == 200, added.text
            again = await c.post(f'/api/tournaments/{t.id}/players', json={'user_id': player.id})
            assert again.status_code == 400
            roster = await c.get(f'/api/tournaments/{t.id}/players')
            assert [u['id'] for u in roster.json()] == [player.id]
            removed = await c.delete(f'/api/tournaments/{t.id}/players/{player.id}')
            assert removed.status_code == 204
            assert (await c.get(f'/api/tournaments/{t.id}/players')).json() == []

    async def test_a_player_cannot_manage_the_roster(self, db, app):
        _, raw = await create_user_token(username='plain')
        target = await User.create(discord_id=4242, username='target')
        t = await Tournament.create(name='Cup')
        async with client_for(app, raw) as c:
            assert (await c.get(f'/api/tournaments/{t.id}/players')).status_code == 403
            resp = await c.post(f'/api/tournaments/{t.id}/players', json={'user_id': target.id})
            assert resp.status_code == 403

    async def test_another_communitys_tournament_is_not_found(self, db, app):
        from models import Tenant
        other = await Tenant.create(slug='elsewhere', name='Elsewhere')
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        theirs = await Tournament.create(name='Theirs', tenant_id=other.id)
        async with client_for(app, raw) as c:
            assert (await c.get(f'/api/tournaments/{theirs.id}/players')).status_code == 404
            resp = await c.patch(f'/api/tournaments/{theirs.id}', json={'is_test': True})
            assert resp.status_code == 404
            resp = await c.put(f'/api/tournaments/{theirs.id}/challonge', json={'challonge': 'abc'})
            assert resp.status_code == 404


class TestMembershipAndShiftWrites:
    async def test_staff_add_and_remove_a_member(self, db, app):
        from models import TenantMembership
        _, raw = await create_user_token(username='boss', roles=[Role.STAFF])
        person = await User.create(discord_id=7070, username='newcomer', dm_notifications=False)
        async with client_for(app, raw) as c:
            added = await c.post(f'/api/users/{person.id}/membership')
            assert added.status_code == 200, added.text
            assert await TenantMembership.filter(user=person, tenant_id=1).exists()
            removed = await c.delete(f'/api/users/{person.id}/membership')
            assert removed.status_code == 204
        assert not await TenantMembership.filter(user=person, tenant_id=1).exists()

    async def test_a_player_cannot_add_members(self, db, app):
        _, raw = await create_user_token(username='plain')
        person = await User.create(discord_id=7071, username='newcomer')
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/users/{person.id}/membership')).status_code == 403

    async def test_coordinator_updates_a_shift(self, db, app):
        from datetime import datetime, timedelta, timezone

        from models import VolunteerPosition, VolunteerShift
        _, raw = await create_user_token(username='coord', roles=[Role.VOLUNTEER_COORDINATOR])
        position = await VolunteerPosition.create(name='Desk')
        start = datetime.now(timezone.utc) + timedelta(days=1)
        shift = await VolunteerShift.create(position=position, starts_at=start, ends_at=start + timedelta(hours=2))
        async with client_for(app, raw) as c:
            resp = await c.patch(f'/api/volunteers/shifts/{shift.id}', json={'slots_needed': 4, 'notes': 'Bring a jacket'})
            assert resp.status_code == 200, resp.text
            assert resp.json()['slots_needed'] == 4
            assert resp.json()['notes'] == 'Bring a jacket'
            bad = await c.patch(f'/api/volunteers/shifts/{shift.id}', json={'slots_needed': 0})
            assert bad.status_code == 400
