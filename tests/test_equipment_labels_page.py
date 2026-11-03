"""Unit tests for the bulk QR-label sheet page + dialog helpers."""

from pages.equipment_labels import _chunk, _parse_ids, _parse_show, resolve_template
from theme.dialog.qr_label_dialog import filtered_ids


class TestParseIds:
    def test_parses_and_orders_input(self):
        assert _parse_ids('3,1,2') == [3, 1, 2]

    def test_drops_blanks_and_non_numerics(self):
        assert _parse_ids('3, ,x,1,,2') == [3, 1, 2]

    def test_empty_inputs(self):
        assert _parse_ids('') == []
        assert _parse_ids('   ') == []
        assert _parse_ids(',,') == []


class TestParseShow:
    def test_recognized_fields_only(self):
        assert _parse_show('owner,desc') == {'owner', 'desc'}
        assert _parse_show('owner,bogus,community') == {'owner', 'community'}

    def test_empty(self):
        assert _parse_show('') == set()


class TestResolveTemplate:
    def test_known_avery_kept(self):
        assert resolve_template('avery5160') == 'avery5160'
        assert resolve_template('avery5163') == 'avery5163'

    def test_unknown_falls_back_to_plain(self):
        assert resolve_template('plain') == 'plain'
        assert resolve_template('') == 'plain'
        assert resolve_template('nope') == 'plain'


class TestChunk:
    def test_splits_into_pages(self):
        assert _chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]

    def test_exact_multiple_and_empty(self):
        assert _chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
        assert _chunk([], 3) == []

    def test_size_floor_of_one(self):
        assert _chunk([1, 2], 0) == [[1], [2]]


class TestFilteredIds:
    def _rows(self):
        return [
            {'id': 1, 'num': 1, 'available': True, 'owner_key': ''},
            {'id': 2, 'num': 2, 'available': False, 'owner_key': '7'},
            {'id': 3, 'num': 3, 'available': True, 'owner_key': '7'},
            {'id': 4, 'num': 4, 'available': True, 'owner_key': ''},
        ]

    def test_no_filters_returns_all(self):
        assert filtered_ids(self._rows()) == [1, 2, 3, 4]

    def test_available_only(self):
        assert filtered_ids(self._rows(), available_only=True) == [1, 3, 4]

    def test_owner_key_community_vs_user(self):
        assert filtered_ids(self._rows(), owner_key='') == [1, 4]
        assert filtered_ids(self._rows(), owner_key='7') == [2, 3]

    def test_number_range_inclusive(self):
        assert filtered_ids(self._rows(), num_from=2, num_to=3) == [2, 3]
        assert filtered_ids(self._rows(), num_from=3) == [3, 4]
        assert filtered_ids(self._rows(), num_to=2) == [1, 2]

    def test_combined_filters(self):
        assert filtered_ids(
            self._rows(), available_only=True, owner_key='7', num_from=1, num_to=10,
        ) == [3]
