from datetime import date, datetime, time, timezone

import pytest

from application.timezone_context import (
    FALLBACK_TIMEZONE,
    current_timezone_name,
    is_valid_timezone,
    read_timezone_cookie,
    tz_scope,
)
from application.utils.timezone import (
    EASTERN_TZ,
    combine_local,
    format_local_date,
    format_local_datetime,
    format_local_display,
    format_local_short,
    format_local_time,
    format_local_time_range,
    local_day_bounds,
    now_local,
    parse_local_datetime,
    timezone_label,
    to_local,
    to_utc_aware,
    today_local,
)

# A zone deliberately unlike Eastern: +05:30, no DST, half-hour offset. Catches
# both "still hardcoded to Eastern" and "assumes whole-hour offsets".
KOLKATA = 'Asia/Kolkata'
# Southern-hemisphere DST, so its summer/winter run opposite Eastern's.
SYDNEY = 'Australia/Sydney'


class TestTimezoneContext:
    def test_unset_context_falls_back(self):
        assert current_timezone_name() == FALLBACK_TIMEZONE

    def test_scope_binds_zone(self):
        with tz_scope(KOLKATA):
            assert current_timezone_name() == KOLKATA

    def test_scope_restores_on_exit(self):
        with tz_scope(KOLKATA):
            pass
        assert current_timezone_name() == FALLBACK_TIMEZONE

    def test_scope_nests(self):
        with tz_scope(KOLKATA):
            with tz_scope(SYDNEY):
                assert current_timezone_name() == SYDNEY
            assert current_timezone_name() == KOLKATA

    def test_unknown_zone_falls_back_rather_than_raising(self):
        # A bad name can reach here from stored config or a forged cookie;
        # rendering must not break because of it.
        with tz_scope('Mars/Olympus_Mons'):
            assert current_timezone_name() == FALLBACK_TIMEZONE

    def test_is_valid_timezone(self):
        assert is_valid_timezone('Europe/London')
        assert not is_valid_timezone('Mars/Olympus_Mons')
        assert not is_valid_timezone('')
        assert not is_valid_timezone(None)


class TestReadTimezoneCookie:
    """The browser writes the cookie with ``encodeURIComponent``.

    Starlette does not percent-decode cookie values, so an undecoded name loads
    in no tzdata — it failed validation silently and every viewer fell through to
    the community default, which is indistinguishable from the browser tier not
    existing at all.
    """

    def test_percent_encoded_value_is_decoded(self):
        assert read_timezone_cookie('Asia%2FTokyo') == 'Asia/Tokyo'

    def test_decoded_value_is_a_loadable_zone(self):
        assert is_valid_timezone(read_timezone_cookie('America%2FNew_York'))

    def test_plain_value_passes_through(self):
        assert read_timezone_cookie('America/New_York') == 'America/New_York'

    def test_absent_cookie_is_none(self):
        assert read_timezone_cookie(None) is None
        assert read_timezone_cookie('') is None

    def test_garbage_decodes_but_does_not_validate(self):
        # Decoding is not validation — a forged cookie still has to load.
        assert not is_valid_timezone(read_timezone_cookie('Mars%2FOlympus_Mons'))


