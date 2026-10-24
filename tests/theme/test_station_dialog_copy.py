"""``StationAssignmentDialog``'s two copy modes.

The same dialog does two jobs: the proctor's check-in (seat the players, then
the caller checks the match in) and correcting the stations of a match that is
already checked in. The check-in mode has to say "check in" — the audit found a
dialog that never mentioned the thing it was doing — and the stations mode must
not claim to check anything in, because in that mode nothing is checked in.

Rendering the dialog needs a live NiceGUI client, so this locks the copy table
and the constructor state ``open()`` reads from it.
"""

from theme.dialog.station_assignment_dialog import _PURPOSE_COPY, StationAssignmentDialog


class TestPurposeCopy:
    def test_checkin_copy_mentions_checking_in(self):
        assert 'check' in _PURPOSE_COPY['checkin']['title'].lower()
        assert 'check' in _PURPOSE_COPY['checkin']['submit'].lower()

    def test_station_copy_does_not_claim_to_check_in(self):
        assert 'check in' not in _PURPOSE_COPY['stations']['submit'].lower()
        assert 'check in' not in _PURPOSE_COPY['stations']['title'].lower()

    def test_every_mode_supplies_the_three_strings(self):
        for purpose, copy in _PURPOSE_COPY.items():
            assert set(copy) == {'title', 'lead', 'submit'}
            # One player's field is the whole dialog, with nothing to lead into.
            assert all(v for k, v in copy.items() if not (purpose == 'player' and k == 'lead'))

    def test_titles_name_the_match_rather_than_its_id(self):
        """The rule ``application.utils.match_labels`` was written down for.

        These titles used to interpolate the primary key ("Check in — Match
        #11"), which is the one thing a proctor standing at a station cannot
        use. They take a rendered label now, so the placeholder is ``{match}``
        and no ``#`` survives the format.
        """
        for purpose, copy in _PURPOSE_COPY.items():
            rendered = copy['title'].format(match='Alice vs Bob', player='Alice')
            assert rendered.endswith('Alice' if purpose == 'player' else 'Alice vs Bob')
            assert '#' not in rendered


class TestPurposeSelection:
    def test_defaults_to_stations(self):
        assert StationAssignmentDialog(match=None).purpose == 'stations'

    def test_honours_a_known_purpose(self):
        assert StationAssignmentDialog(match=None, purpose='checkin').purpose == 'checkin'

    def test_one_player_is_its_own_mode(self):
        dialog = StationAssignmentDialog(match=None, player_id=7)
        assert (dialog.purpose, dialog.player_id) == ('player', 7)

    def test_falls_back_for_an_unknown_purpose(self):
        assert StationAssignmentDialog(match=None, purpose='nonsense').purpose == 'stations'
