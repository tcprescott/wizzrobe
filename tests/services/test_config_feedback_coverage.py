"""Coverage tests for SystemConfigService and FeedbackService.

Exercises the typed accessors, JSON tournament-hours parsing/validation, the
event-window fallback chain, and the feedback list/review paths against the
in-memory SQLite ``db`` fixture so the real ORM contract is verified.
"""

import json
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

from application.services.audit_service import AuditActions
from application.services.feedback_service import PAGE_URL_MAX_LENGTH, FeedbackService
from application.services.system_config_service import (
    EVENT_CONFIG_KEYS,
    EVENT_DATE_FIELDS,
    KEY_EVENT_END_DATE,
    KEY_EVENT_START_DATE,
    KEY_MAX_CONCURRENT_PLAYERS,
    KEY_MAX_CONCURRENT_STAGES,
    KEY_STATION_FORMAT,
    KEY_TOURNAMENT_HOURS,
    KEY_VOLUNTEER_REMINDER_LEAD_MINUTES,
    SystemConfigService,
)
from application.utils.timezone import EASTERN_TZ
from models import (
    AuditLog,
    Event,
    Feedback,
    FeedbackCategory,
    FeedbackStatus,
    Match,
    Role,
    Stage,
    StationFormat,
    SystemConfiguration,
    TenantMembership,
    Tournament,
    User,
    UserRole,
)

UTC = timezone.utc


from tests.factories import utc


async def make_user(discord_id: int, username: str = 'user', staff: bool = False) -> User:
    user = await User.create(discord_id=discord_id, username=username, display_name=username)
    if staff:
        await UserRole.create(user=user, role=Role.STAFF)
    return user


async def set_config(name: str, value: str) -> None:
    # The event's own settings live on the default event, written raw so a test
    # can store what the service would refuse (an end before the start).
    if name in EVENT_DATE_FIELDS:
        await Event.filter(tenant_id=1, is_default=True).update(**{EVENT_DATE_FIELDS[name]: date.fromisoformat(value)})
    elif name in EVENT_CONFIG_KEYS:
        event = await Event.get(tenant_id=1, is_default=True)
        await Event.filter(id=event.id).update(config={**(event.config or {}), name: value})
    else:
        await SystemConfiguration.create(name=name, value=value)


class TestGetInt:
    async def test_parses_value(self, db):
        await set_config('n', '42')
        assert await SystemConfigService.get_int('n') == 42

    async def test_negative_value(self, db):
        await set_config('n', '-7')
        assert await SystemConfigService.get_int('n') == -7

    async def test_default_when_missing(self, db):
        assert await SystemConfigService.get_int('absent', default=9) == 9

    async def test_default_when_empty_string(self, db):
        await set_config('n', '')
        assert await SystemConfigService.get_int('n', default=3) == 3

    async def test_default_when_unparseable(self, db):
        await set_config('n', 'not-a-number')
        assert await SystemConfigService.get_int('n', default=5) == 5


class TestGetDate:
    async def test_valid_iso(self, db):
        await set_config('d', '2025-10-23')
        assert await SystemConfigService.get_date('d') == date(2025, 10, 23)

    async def test_default_when_missing(self, db):
        default = date(2025, 1, 1)
        assert await SystemConfigService.get_date('absent', default=default) == default

    async def test_default_when_blank(self, db):
        await set_config('d', '')
        assert await SystemConfigService.get_date('d', default=date(2030, 6, 1)) == date(2030, 6, 1)

    async def test_default_when_unparseable(self, db):
        await set_config('d', '10/23/2025')
        assert await SystemConfigService.get_date('d', default=None) is None


class TestSetRaw:
    async def test_creates_new_when_missing(self, db):
        staff = await make_user(1, staff=True)
        await SystemConfigService.set_raw('new_key', 'new_value', staff)
        assert await SystemConfigService.get_raw('new_key') == 'new_value'

    async def test_updates_existing(self, db):
        staff = await make_user(1, staff=True)
        await set_config('k', 'old')
        result = await SystemConfigService.set_raw('k', 'new', staff)
        assert result.value == 'new'
        assert await SystemConfigService.get_raw('k') == 'new'

    async def test_writes_audit_log(self, db):
        staff = await make_user(1, staff=True)
        await set_config('k', 'before')
        await SystemConfigService.set_raw('k', 'after', staff)
        log = await AuditLog.filter(action=AuditActions.SYSTEM_CONFIG_UPDATED).first()
        assert log is not None
        details = json.loads(log.details)
        assert details['key'] == 'k'
        assert details['old_value'] == 'before'
        assert details['new_value'] == 'after'

    async def test_non_staff_denied(self, db):
        plain = await make_user(2, staff=False)
        with pytest.raises(PermissionError):
            await SystemConfigService.set_raw('k', 'v', plain)
        assert await SystemConfigService.get_raw('k') is None


