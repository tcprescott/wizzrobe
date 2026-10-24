"""The viewer's 12/24-hour clock: resolution and every formatter that follows it."""

from datetime import datetime, timezone

from application.timezone_context import (
    hour_cycle_scope,
    pick_hour12,
    read_hour_cycle_cookie,
    tz_scope,
    uses_12_hour,
)
from application.utils.timezone import (
    format_local_datetime,
    format_local_display,
    format_local_hour,
    format_local_short,
    format_local_time,
    format_local_time_range,
    readable_discord_times,
    strftime_local,
)

EVENING = datetime(2026, 10, 24, 21, 5, 9, tzinfo=timezone.utc)
MIDNIGHT = datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)
NOON = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def twelve():
    return hour_cycle_scope(True)


class TestResolution:
    def test_unbound_is_24_hour(self):
        assert uses_12_hour() is False

    def test_saved_choice_beats_the_browser(self):
        assert pick_hour12('24', '12') is False
        assert pick_hour12('12', '24') is True

    def test_browser_is_used_when_nothing_is_saved(self):
        assert pick_hour12(None, '12') is True
        assert pick_hour12(None, '24') is False

    def test_nothing_known_is_24_hour(self):
        assert pick_hour12(None, None) is False
        assert pick_hour12('bogus', 'h12') is False

    def test_cookie_accepts_only_12_or_24(self):
        assert read_hour_cycle_cookie('12') == '12'
        assert read_hour_cycle_cookie('24') == '24'
        assert read_hour_cycle_cookie('h12') is None
        assert read_hour_cycle_cookie(None) is None


class TestFormatting:
    def test_24_hour_is_unchanged(self):
        with tz_scope('UTC'):
            assert format_local_time(EVENING) == '21:05'
            assert format_local_display(EVENING) == '2026-10-24 21:05 UTC'

    def test_12_hour_time(self):
        with tz_scope('UTC'), twelve():
            assert format_local_time(EVENING) == '9:05\u00a0PM'
            assert format_local_time(MIDNIGHT) == '12:30\u00a0AM'
            assert format_local_time(NOON) == '12:00\u00a0PM'

    def test_12_hour_in_longer_formats(self):
        with tz_scope('UTC'), twelve():
            assert format_local_display(EVENING) == '2026-10-24 9:05\u00a0PM UTC'
            assert format_local_datetime(EVENING) == '2026-10-24 9:05\u00a0PM'
            assert format_local_datetime(EVENING, '%a %d %b %Y, %H:%M:%S %Z') == 'Sat 24 Oct 2026, 9:05:09\u00a0PM UTC'
            assert format_local_short(EVENING) == 'Oct 24 9:05\u00a0PM'
            assert format_local_time_range(NOON, EVENING) == '12:00\u00a0PM–9:05\u00a0PM UTC'

    def test_follows_the_zone(self):
        with tz_scope('America/New_York'), twelve():
            assert format_local_time(EVENING) == '5:05\u00a0PM'

    def test_input_values_stay_24_hour(self):
        local = EVENING.replace(tzinfo=None)
        with twelve():
            assert strftime_local(local, '%Y-%m-%dT%H:%M') == '2026-10-24T21:05'

    def test_hour_axis_labels(self):
        nine_pm = EVENING.replace(minute=0, second=0)
        with tz_scope('UTC'):
            assert format_local_hour(nine_pm) == '21:00'
            with twelve():
                assert format_local_hour(nine_pm) == '9\u00a0PM'
                assert format_local_hour(MIDNIGHT) == '12\u00a0AM'

    def test_notification_copies_stay_24_hour(self):
        text = f'Starts <t:{int(EVENING.timestamp())}:F>'
        with tz_scope('UTC'), twelve():
            assert readable_discord_times(text, 'UTC') == 'Starts 2026-10-24 21:05 UTC'
            assert uses_12_hour() is True
