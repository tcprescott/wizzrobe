"""REST tests for the event-day endpoints: the check-in desk, volunteer
arrivals, stream offers and join requests.

The services own the rules; these assert the routes reach them with the same
answers the web UI gets, and that each one is fenced the way its page is.
"""

import json
from datetime import datetime, timedelta, timezone

from application.services.audit_service import AuditActions
from models import (
    AuditLog,
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEvent,
    CheckInEventStatus,
    FeatureFlag,
    JoinRequestStatus,
    Match,
    MatchPlayers,
    MatchStreamVolunteer,
    Role,
    Tenant,
    TenantFeatureFlag,
    TenantJoinRequest,
    TenantMembership,
    Tournament,
    User,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)
from tests.api_helpers import client_for, create_community_member, create_user_token
from tests.factories import utc


async def _event(status: CheckInEventStatus = CheckInEventStatus.OPEN, name: str = 'SG Live') -> CheckInEvent:
    return await CheckInEvent.create(name=name, status=status)


async def _entrant(event: CheckInEvent, name: str, **fields) -> CheckInEntrant:
    return await CheckInEntrant.create(
        event=event, source=fields.pop('source', CheckInEntrantSource.WALK_UP),
        display_name=name, **fields,
    )


class TestCheckInDesk:
    async def test_a_desk_volunteer_sees_only_open_events(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        open_event = await _event()
        await _event(CheckInEventStatus.DRAFT, name='Setup')
        async with client_for(app, raw) as c:
            events = (await c.get('/api/check-in/events')).json()
        assert [e['id'] for e in events] == [open_event.id]

    async def test_staff_see_every_event(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        await _event()
        await _event(CheckInEventStatus.DRAFT, name='Setup')
        async with client_for(app, raw) as c:
            assert len((await c.get('/api/check-in/events')).json()) == 2

    async def test_a_plain_member_is_refused(self, db, app):
        _, raw = await create_user_token(username='plain', member=True)
        event = await _event()
        async with client_for(app, raw) as c:
            assert (await c.get('/api/check-in/events')).status_code == 403
            assert (await c.get(f'/api/check-in/events/{event.id}/roster')).status_code == 403

    async def test_a_desk_volunteer_cannot_read_a_draft_roster(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        draft = await _event(CheckInEventStatus.DRAFT)
        async with client_for(app, raw) as c:
            assert (await c.get(f'/api/check-in/events/{draft.id}/roster')).status_code == 404

    async def test_roster_then_check_in_twice(self, db, app):
        desk, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        event = await _event()
        row = await _entrant(event, 'Alice')
        async with client_for(app, raw) as c:
            roster = (await c.get(f'/api/check-in/events/{event.id}/roster')).json()
            assert [(r['id'], r['checked_in_at']) for r in roster] == [(row.id, None)]

            first = await c.post(f'/api/check-in/entrants/{row.id}/check-in')
            assert first.status_code == 200, first.text
            assert first.json()['already'] is False
            assert first.json()['entrant']['checked_in_by_name'] == desk.preferred_name

            second = (await c.post(f'/api/check-in/entrants/{row.id}/check-in')).json()
            assert second['already'] is True

            undone = await c.delete(f'/api/check-in/entrants/{row.id}/check-in')
            assert undone.status_code == 200
            assert undone.json()['checked_in_at'] is None

    async def test_a_withdrawn_row_needs_staff(self, db, app):
        _, desk_raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        _, staff_raw = await create_user_token(username='staff', roles=[Role.STAFF])
        event = await _event()
        row = await _entrant(
            event, 'Refunded', source=CheckInEntrantSource.MATCHERINO,
            matcherino_user_id='m1', withdrawn_at=datetime.now(timezone.utc),
        )
        async with client_for(app, desk_raw) as c:
            assert (await c.post(f'/api/check-in/entrants/{row.id}/check-in')).status_code == 400
            assert (await c.post(
                f'/api/check-in/entrants/{row.id}/check-in', json={'despite_withdrawn': True},
            )).status_code == 403
        async with client_for(app, staff_raw) as c:
            resp = await c.post(
                f'/api/check-in/entrants/{row.id}/check-in', json={'despite_withdrawn': True},
            )
            assert resp.status_code == 200, resp.text
        logs = await AuditLog.filter(action=AuditActions.CHECK_IN_ENTRANT_CHECKED_IN)
        assert [json.loads(log.details)['despite_withdrawn'] for log in logs] == [True]

    async def test_walk_ups_are_staff_only(self, db, app):
        _, desk_raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        _, staff_raw = await create_user_token(username='staff', roles=[Role.STAFF])
        event = await _event()
        async with client_for(app, desk_raw) as c:
            assert (await c.post(
                f'/api/check-in/events/{event.id}/walk-ups', json={'name': 'Bob'},
            )).status_code == 403
        async with client_for(app, staff_raw) as c:
            resp = await c.post(f'/api/check-in/events/{event.id}/walk-ups', json={'name': 'Bob'})
            assert resp.status_code == 201, resp.text
            assert resp.json()['source'] == 'walk_up'
            assert resp.json()['checked_in_at'] is not None

    async def test_link_and_unlink(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        member = await create_community_member(username='carol')
        event = await _event()
        row = await _entrant(event, 'Carol', source=CheckInEntrantSource.MATCHERINO, matcherino_user_id='m2')
        async with client_for(app, raw) as c:
            linked = await c.put(f'/api/check-in/entrants/{row.id}/user', json={'user_id': member.id})
            assert linked.status_code == 200, linked.text
            assert linked.json()['user_name'] == member.preferred_name
            unlinked = (await c.delete(f'/api/check-in/entrants/{row.id}/user')).json()
            assert unlinked['user_id'] is None

    async def test_linking_someone_outside_the_community_reads_as_unknown(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        outsider = await User.create(username='outsider', discord_id=987654)
        event = await _event()
        row = await _entrant(event, 'Mallory', source=CheckInEntrantSource.MATCHERINO, matcherino_user_id='m9')
        async with client_for(app, raw) as c:
            resp = await c.put(f'/api/check-in/entrants/{row.id}/user', json={'user_id': outsider.id})
        assert resp.status_code == 404, resp.text
        await outsider.refresh_from_db()
        assert outsider.matcherino_user_id is None
        assert outsider.matcherino_username is None

    async def test_a_desk_volunteer_cannot_touch_a_draft_or_closed_roster(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        for status in (CheckInEventStatus.DRAFT, CheckInEventStatus.CLOSED):
            row = await _entrant(await _event(status, name=status.value), 'Erin')
            async with client_for(app, raw) as c:
                assert (await c.post(f'/api/check-in/entrants/{row.id}/check-in')).status_code == 404
                assert (await c.delete(f'/api/check-in/entrants/{row.id}/check-in')).status_code == 404
                assert (await c.delete(f'/api/check-in/entrants/{row.id}/user')).status_code == 404
            await row.refresh_from_db()
            assert row.checked_in_at is None

    async def test_staff_still_work_a_draft_roster(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        row = await _entrant(await _event(CheckInEventStatus.DRAFT), 'Finn')
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/check-in/entrants/{row.id}/check-in')).status_code == 200

    async def test_read_only_token_cannot_check_in(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK], read_only=True)
        row = await _entrant(await _event(), 'Dana')
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/check-in/entrants/{row.id}/check-in')).status_code == 403

    async def test_the_feature_flag_hides_the_router(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        await TenantFeatureFlag.filter(flag=FeatureFlag.EVENT_CHECK_IN.value).update(enabled=False)
        async with client_for(app, raw) as c:
            assert (await c.get('/api/check-in/events')).status_code == 404


class TestVolunteerArrival:
    async def _assignment(self) -> VolunteerAssignment:
        volunteer = await create_community_member(username='vol', dm_notifications=False)
        position = await VolunteerPosition.create(name='Desk')
        shift = await VolunteerShift.create(
            position=position, starts_at=utc(2026, 10, 17, 12), ends_at=utc(2026, 10, 17, 16),
        )
        return await VolunteerAssignment.create(shift=shift, user=volunteer)

    async def test_a_coordinator_records_arrival_once(self, db, app):
        coordinator, raw = await create_user_token(username='coord', roles=[Role.VOLUNTEER_COORDINATOR])
        assignment = await self._assignment()
        async with client_for(app, raw) as c:
            first = await c.post(f'/api/volunteers/assignments/{assignment.id}/check-in')
            assert first.status_code == 200, first.text
            assert first.json()['checked_in_by_id'] == coordinator.id
            again = (await c.post(f'/api/volunteers/assignments/{assignment.id}/check-in')).json()
            assert again['checked_in_at'] == first.json()['checked_in_at']

    async def test_the_volunteer_cannot_check_themselves_in(self, db, app):
        assignment = await self._assignment()
        await assignment.fetch_related('user')
        _, raw = await create_user_token(username='other-vol', roles=[Role.VOLUNTEER])
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/volunteers/assignments/{assignment.id}/check-in')
            assert resp.status_code == 403


class TestStreamOffer:
    async def _match(self, player: User) -> Match:
        tournament = await Tournament.create(name='SGL Open', is_active=True)
        match = await Match.create(
            tournament=tournament, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        await MatchPlayers.create(match=match, user=player)
        return match

    async def test_a_player_offers_and_withdraws(self, db, app):
        player, raw = await create_user_token(username='player', member=True)
        match = await self._match(player)
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/matches/{match.id}/stream-offer')).status_code == 200
            assert await MatchStreamVolunteer.filter(match=match, user=player).exists()
            assert (await c.delete(f'/api/matches/{match.id}/stream-offer')).status_code == 204
        assert not await MatchStreamVolunteer.filter(match=match, user=player).exists()

    async def test_someone_not_in_the_match_is_refused(self, db, app):
        player = await create_community_member(username='player')
        match = await self._match(player)
        _, raw = await create_user_token(username='bystander', member=True)
        async with client_for(app, raw) as c:
            assert (await c.post(f'/api/matches/{match.id}/stream-offer')).status_code == 400


class TestJoinRequests:
    async def test_staff_list_and_approve(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        asker = await User.create(discord_id=4242, username='asker')
        request = await TenantJoinRequest.create(user=asker, message='hi')
        async with client_for(app, raw) as c:
            pending = (await c.get('/api/join-requests')).json()
            assert [(r['id'], r['message']) for r in pending] == [(request.id, 'hi')]
            resp = await c.post(f'/api/join-requests/{request.id}/approve')
            assert resp.status_code == 200, resp.text
            assert resp.json()['status'] == JoinRequestStatus.APPROVED.value
            assert (await c.post(f'/api/join-requests/{request.id}/deny')).status_code == 400
        assert await TenantMembership.filter(user=asker).exists()

    async def test_staff_deny(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        asker = await User.create(discord_id=4243, username='asker')
        request = await TenantJoinRequest.create(user=asker)
        async with client_for(app, raw) as c:
            resp = await c.post(f'/api/join-requests/{request.id}/deny')
            assert resp.json()['status'] == JoinRequestStatus.DENIED.value
        assert not await TenantMembership.filter(user=asker).exists()

    async def test_non_staff_are_refused(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        asker = await User.create(discord_id=4244, username='asker')
        request = await TenantJoinRequest.create(user=asker)
        async with client_for(app, raw) as c:
            assert (await c.get('/api/join-requests')).status_code == 403
            assert (await c.post(f'/api/join-requests/{request.id}/approve')).status_code == 403

    async def test_another_communitys_request_is_not_found(self, db, app):
        _, raw = await create_user_token(username='staff', roles=[Role.STAFF])
        other = await Tenant.create(name='Beta', slug='beta')
        asker = await User.create(discord_id=4245, username='asker')
        request = await TenantJoinRequest.create(user=asker, tenant=other)
        async with client_for(app, raw) as c:
            assert (await c.get('/api/join-requests')).json() == []
            assert (await c.post(f'/api/join-requests/{request.id}/approve')).status_code == 404