class TestGetEventWindow:
    async def test_uses_config_when_both_set(self, db):
        await set_config(KEY_EVENT_START_DATE, '2025-10-20')
        await set_config(KEY_EVENT_END_DATE, '2025-10-23')
        start, end = await SystemConfigService.get_event_window()
        assert start == date(2025, 10, 20)
        assert end == date(2025, 10, 23)

    async def test_falls_back_to_match_min_max(self, db):
        tournament = await Tournament.create(name='T')
        await Match.create(tournament=tournament, scheduled_at=utc(2025, 1, 15, 19, 30))
        await Match.create(tournament=tournament, scheduled_at=utc(2025, 1, 18, 19, 30))
        start, end = await SystemConfigService.get_event_window()
        assert start == date(2025, 1, 15)
        assert end == date(2025, 1, 18)

    async def test_partial_config_uses_match_for_missing_end(self, db):
        await set_config(KEY_EVENT_START_DATE, '2025-10-20')
        tournament = await Tournament.create(name='T')
        await Match.create(tournament=tournament, scheduled_at=utc(2025, 10, 25, 19, 30))
        start, end = await SystemConfigService.get_event_window()
        assert start == date(2025, 10, 20)
        assert end == date(2025, 10, 25)

    async def test_falls_back_to_today_when_no_config_or_matches(self, db):
        start, end = await SystemConfigService.get_event_window()
        assert start == end
        assert start == datetime.now(EASTERN_TZ).date()

    async def test_end_never_before_start(self, db):
        await set_config(KEY_EVENT_START_DATE, '2025-10-20')
        await set_config(KEY_EVENT_END_DATE, '2025-10-15')
        start, end = await SystemConfigService.get_event_window()
        assert start == date(2025, 10, 20)
        assert end == date(2025, 10, 20)


class TestGetMaxConcurrentPlayers:
    async def test_default_when_unset(self, db):
        assert await SystemConfigService.get_max_concurrent_players(default=60) == 60

    async def test_configured_value(self, db):
        await set_config(KEY_MAX_CONCURRENT_PLAYERS, '8')
        assert await SystemConfigService.get_max_concurrent_players() == 8

    async def test_zero_or_negative_uses_default(self, db):
        await set_config(KEY_MAX_CONCURRENT_PLAYERS, '0')
        assert await SystemConfigService.get_max_concurrent_players(default=42) == 42


class TestGetMaxConcurrentStages:
    async def test_configured_value(self, db):
        await set_config(KEY_MAX_CONCURRENT_STAGES, '3')
        assert await SystemConfigService.get_max_concurrent_stages() == 3

    async def test_explicit_default_when_unset(self, db):
        assert await SystemConfigService.get_max_concurrent_stages(default=4) == 4

    async def test_falls_back_to_active_stage_count(self, db):
        await Stage.create(name='A', is_active=True)
        await Stage.create(name='B', is_active=True)
        await Stage.create(name='C', is_active=False)
        assert await SystemConfigService.get_max_concurrent_stages() == 2


class TestGetVolunteerReminderLeadMinutes:
    async def test_default_when_unset(self, db):
        assert await SystemConfigService.get_volunteer_reminder_lead_minutes() == 60

    async def test_configured_value(self, db):
        await set_config(KEY_VOLUNTEER_REMINDER_LEAD_MINUTES, '30')
        assert await SystemConfigService.get_volunteer_reminder_lead_minutes() == 30

    async def test_zero_uses_default(self, db):
        await set_config(KEY_VOLUNTEER_REMINDER_LEAD_MINUTES, '0')
        assert await SystemConfigService.get_volunteer_reminder_lead_minutes(default=15) == 15


class TestGetStationFormat:
    async def test_valid_stored_value(self, db):
        await set_config(KEY_STATION_FORMAT, 'numeric')
        assert await SystemConfigService.get_station_format() == StationFormat.NUMERIC

    async def test_default_when_missing(self, db):
        assert await SystemConfigService.get_station_format() == StationFormat.FREE

    async def test_default_when_invalid(self, db):
        await set_config(KEY_STATION_FORMAT, 'not_a_format')
        assert await SystemConfigService.get_station_format() == StationFormat.FREE

    async def test_custom_default_when_missing(self, db):
        result = await SystemConfigService.get_station_format(default=StationFormat.STRUCTURED)
        assert result == StationFormat.STRUCTURED


