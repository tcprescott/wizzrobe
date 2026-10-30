import pytest

from application.utils.duration import format_hms, parse_hms


class TestFormatHms:
    @pytest.mark.parametrize('seconds,expected', [
        (0, '0:00:00'),
        (5, '0:00:05'),
        (450, '0:07:30'),
        (4325, '1:12:05'),
        (5025, '1:23:45'),
        (6000, '1:40:00'),
        (362439, '100:40:39'),
        (604800, '168:00:00'),
    ])
    def test_formats_whole_seconds(self, seconds, expected):
        assert format_hms(seconds) == expected

    def test_none_is_the_dash(self):
        assert format_hms(None) == '—'

    def test_dash_is_overridable(self):
        assert format_hms(None, dash='') == ''

    def test_negative_clamps_to_zero(self):
        assert format_hms(-5) == '0:00:00'


class TestParseHms:
    @pytest.mark.parametrize('text,seconds', [
        ('1:23:45', 5025),
        ('0:07:30', 450),
        ('0:00:01', 1),
        ('168:00:00', 604800),
        ('  1:23:45  ', 5025),
    ])
    def test_accepts_three_segments(self, text, seconds):
        assert parse_hms(text) == seconds

    @pytest.mark.parametrize('text', ['', '   ', None])
    def test_empty_names_the_shape(self, text):
        with pytest.raises(ValueError, match='Enter a finish time as H:MM:SS'):
            parse_hms(text)

    @pytest.mark.parametrize('text', ['abc', '1:ab:00'])
    def test_junk_keeps_todays_wording(self, text):
        with pytest.raises(ValueError, match="must be numbers separated by ':'"):
            parse_hms(text)

    @pytest.mark.parametrize('text', ['83', '1:23', '1:2:3:4'])
    def test_wrong_segment_count_names_all_three_parts(self, text):
        with pytest.raises(ValueError, match='Enter all three parts'):
            parse_hms(text)

    @pytest.mark.parametrize('text', ['-1:00:00', '1:-2:00'])
    def test_negative_segment_names_the_shape(self, text):
        with pytest.raises(ValueError, match='Enter a finish time as H:MM:SS'):
            parse_hms(text)

    @pytest.mark.parametrize('text', ['1:99:00', '1:00:99', '0:60:00'])
    def test_out_of_range_segment_is_named(self, text):
        with pytest.raises(ValueError, match='between 0 and 59'):
            parse_hms(text)

    def test_zero_keeps_todays_wording(self):
        with pytest.raises(ValueError, match='greater than zero'):
            parse_hms('0:00:00')

    def test_a_dropped_segment_is_no_longer_sixty_times_too_fast(self):
        with pytest.raises(ValueError):
            parse_hms('1:23')