class TestParseLocalDatetime:
    def test_winter_est_converts_to_utc(self):
        # EST is UTC-5, so 14:30 Eastern → 19:30 UTC
        dt = parse_local_datetime("2025-01-15", "14:30")
        assert dt.hour == 19
        assert dt.minute == 30

    def test_result_is_utc_aware(self):
        dt = parse_local_datetime("2025-01-15", "14:30")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_summer_edt_converts_to_utc(self):
        # EDT is UTC-4, so 14:30 Eastern → 18:30 UTC
        dt = parse_local_datetime("2025-07-15", "14:30")
        assert dt.hour == 18
        assert dt.minute == 30

    def test_midnight_converts_correctly(self):
        dt = parse_local_datetime("2025-01-15", "00:00")
        assert dt.hour == 5
        assert dt.minute == 0

    def test_explicit_tz_argument_wins(self):
        # 14:30 in Kolkata (+05:30) → 09:00 UTC
        dt = parse_local_datetime("2025-01-15", "14:30", KOLKATA)
        assert (dt.hour, dt.minute) == (9, 0)

    def test_ambient_zone_is_used_when_no_tz_passed(self):
        with tz_scope(KOLKATA):
            dt = parse_local_datetime("2025-01-15", "14:30")
        assert (dt.hour, dt.minute) == (9, 0)

    def test_southern_hemisphere_dst(self):
        # January is AEDT (+11) in Sydney: 14:30 → 03:30 UTC same day.
        dt = parse_local_datetime("2025-01-15", "14:30", SYDNEY)
        assert (dt.hour, dt.minute) == (3, 30)
        # July is AEST (+10): 14:30 → 04:30 UTC.
        dt = parse_local_datetime("2025-07-15", "14:30", SYDNEY)
        assert (dt.hour, dt.minute) == (4, 30)

    def test_invalid_date_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid date/time format"):
            parse_local_datetime("not-a-date", "14:30")

    def test_invalid_time_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid date/time format"):
            parse_local_datetime("2025-01-15", "99:99")

    def test_wrong_date_format_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid date/time format"):
            parse_local_datetime("01/15/2025", "2:30pm")

    def test_roundtrip_preserves_time(self):
        dt = parse_local_datetime("2025-06-15", "09:00")
        back = to_local(dt)
        assert back.hour == 9
        assert back.minute == 0

    def test_roundtrip_preserves_time_in_half_hour_zone(self):
        dt = parse_local_datetime("2025-06-15", "09:00", KOLKATA)
        back = to_local(dt, KOLKATA)
        assert (back.hour, back.minute) == (9, 0)

    def test_dst_before_spring_forward(self):
        # 01:59 on spring-forward day is unambiguously EST (UTC-5)
        dt = parse_local_datetime("2025-03-09", "01:59")
        assert dt.hour == 6
        assert dt.minute == 59

    def test_fall_back_midnight_is_edt(self):
        # 00:00 on fall-back day is unambiguously EDT (UTC-4)
        dt = parse_local_datetime("2025-11-02", "00:00")
        assert dt.hour == 4
        assert dt.minute == 0

    def test_nonexistent_spring_forward_time_is_rejected(self):
        # 02:30 does not exist on 2025-03-09 in New York — the clocks jump
        # 02:00 → 03:00. Accepting it silently stored 03:30.
        with pytest.raises(ValueError, match="does not exist"):
            parse_local_datetime("2025-03-09", "02:30")

    def test_nonexistent_time_names_the_zone(self):
        with pytest.raises(ValueError, match="America/New_York"):
            parse_local_datetime("2025-03-09", "02:30")

    def test_same_wall_clock_is_valid_in_a_zone_without_that_gap(self):
        # The gap is zone-specific: 02:30 that day is perfectly ordinary in
        # Kolkata, which has no DST at all.
        dt = parse_local_datetime("2025-03-09", "02:30", KOLKATA)
        assert (dt.hour, dt.minute) == (21, 0)  # previous day 21:00 UTC

    def test_ambiguous_fall_back_time_resolves_to_first_occurrence(self):
        # 01:30 happens twice on 2025-11-02; the earlier (EDT, UTC-4) is chosen.
        dt = parse_local_datetime("2025-11-02", "01:30")
        assert dt.hour == 5
        assert dt.minute == 30


class TestCombineLocal:
    def test_combines_on_ambient_zone(self):
        dt = combine_local(date(2025, 1, 15), time(0, 0))
        assert dt.hour == 5  # EST midnight → 05:00 UTC
        assert dt.tzinfo is not None

    def test_combines_on_explicit_zone(self):
        dt = combine_local(date(2025, 1, 15), time(0, 0), KOLKATA)
        # Kolkata midnight is 18:30 UTC the previous day.
        assert (dt.date(), dt.hour, dt.minute) == (date(2025, 1, 14), 18, 30)

    def test_result_is_utc(self):
        dt = combine_local(date(2025, 6, 1), time(12, 0))
        assert dt.utcoffset().total_seconds() == 0

    def test_does_not_reject_nonexistent_wall_clock(self):
        # Unlike parse_local_datetime: range bounds must always yield an instant.
        assert combine_local(date(2025, 3, 9), time(2, 30)) is not None