class TestGetTournamentHours:
    async def test_empty_when_unset(self, db):
        assert await SystemConfigService.get_tournament_hours() == {}

    async def test_parses_valid_windows(self, db):
        await set_config(KEY_TOURNAMENT_HOURS, json.dumps({
            '2025-10-20': {'open': '09:00', 'close': '17:00'},
            '2025-10-21': {'open': '10:30', 'close': '22:00'},
        }))
        hours = await SystemConfigService.get_tournament_hours()
        assert hours == {
            date(2025, 10, 20): (time(9, 0), time(17, 0)),
            date(2025, 10, 21): (time(10, 30), time(22, 0)),
        }

    async def test_returns_empty_on_invalid_json(self, db):
        await set_config(KEY_TOURNAMENT_HOURS, 'not valid json {')
        assert await SystemConfigService.get_tournament_hours() == {}

    async def test_skips_invalid_date_time_and_missing_keys(self, db):
        await set_config(KEY_TOURNAMENT_HOURS, json.dumps({
            '2025-10-20': {'open': '09:00', 'close': '17:00'},  # kept
            'not-a-date': {'open': '09:00', 'close': '17:00'},  # bad date -> skip
            '2025-10-21': {'open': '25:99', 'close': '17:00'},  # bad time -> skip
            '2025-10-22': {'open': '09:00'},                    # missing close -> skip
        }))
        hours = await SystemConfigService.get_tournament_hours()
        assert hours == {date(2025, 10, 20): (time(9, 0), time(17, 0))}


class TestGetTournamentWindowForDate:
    async def test_returns_window_for_configured_date(self, db):
        await set_config(KEY_TOURNAMENT_HOURS, json.dumps({
            '2025-10-20': {'open': '09:00', 'close': '17:00'},
        }))
        window = await SystemConfigService.get_tournament_window_for_date(date(2025, 10, 20))
        assert window == (time(9, 0), time(17, 0))

    async def test_returns_none_for_unconfigured_date(self, db):
        assert await SystemConfigService.get_tournament_window_for_date(date(2025, 10, 20)) is None


def _tournament(*, hours=None, start=None, end=None):
    """A stub carrying only the attributes the resolver reads."""
    return SimpleNamespace(
        tournament_hours=hours, event_start_date=start, event_end_date=end,
    )


class TestTournamentHoursOverride:
    async def test_tournament_hours_override_replaces_tenant(self, db):
        # Tenant configures one window; the tournament configures another.
        await set_config(KEY_TOURNAMENT_HOURS, json.dumps({
            '2025-10-20': {'open': '09:00', 'close': '17:00'},
        }))
        tournament = _tournament(hours={'2025-10-20': {'open': '12:00', 'close': '20:00'}})
        hours = await SystemConfigService.get_tournament_hours(tournament)
        assert hours == {date(2025, 10, 20): (time(12, 0), time(20, 0))}

    async def test_falls_back_to_tenant_when_tournament_hours_none(self, db):
        await set_config(KEY_TOURNAMENT_HOURS, json.dumps({
            '2025-10-20': {'open': '09:00', 'close': '17:00'},
        }))
        tournament = _tournament(hours=None)
        hours = await SystemConfigService.get_tournament_hours(tournament)
        assert hours == {date(2025, 10, 20): (time(9, 0), time(17, 0))}

    async def test_window_for_date_uses_tournament_override(self, db):
        await set_config(KEY_TOURNAMENT_HOURS, json.dumps({
            '2025-10-20': {'open': '09:00', 'close': '17:00'},
        }))
        tournament = _tournament(hours={'2025-10-20': {'open': '12:00', 'close': '20:00'}})
        window = await SystemConfigService.get_tournament_window_for_date(
            date(2025, 10, 20), tournament=tournament,
        )
        assert window == (time(12, 0), time(20, 0))

    async def test_absent_day_in_tournament_override_is_unrestricted(self, db):
        # The tournament defines its own schedule; a date it omits is unrestricted
        # even though the tenant restricts it.
        await set_config(KEY_TOURNAMENT_HOURS, json.dumps({
            '2025-10-21': {'open': '09:00', 'close': '17:00'},
        }))
        tournament = _tournament(hours={'2025-10-20': {'open': '12:00', 'close': '20:00'}})
        window = await SystemConfigService.get_tournament_window_for_date(
            date(2025, 10, 21), tournament=tournament,
        )
        assert window is None


