"""The app's Work tab: your areas, the volunteer schedule grid, and the desk's reads."""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from application.services.work_service import FULL_SCHEDULE_PREFS_KEY, event_days, opening_day
from application.utils.timezone import combine_local, today_local
from models import (
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEvent,
    CheckInEventStatus,
    Event,
    FeatureFlag,
    Role,
    TenantFeatureFlag,
    VolunteerAssignment,
    VolunteerPosition,
    VolunteerShift,
)
from tests.api_helpers import client_for, create_community_member, create_user_token


@pytest.fixture(autouse=True)
async def _event_runs_this_week(db):
    today = today_local()
    await Event.filter(is_default=True).update(
        starts_on=today - timedelta(days=1), ends_on=today + timedelta(days=2),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _today_noon() -> datetime:
    """Inside the grid the tab opens on (06:00 to 06:00 local), whatever the hour."""
    return combine_local(today_local(), time(12, 0))


async def _shift(position: VolunteerPosition, start: datetime, hours: int = 2, slots: int = 2) -> VolunteerShift:
    return await VolunteerShift.create(
        position=position, starts_at=start, ends_at=start + timedelta(hours=hours), slots_needed=slots,
    )


class TestAreas:
    async def test_a_plain_member_has_no_areas(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            body = (await c.get('/api/work')).json()
        assert {k: body[k] for k in ('desk', 'schedule', 'availability', 'proctor')} == {
            'desk': None, 'schedule': None, 'availability': None, 'proctor': False,
        }

    async def test_a_volunteer_sees_schedule_and_availability(self, db, app):
        me, raw = await create_user_token(username='vol', roles=[Role.VOLUNTEER])
        other = await create_community_member(username='other')
        desk = await VolunteerPosition.create(name='Desk')
        now = _now()
        live = await _shift(desk, now - timedelta(hours=1))
        later = await _shift(desk, now + timedelta(hours=3))
        await VolunteerAssignment.create(shift=live, user=other)
        await VolunteerAssignment.create(shift=later, user=me)
        async with client_for(app, raw) as c:
            body = (await c.get('/api/work')).json()
        assert body['schedule']['on_shift_now'] == 1
        assert body['schedule']['my_next_shift_at'].startswith(later.starts_at.strftime('%Y-%m-%dT%H:%M'))
        assert body['availability'] == {'windows': 0}
        assert body['desk'] is None
        assert body['proctor'] is False

    async def test_desk_line_counts_the_one_open_event(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        event = await CheckInEvent.create(name='SG Live', status=CheckInEventStatus.OPEN)
        await CheckInEvent.create(name='Setup', status=CheckInEventStatus.DRAFT)
        await CheckInEntrant.create(event=event, source=CheckInEntrantSource.WALK_UP, display_name='A',
                                    checked_in_at=_now())
        await CheckInEntrant.create(event=event, source=CheckInEntrantSource.WALK_UP, display_name='B')
        await CheckInEntrant.create(event=event, source=CheckInEntrantSource.MATCHERINO, display_name='Gone',
                                    withdrawn_at=_now())
        async with client_for(app, raw) as c:
            desk = (await c.get('/api/work')).json()['desk']
        assert desk == {'open_events': 1, 'event_id': event.id, 'event_name': 'SG Live',
                        'checked_in': 1, 'expected': 2}

    async def test_a_proctor_gets_proctor_station(self, db, app):
        _, raw = await create_user_token(username='proc', roles=[Role.PROCTOR])
        async with client_for(app, raw) as c:
            assert (await c.get('/api/work')).json()['proctor'] is True

    async def test_flags_off_hide_the_areas(self, db, app):
        await TenantFeatureFlag.filter(
            flag__in=[FeatureFlag.VOLUNTEERS.value, FeatureFlag.EVENT_CHECK_IN.value],
        ).update(enabled=False)
        _, raw = await create_user_token(username='both', roles=[Role.VOLUNTEER, Role.CHECK_IN_DESK])
        async with client_for(app, raw) as c:
            body = (await c.get('/api/work')).json()
        assert body['schedule'] is None and body['availability'] is None and body['desk'] is None


class TestSchedule:
    async def test_grid_for_today_with_you_marked(self, db, app):
        me, raw = await create_user_token(username='vol', roles=[Role.VOLUNTEER])
        desk = await VolunteerPosition.create(name='Desk', display_order=1)
        runner = await VolunteerPosition.create(name='Runner', display_order=2)
        shift = await _shift(desk, _today_noon())
        await VolunteerAssignment.create(shift=shift, user=me)
        await _shift(runner, shift.starts_at)
        async with client_for(app, raw) as c:
            body = (await c.get('/api/work/schedule')).json()
        assert len(body['days']) == 4
        assert [c['position'] for c in body['columns']] == ['Desk', 'Runner']
        desk_shift = body['columns'][0]['shifts'][0]
        assert desk_shift['people'] == [{'name': me.preferred_name, 'is_viewer': True, 'called_off': False}]
        assert desk_shift['open_slots'] == 1
        assert body['first_hour'] <= desk_shift['start_hour'] < body['last_hour']

    async def test_before_six_opens_on_the_event_day_still_running(self, db, app, monkeypatch):
        import application.services.work_service as work_service
        from application.utils.timezone import now_local, to_utc_aware

        late = now_local().replace(hour=1, minute=30, second=0, microsecond=0)
        monkeypatch.setattr(work_service, 'now_local', lambda tz=None: late)
        me, raw = await create_user_token(username='owl', roles=[Role.VOLUNTEER])
        desk = await VolunteerPosition.create(name='Desk', display_order=1)
        shift = await _shift(desk, to_utc_aware(late.replace(hour=1, minute=0)))
        await VolunteerAssignment.create(shift=shift, user=me)
        async with client_for(app, raw) as c:
            body = (await c.get('/api/work/schedule')).json()
        assert body['day'] == (late.date() - timedelta(days=1)).isoformat()
        assert [c['position'] for c in body['columns']] == ['Desk']

    async def test_columns_save_hide_and_reset(self, db, app):
        _, raw = await create_user_token(username='vol', roles=[Role.VOLUNTEER])
        desk = await VolunteerPosition.create(name='Desk', display_order=1)
        runner = await VolunteerPosition.create(name='Runner', display_order=2)
        noon = _today_noon()
        await _shift(desk, noon)
        await _shift(runner, noon)
        async with client_for(app, raw) as c:
            roles = (await c.put('/api/work/schedule/columns', json={'columns': [
                {'position_id': runner.id, 'visible': True}, {'position_id': desk.id, 'visible': False},
            ]})).json()
            assert [(r['name'], r['visible']) for r in roles] == [('Runner', True), ('Desk', False)]
            body = (await c.get('/api/work/schedule')).json()
            assert [c['position'] for c in body['columns']] == ['Runner']
            assert body['hidden_with_shifts'] == 1 and body['customized'] is True
            reset = (await c.delete('/api/work/schedule/columns')).json()
            assert [r['name'] for r in reset] == ['Desk', 'Runner']

    async def test_a_plain_member_is_refused(self, db, app):
        _, raw = await create_user_token(username='plain')
        async with client_for(app, raw) as c:
            assert (await c.get('/api/work/schedule')).status_code == 403

    def test_prefs_key_matches_the_website(self):
        from theme.tables.preferences import TableKeys

        assert FULL_SCHEDULE_PREFS_KEY == TableKeys.VOLUNTEER_FULL_SCHEDULE

    def test_opening_day(self):
        days = event_days(date(2026, 10, 22), date(2026, 10, 25))
        assert len(days) == 4
        assert opening_day(days, date(2026, 10, 24), date(2026, 1, 1)) == date(2026, 10, 24)
        assert opening_day(days, None, date(2026, 10, 23)) == date(2026, 10, 23)
        assert opening_day(days, date(2027, 1, 1), date(2026, 1, 1)) == date(2026, 10, 22)
        assert opening_day([], None, date(2026, 1, 1)) is None


class TestDesk:
    async def test_desk_read_for_a_volunteer(self, db, app):
        _, raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        member = await create_community_member(username='carolyn')
        event = await CheckInEvent.create(name='SG Live', status=CheckInEventStatus.OPEN)
        linked = await CheckInEntrant.create(event=event, source=CheckInEntrantSource.WALK_UP,
                                             display_name='Dave', user=member)
        await CheckInEntrant.create(event=event, source=CheckInEntrantSource.MATCHERINO,
                                    display_name='Carolyn', matcherino_user_id='m1')
        async with client_for(app, raw) as c:
            body = (await c.get(f'/api/check-in/events/{event.id}/desk')).json()
            assert body['can_manage'] is False
            rows = {r['display_name']: r for r in body['rows']}
            assert rows['Dave']['username'] == 'carolyn'
            assert rows['Dave']['has_discord'] is True
            assert rows['Carolyn']['suggestions'] >= 0
            found = (await c.get(f'/api/check-in/events/{event.id}/members', params={'q': 'car'})).json()
            assert found == []
            linked_found = (await c.get(f'/api/check-in/events/{event.id}/members',
                                        params={'q': 'zz'})).json()
            assert linked_found == []
            resp = await c.post(f'/api/check-in/entrants/{linked.id}/test-dm')
            assert resp.status_code == 400

    async def test_member_search_skips_people_on_the_roster(self, db, app):
        _, raw = await create_user_token(username='staffer', roles=[Role.STAFF])
        await create_community_member(username='erin')
        on_roster = await create_community_member(username='erik')
        event = await CheckInEvent.create(name='SG Live', status=CheckInEventStatus.OPEN)
        await CheckInEntrant.create(event=event, source=CheckInEntrantSource.WALK_UP,
                                    display_name='Erik', user=on_roster)
        async with client_for(app, raw) as c:
            found = (await c.get(f'/api/check-in/events/{event.id}/members', params={'q': 'eri'})).json()
        assert [m['username'] for m in found] == ['erin']

    async def test_remove_is_staff_only_and_walk_ups_only(self, db, app):
        _, desk_raw = await create_user_token(username='desk', roles=[Role.CHECK_IN_DESK])
        _, staff_raw = await create_user_token(username='staffer', roles=[Role.STAFF])
        event = await CheckInEvent.create(name='SG Live', status=CheckInEventStatus.OPEN)
        walk_up = await CheckInEntrant.create(event=event, source=CheckInEntrantSource.WALK_UP, display_name='W')
        bought = await CheckInEntrant.create(event=event, source=CheckInEntrantSource.MATCHERINO,
                                             display_name='M', matcherino_user_id='m5')
        async with client_for(app, desk_raw) as c:
            assert (await c.delete(f'/api/check-in/entrants/{walk_up.id}')).status_code == 403
        async with client_for(app, staff_raw) as c:
            assert (await c.delete(f'/api/check-in/entrants/{bought.id}')).status_code == 400
            assert (await c.delete(f'/api/check-in/entrants/{walk_up.id}')).status_code == 204
        assert not await CheckInEntrant.filter(id=walk_up.id).exists()

    async def test_a_plain_member_is_refused(self, db, app):
        _, raw = await create_user_token(username='plain')
        event = await CheckInEvent.create(name='SG Live', status=CheckInEventStatus.OPEN)
        async with client_for(app, raw) as c:
            assert (await c.get(f'/api/check-in/events/{event.id}/desk')).status_code == 403
            assert (await c.get(f'/api/check-in/events/{event.id}/members', params={'q': 'ab'})).status_code == 403