class TestLocalDayBounds:
    def test_single_day_is_24_hours(self):
        start, end = local_day_bounds(date(2025, 1, 15), date(2025, 1, 15))
        assert (end - start).total_seconds() == 24 * 3600

    def test_upper_bound_is_next_midnight_not_235959(self):
        _, end = local_day_bounds(date(2025, 1, 15), date(2025, 1, 15))
        assert to_local(end).date() == date(2025, 1, 16)
        assert to_local(end).hour == 0

    def test_bounds_follow_the_zone(self):
        eastern_start, _ = local_day_bounds(date(2025, 1, 15), date(2025, 1, 15))
        kolkata_start, _ = local_day_bounds(date(2025, 1, 15), date(2025, 1, 15), KOLKATA)
        assert eastern_start != kolkata_start

    def test_multi_day_range_spans_all_days(self):
        start, end = local_day_bounds(date(2025, 1, 15), date(2025, 1, 17))
        assert (end - start).total_seconds() == 3 * 24 * 3600

    def test_dst_day_is_23_hours(self):
        # Spring forward: 2025-03-09 in New York is a 23-hour day.
        start, end = local_day_bounds(date(2025, 3, 9), date(2025, 3, 9))
        assert (end - start).total_seconds() == 23 * 3600


class TestToLocal:
    def test_none_returns_none(self):
        assert to_local(None) is None

    def test_naive_datetime_treated_as_utc(self):
        naive_utc = datetime(2025, 1, 15, 19, 30)  # naive, interpreted as UTC
        result = to_local(naive_utc)
        assert result.hour == 14
        assert result.minute == 30

    def test_utc_aware_datetime_converts(self):
        utc_dt = datetime(2025, 7, 15, 18, 30, tzinfo=timezone.utc)
        result = to_local(utc_dt)
        assert result.hour == 14
        assert result.minute == 30

    def test_already_local_preserves_instant(self):
        eastern_dt = datetime(2025, 1, 15, 14, 30, tzinfo=EASTERN_TZ)
        result = to_local(eastern_dt)
        assert result.hour == 14
        assert result.minute == 30

    def test_explicit_tz_argument(self):
        utc_dt = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        result = to_local(utc_dt, KOLKATA)
        assert (result.hour, result.minute) == (17, 30)

    def test_follows_ambient_zone(self):
        utc_dt = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        with tz_scope(KOLKATA):
            result = to_local(utc_dt)
        assert (result.hour, result.minute) == (17, 30)


class TestToUtcAware:
    def test_naive_is_stamped_utc(self):
        result = to_utc_aware(datetime(2025, 1, 15, 12, 0))
        assert result.utcoffset().total_seconds() == 0
        assert result.hour == 12

    def test_aware_is_converted(self):
        result = to_utc_aware(datetime(2025, 1, 15, 12, 0, tzinfo=EASTERN_TZ))
        assert result.hour == 17

    def test_ignores_ambient_zone(self):
        # Normalization, not presentation: the display clock must not affect it.
        naive = datetime(2025, 1, 15, 12, 0)
        with tz_scope(KOLKATA):
            assert to_utc_aware(naive).hour == 12


class TestFormatLocalDate:
    def test_none_returns_empty_string(self):
        assert format_local_date(None) == ""

    def test_formats_utc_as_local_date(self):
        utc_dt = datetime(2025, 1, 15, 19, 30, tzinfo=timezone.utc)
        assert format_local_date(utc_dt) == "2025-01-15"

    def test_utc_midnight_may_show_previous_date(self):
        # 00:01 UTC Jan 16 → 19:01 EST Jan 15
        utc_dt = datetime(2025, 1, 16, 0, 1, tzinfo=timezone.utc)
        assert format_local_date(utc_dt) == "2025-01-15"

    def test_date_follows_the_zone(self):
        # The same instant is two different calendar days in two zones.
        utc_dt = datetime(2025, 1, 15, 21, 0, tzinfo=timezone.utc)
        assert format_local_date(utc_dt) == "2025-01-15"           # 16:00 EST
        assert format_local_date(utc_dt, KOLKATA) == "2025-01-16"  # 02:30 IST

    def test_accepts_plain_date(self):
        assert format_local_date(date(2025, 1, 15)) == "2025-01-15"

    def test_plain_date_is_not_shifted(self):
        # A plain date carries no instant, so no zone can move it.
        with tz_scope(KOLKATA):
            assert format_local_date(date(2025, 12, 31)) == "2025-12-31"