class TestEventWindowOverride:
    async def test_tournament_dates_win_over_tenant(self, db):
        await set_config(KEY_EVENT_START_DATE, '2025-10-20')
        await set_config(KEY_EVENT_END_DATE, '2025-10-23')
        tournament = _tournament(start=date(2025, 11, 1), end=date(2025, 11, 3))
        start, end = await SystemConfigService.get_event_window(tournament)
        assert (start, end) == (date(2025, 11, 1), date(2025, 11, 3))

    async def test_missing_bound_falls_back_to_tenant(self, db):
        await set_config(KEY_EVENT_START_DATE, '2025-10-20')
        await set_config(KEY_EVENT_END_DATE, '2025-10-23')
        # Only the start is overridden; the end inherits the tenant setting.
        tournament = _tournament(start=date(2025, 10, 22), end=None)
        start, end = await SystemConfigService.get_event_window(tournament)
        assert (start, end) == (date(2025, 10, 22), date(2025, 10, 23))

    async def test_none_tournament_matches_tenant(self, db):
        await set_config(KEY_EVENT_START_DATE, '2025-10-20')
        await set_config(KEY_EVENT_END_DATE, '2025-10-23')
        start, end = await SystemConfigService.get_event_window(None)
        assert (start, end) == (date(2025, 10, 20), date(2025, 10, 23))


class TestSetTournamentHours:
    async def test_persists_and_trims_values(self, db):
        staff = await make_user(1, staff=True)
        await SystemConfigService.set_tournament_hours(
            {
                date(2025, 10, 20): ('09:00', '17:00'),
                date(2025, 10, 21): ('  10:00  ', '  18:00  '),
            },
            staff,
        )
        hours = await SystemConfigService.get_tournament_hours()
        assert hours == {
            date(2025, 10, 20): (time(9, 0), time(17, 0)),
            date(2025, 10, 21): (time(10, 0), time(18, 0)),
        }

    async def test_skips_days_with_blank_times(self, db):
        staff = await make_user(1, staff=True)
        await SystemConfigService.set_tournament_hours(
            {
                date(2025, 10, 20): ('', '17:00'),
                date(2025, 10, 21): ('09:00', '   '),
            },
            staff,
        )
        assert await SystemConfigService.get_tournament_hours() == {}
        assert await SystemConfigService.get_raw(KEY_TOURNAMENT_HOURS) == '{}'

    async def test_raises_on_bad_time_format(self, db):
        staff = await make_user(1, staff=True)
        with pytest.raises(ValueError, match='HH:MM format'):
            await SystemConfigService.set_tournament_hours(
                {date(2025, 10, 20): ('9am', '5pm')}, staff,
            )

    async def test_raises_when_close_before_open(self, db):
        staff = await make_user(1, staff=True)
        with pytest.raises(ValueError, match='after open time'):
            await SystemConfigService.set_tournament_hours(
                {date(2025, 10, 20): ('17:00', '09:00')}, staff,
            )

    async def test_raises_when_close_equals_open(self, db):
        staff = await make_user(1, staff=True)
        with pytest.raises(ValueError, match='after open time'):
            await SystemConfigService.set_tournament_hours(
                {date(2025, 10, 20): ('12:00', '12:00')}, staff,
            )

    async def test_valid_mapping_still_requires_staff(self, db):
        plain = await make_user(2, staff=False)
        with pytest.raises(PermissionError):
            await SystemConfigService.set_tournament_hours(
                {date(2025, 10, 20): ('09:00', '17:00')}, plain,
            )
        assert await SystemConfigService.get_raw(KEY_TOURNAMENT_HOURS) is None


class TestMatchSchedulingHonorsTournamentHours:
    async def test_tournament_override_bounds_scheduling(self, db):
        from application.services.match.match_service import MatchService
        from application.utils.timezone import parse_local_datetime

        # Tenant has no hours; the tournament restricts 12:00–20:00 on this date.
        tournament = await Tournament.create(
            name='Cup',
            tournament_hours={'2025-10-20': {'open': '12:00', 'close': '20:00'}},
        )
        svc = object.__new__(MatchService)

        outside = parse_local_datetime('2025-10-20', '10:00')
        with pytest.raises(ValueError, match='can only start between'):
            await svc.assert_within_tournament_hours(outside, tournament.id)

        inside = parse_local_datetime('2025-10-20', '13:00')
        await svc.assert_within_tournament_hours(inside, tournament.id)  # no raise

    async def test_falls_back_to_tenant_hours_when_tournament_unset(self, db):
        from application.services.match.match_service import MatchService
        from application.utils.timezone import parse_local_datetime

        await set_config(KEY_TOURNAMENT_HOURS, json.dumps({
            '2025-10-20': {'open': '12:00', 'close': '20:00'},
        }))
        tournament = await Tournament.create(name='NoOverride')  # tournament_hours is None
        svc = object.__new__(MatchService)

        outside = parse_local_datetime('2025-10-20', '10:00')
        with pytest.raises(ValueError, match='can only start between'):
            await svc.assert_within_tournament_hours(outside, tournament.id)


