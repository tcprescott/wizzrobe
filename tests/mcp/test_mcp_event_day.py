"""Event-day tools: the check-in desk, volunteer arrivals, stream offers, join
requests, and the caller's own shifts, reschedules and checkouts.

The connection gate for every write is covered in ``test_mcp_writes.py``; these
assert each tool reaches its service and that the write lands.
"""

from datetime import datetime, timedelta, timezone

from models import (
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEvent,
    CheckInEventStatus,
    Equipment,
    EquipmentLoan,
    FeatureFlag,
    JoinRequestStatus,
    Match,
    MatchPlayers,
    MatchStreamVolunteer,
    Role,
    TenantFeatureFlag,
    TenantJoinRequest,
    Tournament,
    User,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)
from tests.api_helpers import create_community_member
from tests.mcp.conftest import call_tool, create_oauth_token, list_tools, mcp_session

TENANT = 'default'


async def _shift_for(user: User, *, hours_from_now: int = 24) -> VolunteerAssignment:
    position, _ = await VolunteerPosition.get_or_create(name='Desk')
    start = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    shift = await VolunteerShift.create(position=position, starts_at=start, ends_at=start + timedelta(hours=4))
    return await VolunteerAssignment.create(shift=shift, user=user)


class TestCheckInDesk:
    async def test_find_then_check_in(self, db):
        desk, raw = await create_oauth_token(roles=[Role.CHECK_IN_DESK], write=True)
        event = await CheckInEvent.create(name='SG Live', status=CheckInEventStatus.OPEN)
        await CheckInEntrant.create(event=event, source=CheckInEntrantSource.WALK_UP, display_name='Alice')
        bob = await CheckInEntrant.create(event=event, source=CheckInEntrantSource.WALK_UP, display_name='Bob')
        async with mcp_session() as client:
            is_error, events = await call_tool(client, raw, 'list_check_in_events', tenant=TENANT)
            assert not is_error, events
            assert [e['id'] for e in events['result']] == [event.id]

            is_error, roster = await call_tool(
                client, raw, 'get_check_in_roster', tenant=TENANT, event_id=event.id, search='bo',
            )
            assert not is_error, roster
            assert [r['id'] for r in roster['result']] == [bob.id]

            is_error, result = await call_tool(
                client, raw, 'check_in_attendee', tenant=TENANT, entrant_id=bob.id,
            )
            assert not is_error, result
            assert result['already'] is False
        await bob.refresh_from_db()
        assert bob.checked_in_at is not None
        assert bob.checked_in_by_id == desk.id

    async def test_a_withdrawn_row_is_refused_for_the_desk(self, db):
        _, raw = await create_oauth_token(roles=[Role.CHECK_IN_DESK], write=True)
        event = await CheckInEvent.create(name='SG Live', status=CheckInEventStatus.OPEN)
        row = await CheckInEntrant.create(
            event=event, source=CheckInEntrantSource.MATCHERINO, matcherino_user_id='m1',
            display_name='Refunded', withdrawn_at=datetime.now(timezone.utc),
        )
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'check_in_attendee', tenant=TENANT, entrant_id=row.id,
                despite_withdrawn=True,
            )
        assert is_error and 'forbidden:' in payload, payload
        await row.refresh_from_db()
        assert row.checked_in_at is None

    async def test_the_flag_hides_the_tools(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        await TenantFeatureFlag.filter(flag=FeatureFlag.EVENT_CHECK_IN.value).update(enabled=False)
        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'list_check_in_events', tenant=TENANT)
        assert is_error, payload


class TestVolunteers:
    async def test_my_shifts_lists_only_mine(self, db):
        me, raw = await create_oauth_token(roles=[Role.VOLUNTEER])
        other = await create_community_member(username='someone-else')
        mine = await _shift_for(me)
        await _shift_for(other)
        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'my_volunteer_shifts', tenant=TENANT)
        assert not is_error, payload
        assert [s['assignment_id'] for s in payload['result']] == [mine.id]
        assert payload['result'][0]['position'] == 'Desk'

    async def test_a_coordinator_records_arrival(self, db):
        coordinator, raw = await create_oauth_token(roles=[Role.VOLUNTEER_COORDINATOR], write=True)
        volunteer = await create_community_member(username='vol', dm_notifications=False)
        assignment = await _shift_for(volunteer, hours_from_now=0)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'check_in_volunteer', tenant=TENANT, assignment_id=assignment.id,
            )
        assert not is_error, payload
        await assignment.refresh_from_db()
        assert assignment.checked_in_by_id == coordinator.id


class TestStreamOffer:
    async def test_a_player_offers_their_match(self, db):
        player, raw = await create_oauth_token(roles=[Role.VOLUNTEER], write=True)
        tournament = await Tournament.create(name='SGL Open', is_active=True)
        match = await Match.create(
            tournament=tournament, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        await MatchPlayers.create(match=match, user=player)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'offer_match_for_stream', tenant=TENANT, match_id=match.id,
            )
            assert not is_error, payload
            assert await MatchStreamVolunteer.filter(match=match, user=player).exists()
            is_error, payload = await call_tool(
                client, raw, 'withdraw_stream_offer', tenant=TENANT, match_id=match.id,
            )
            assert not is_error and payload['ok'] is True, payload
        assert not await MatchStreamVolunteer.filter(match=match, user=player).exists()


class TestJoinRequests:
    async def test_staff_list_and_approve(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        asker = await User.create(discord_id=5151, username='asker')
        request = await TenantJoinRequest.create(user=asker)
        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'list_join_requests', tenant=TENANT)
            assert not is_error, payload
            assert [r['id'] for r in payload['result']] == [request.id]
            is_error, payload = await call_tool(
                client, raw, 'approve_join_request', tenant=TENANT, request_id=request.id,
            )
            assert not is_error, payload
        await request.refresh_from_db()
        assert request.status == JoinRequestStatus.APPROVED

    async def test_join_requests_are_hidden_below_staff(self, db):
        _, raw = await create_oauth_token(roles=[Role.CHECK_IN_DESK])
        async with mcp_session() as client:
            names = {t['name'] for t in await list_tools(client, raw)}
            is_error, payload = await call_tool(client, raw, 'list_join_requests', tenant=TENANT)
        assert 'list_join_requests' in names
        assert is_error and 'forbidden:' in payload, payload


class TestMine:
    async def test_my_reschedule_requests_starts_empty(self, db):
        _, raw = await create_oauth_token(roles=[Role.VOLUNTEER])
        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'my_reschedule_requests', tenant=TENANT)
        assert not is_error, payload
        assert payload['result'] == []

    async def test_my_equipment_checkouts(self, db):
        me, raw = await create_oauth_token(roles=[Role.VOLUNTEER])
        asset = await Equipment.create(asset_number=7, name='Controller')
        await EquipmentLoan.create(equipment=asset, borrower=me, checked_out_by=me)
        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'my_equipment_checkouts', tenant=TENANT)
        assert not is_error, payload
        assert [r['asset_number'] for r in payload['result']] == [7]