class TestFormatLocalTime:
    def test_none_returns_empty_string(self):
        assert format_local_time(None) == ""

    def test_utc_to_local_time_winter(self):
        utc_dt = datetime(2025, 1, 15, 19, 30, tzinfo=timezone.utc)
        assert format_local_time(utc_dt) == "14:30"

    def test_utc_to_local_time_summer(self):
        utc_dt = datetime(2025, 7, 15, 18, 30, tzinfo=timezone.utc)
        assert format_local_time(utc_dt) == "14:30"

    def test_half_hour_offset_zone(self):
        utc_dt = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert format_local_time(utc_dt, KOLKATA) == "17:30"

    def test_format_is_hh_mm_24_hour(self):
        utc_dt = datetime(2025, 1, 15, 0, 5, tzinfo=timezone.utc)
        parts = format_local_time(utc_dt).split(":")
        assert len(parts) == 2
        assert len(parts[0]) == 2
        assert len(parts[1]) == 2


class TestFormatLocalDisplay:
    def test_none_returns_empty_string(self):
        assert format_local_display(None) == ""

    def test_winter_shows_est_abbreviation(self):
        utc_dt = datetime(2025, 1, 15, 19, 30, tzinfo=timezone.utc)
        assert format_local_display(utc_dt) == "2025-01-15 14:30 EST"

    def test_summer_shows_edt_abbreviation(self):
        utc_dt = datetime(2025, 7, 15, 18, 30, tzinfo=timezone.utc)
        assert format_local_display(utc_dt) == "2025-07-15 14:30 EDT"

    def test_label_follows_the_zone(self):
        utc_dt = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert format_local_display(utc_dt, KOLKATA) == "2025-01-15 17:30 IST"

    def test_ambient_zone_is_used(self):
        utc_dt = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        with tz_scope(SYDNEY):
            assert format_local_display(utc_dt).endswith("AEDT")


class TestFormatLocalShort:
    def test_none_returns_empty_string(self):
        assert format_local_short(None) == ""

    def test_no_year_no_zone_no_leading_zero(self):
        utc_dt = datetime(2025, 7, 5, 3, 59, tzinfo=timezone.utc)
        assert format_local_short(utc_dt) == "Jul 4 23:59"

    def test_follows_the_zone(self):
        utc_dt = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert format_local_short(utc_dt, KOLKATA) == "Jan 15 17:30"


class TestFormatLocalDatetime:
    def test_none_returns_empty_string(self):
        assert format_local_datetime(None) == ""

    def test_default_format(self):
        utc_dt = datetime(2025, 1, 15, 19, 30, tzinfo=timezone.utc)
        assert format_local_datetime(utc_dt) == "2025-01-15 14:30"

    def test_custom_format_is_respected(self):
        utc_dt = datetime(2025, 1, 15, 19, 30, tzinfo=timezone.utc)
        assert format_local_datetime(utc_dt, fmt="%H:%M") == "14:30"

    def test_custom_format_with_explicit_zone(self):
        utc_dt = datetime(2025, 1, 15, 19, 30, tzinfo=timezone.utc)
        assert format_local_datetime(utc_dt, fmt="%H:%M", tz=KOLKATA) == "01:00"


class TestNowLocal:
    def test_returns_aware_datetime_in_ambient_zone(self):
        with tz_scope(KOLKATA):
            assert now_local().tzinfo is not None
            assert now_local().utcoffset().total_seconds() == 5.5 * 3600

    def test_is_recent(self):
        before = datetime.now(timezone.utc)
        now = now_local()
        after = datetime.now(timezone.utc)
        assert before <= now.astimezone(timezone.utc) <= after

    def test_explicit_zone(self):
        assert now_local(KOLKATA).utcoffset().total_seconds() == 5.5 * 3600