class TestListRecent:
    async def test_returns_rows_with_user_prefetched(self, db):
        user = await make_user(10, username='attendee')
        for i in range(3):
            await Feedback.create(
                user=user, category=FeedbackCategory.BUG, message=f'm{i}', page_url='/',
            )
        staff = await make_user(11, username='staffer', staff=True)
        recent = await FeedbackService().list_recent(staff)
        assert len(recent) == 3
        # user is prefetched, so the relation is accessible without an await.
        assert recent[0].user.username == 'attendee'

    async def test_respects_limit(self, db):
        user = await make_user(10, username='attendee')
        for i in range(5):
            await Feedback.create(
                user=user, category=FeedbackCategory.OTHER, message=f'm{i}', page_url='/',
            )
        staff = await make_user(11, username='staffer', staff=True)
        limited = await FeedbackService().list_recent(staff, limit=2)
        assert len(limited) == 2

    async def test_empty_when_no_feedback(self, db):
        staff = await make_user(11, username='staffer', staff=True)
        assert await FeedbackService().list_recent(staff) == []

    async def test_refused_for_anyone_but_staff(self, db):
        """The queue carries every submitter's name and message, so the service
        gates it the way it gates ``set_reviewed``, not only the routes in front."""
        user = await make_user(10, username='attendee')
        await Feedback.create(user=user, category=FeedbackCategory.BUG, message='m', page_url='/')
        with pytest.raises(PermissionError):
            await FeedbackService().list_recent(user)
        with pytest.raises(PermissionError):
            await FeedbackService().list_recent(None)


class TestMarkReviewed:
    async def test_admin_marks_reviewed_and_audits(self, db):
        staff = await make_user(1, username='staff', staff=True)
        submitter = await make_user(2, username='attendee')
        fb = await Feedback.create(
            user=submitter, category=FeedbackCategory.SUGGESTION, message='hi', page_url='/',
        )
        result = await FeedbackService().mark_reviewed(staff, fb.id)
        assert result.status == FeedbackStatus.REVIEWED

        stored = await Feedback.get(id=fb.id)
        assert stored.status == FeedbackStatus.REVIEWED

        log = await AuditLog.filter(action=AuditActions.FEEDBACK_REVIEWED).first()
        assert log is not None
        assert json.loads(log.details)['feedback_id'] == fb.id

    async def test_non_admin_denied(self, db):
        plain = await make_user(3, username='plain')
        fb = await Feedback.create(
            user=plain, category=FeedbackCategory.BUG, message='x', page_url='/',
        )
        with pytest.raises(PermissionError):
            await FeedbackService().mark_reviewed(plain, fb.id)
        stored = await Feedback.get(id=fb.id)
        assert stored.status == FeedbackStatus.NEW

    async def test_missing_feedback_raises_value_error(self, db):
        staff = await make_user(1, username='staff', staff=True)
        with pytest.raises(ValueError, match='not found'):
            await FeedbackService().mark_reviewed(staff, 999999)


class TestSubmit:
    async def test_persists_and_coerces_unknown_category(self, db):
        actor = await make_user(4, username='attendee')
        await TenantMembership.create(user=actor, tenant_id=1)
        fb = await FeedbackService().submit(
            actor=actor, category='nonsense', message='  hello  ', page_url='/volunteer',
        )
        stored = await Feedback.get(id=fb.id)
        assert stored.category == FeedbackCategory.OTHER  # unknown -> OTHER
        assert stored.message == 'hello'                  # trimmed
        assert stored.status == FeedbackStatus.NEW

    async def test_empty_message_raises(self, db):
        actor = await make_user(4, username='attendee')
        with pytest.raises(ValueError, match='required'):
            await FeedbackService().submit(actor=actor, category='bug', message='   ', page_url='/')

    async def test_page_url_truncated(self, db):
        actor = await make_user(4, username='attendee')
        await TenantMembership.create(user=actor, tenant_id=1)
        fb = await FeedbackService().submit(
            actor=actor, category='praise', message='great', page_url='/x' * 600,
        )
        stored = await Feedback.get(id=fb.id)
        assert len(stored.page_url) == PAGE_URL_MAX_LENGTH
        assert stored.category == FeedbackCategory.PRAISE
