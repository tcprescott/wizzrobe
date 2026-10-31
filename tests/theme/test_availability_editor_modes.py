"""The availability editor's two readings, and the venue-hours shading.

The heatmap is where a player finds out what the scheduler thinks they said, so
the mode a tab passes has to match the service backing it: block mode paints an
untouched stretch as available, declare mode paints it as nothing at all.
"""

import itertools
from datetime import datetime, timezone
from pathlib import Path

from application.services.player_availability_service import PlayerAvailabilityService
from application.services.volunteer.volunteer_availability_service import (
    VolunteerAvailabilityService,
)
from models import VolunteerAvailabilityStatus
from theme.availability_editor import (
    BLOCK_MODE,
    DECLARE_MODE,
    _split_by_open,
)

UTC = timezone.utc


def _dt(hour, minute=0):
    return datetime(2026, 10, 4, hour, minute, tzinfo=UTC)


class TestModes:
    def test_block_mode_offers_only_block_and_prefer(self):
        assert set(BLOCK_MODE.status_options) == {'unavailable', 'preferred'}
        assert BLOCK_MODE.new_row_status == VolunteerAvailabilityStatus.UNAVAILABLE

    def test_declare_mode_still_offers_all_three(self):
        assert set(DECLARE_MODE.status_options) == {
            'available', 'preferred', 'unavailable',
        }
        assert DECLARE_MODE.new_row_status == VolunteerAvailabilityStatus.AVAILABLE

    def test_block_mode_labels_unavailable_as_cannot_play(self):
        assert BLOCK_MODE.label(VolunteerAvailabilityStatus.UNAVAILABLE) == "Can't play"

    def test_block_mode_still_labels_available_it_never_offers(self):
        assert BLOCK_MODE.label(VolunteerAvailabilityStatus.AVAILABLE) == 'Available'

    def test_only_block_mode_shades_closed_hours(self):
        assert BLOCK_MODE.shade_closed_hours
        assert not DECLARE_MODE.shade_closed_hours


class TestTabWiring:
    """The mode a tab passes has to match the service it injects.

    Mismatch them and the heatmap tells a player the opposite of what the
    scheduler reads — green where they are blocked, or blank where they are
    free — which is exactly the kind of drift a two-line clone introduces.
    """

    def _source(self, path):
        return (Path(__file__).parents[2] / path).read_text()

    def test_home_tab_pairs_block_mode_with_the_player_service(self):
        src = self._source('pages/home_tabs/availability.py')
        assert 'PlayerAvailabilityService()' in src
        assert 'mode=BLOCK_MODE' in src

    def test_volunteer_tab_takes_the_declare_default_with_its_own_service(self):
        src = self._source('pages/volunteer_tabs/availability.py')
        assert 'VolunteerAvailabilityService()' in src
        assert 'BLOCK_MODE' not in src

    def test_block_mode_default_matches_the_service_it_is_paired_with(self):
        assert PlayerAvailabilityService.covers([], _dt(8), _dt(12)) == \
            VolunteerAvailabilityStatus.AVAILABLE
        assert VolunteerAvailabilityService.covers([], _dt(8), _dt(12)) is None


class TestSplitByOpen:
    def test_no_configured_hours_means_everything_open(self):
        assert list(_split_by_open(_dt(8), _dt(12), None)) == [(_dt(8), _dt(12), True)]

    def test_segment_inside_open_hours_is_untouched(self):
        parts = list(_split_by_open(_dt(10), _dt(12), [(_dt(9), _dt(17))]))
        assert parts == [(_dt(10), _dt(12), True)]

    def test_segment_before_opening_is_closed(self):
        parts = list(_split_by_open(_dt(2), _dt(6), [(_dt(9), _dt(17))]))
        assert parts == [(_dt(2), _dt(6), False)]

    def test_segment_straddling_opening_splits(self):
        parts = list(_split_by_open(_dt(7), _dt(11), [(_dt(9), _dt(17))]))
        assert parts == [(_dt(7), _dt(9), False), (_dt(9), _dt(11), True)]

    def test_segment_straddling_close_splits(self):
        parts = list(_split_by_open(_dt(16), _dt(20), [(_dt(9), _dt(17))]))
        assert parts == [(_dt(16), _dt(17), True), (_dt(17), _dt(20), False)]

    def test_whole_day_splits_around_the_open_window(self):
        parts = list(_split_by_open(_dt(0), _dt(23, 59), [(_dt(9), _dt(17))]))
        assert parts == [
            (_dt(0), _dt(9), False),
            (_dt(9), _dt(17), True),
            (_dt(17), _dt(23, 59), False),
        ]

    def test_parts_tile_the_segment_without_gaps(self):
        ranges = [(_dt(4), _dt(6)), (_dt(9), _dt(17))]
        parts = list(_split_by_open(_dt(0), _dt(20), ranges))
        assert parts[0][0] == _dt(0)
        assert parts[-1][1] == _dt(20)
        for (_, prev_end, _flag), (next_start, _, _f) in itertools.pairwise(parts):
            assert prev_end == next_start