class TestTodayLocal:
    def test_matches_now_local_date(self):
        with tz_scope(KOLKATA):
            assert today_local() == now_local().date()

    def test_never_more_than_a_day_apart_between_zones(self):
        delta = abs((today_local(KOLKATA) - today_local('Pacific/Honolulu')).days)
        assert delta <= 1


class TestTimezoneLabel:
    def test_eastern_winter(self):
        at = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert timezone_label('America/New_York', at) == 'EST'

    def test_eastern_summer(self):
        at = datetime(2025, 7, 15, 12, 0, tzinfo=timezone.utc)
        assert timezone_label('America/New_York', at) == 'EDT'

    def test_zone_without_alphabetic_abbreviation_still_labels(self):
        at = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        # tzdata may have no alphabetic abbreviation for this zone; a numeric
        # form is still unambiguous, which is all the label promises.
        assert timezone_label('Asia/Kathmandu', at)

    def test_follows_ambient_zone(self):
        at = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
        with tz_scope(KOLKATA):
            assert timezone_label(at=at) == 'IST'


class TestFormatLocalTimeRange:
    def test_labels_the_zone_it_rendered_in(self):
        start = datetime(2025, 1, 15, 14, 0, tzinfo=timezone.utc)
        end = datetime(2025, 1, 15, 18, 0, tzinfo=timezone.utc)
        assert format_local_time_range(start, end, 'America/New_York') == '09:00–13:00 EST'
        assert format_local_time_range(start, end, KOLKATA) == '19:30–23:30 IST'

    def test_follows_ambient_zone(self):
        start = datetime(2025, 7, 15, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 7, 15, 4, 0, tzinfo=timezone.utc)
        with tz_scope(SYDNEY):
            assert format_local_time_range(start, end) == '10:00–14:00 AEST'

    def test_missing_end_is_blank(self):
        start = datetime(2025, 1, 15, 14, 0, tzinfo=timezone.utc)
        assert format_local_time_range(start, None) == ''


class TestNextWholeHourLocal:
    """The booking dialogs' default: never a time that has already passed."""

    def _at(self, monkeypatch, utc_instant):
        import application.utils.timezone as tzmod

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return utc_instant.astimezone(tz) if tz else utc_instant

        monkeypatch.setattr(tzmod, 'datetime', _Frozen)
        return tzmod.next_whole_hour_local

    def test_rounds_up_past_the_lead(self, monkeypatch):
        # 03:18:40 UTC is 08:48:40 in Kolkata.
        fn = self._at(monkeypatch, datetime(2025, 1, 15, 3, 18, 40, tzinfo=timezone.utc))
        got = fn(tz=KOLKATA)
        assert (got.hour, got.minute, got.second) == (10, 0, 0)

    def test_an_early_minute_takes_the_next_hour(self, monkeypatch):
        # 02:50:40 UTC is 08:20:40 in Kolkata.
        fn = self._at(monkeypatch, datetime(2025, 1, 15, 2, 50, 40, tzinfo=timezone.utc))
        assert fn(tz=KOLKATA).hour == 9

    def test_spring_forward_skips_the_missing_hour(self, monkeypatch):
        # 01:20 EST on 2026-03-08; 02:00-02:59 does not exist in New York.
        fn = self._at(monkeypatch, datetime(2026, 3, 8, 6, 20, tzinfo=timezone.utc))
        got = fn(tz='America/New_York')
        assert (got.hour, got.minute) == (3, 0)
        # The dialog splits it into wall-clock strings; they must parse back.
        back = parse_local_datetime(got.strftime('%Y-%m-%d'), got.strftime('%H:%M'),
                                    tz='America/New_York')
        assert back == datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)

    def test_is_always_in_the_future(self):
        from application.utils.timezone import next_whole_hour_local

        assert next_whole_hour_local() > now_local()
        assert next_whole_hour_local().minute == 0
