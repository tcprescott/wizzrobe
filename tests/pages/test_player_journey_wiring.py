"""Source-shape guards for player-journey fixes the SQLite suite cannot drive.

Each was a live bug found by driving the app (docs/reviews/player-journey-ux.md)
in code that only runs inside a NiceGUI client, so the assertion is on the shape
of the call rather than its behaviour.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


class TestTriforceButton:
    def test_the_card_hands_the_client_to_the_dialog(self):
        source = _read('pages/home_tabs/tournaments.py')
        assert 'open_triforce_dialog(tourney, user, context.client)' in source

    def test_the_dialog_reenters_the_client(self):
        source = _read('pages/home_tabs/triforce_texts.py')
        opener = source[source.index('async def open_triforce_dialog'):]
        opener = opener[:opener.index('\nasync def ', 1)]
        assert 'with client:' in opener


class TestDeepLinkIds:
    """A mangled DM link renders the page and says so, rather than a raw 422."""

    def test_ids_parse_and_blanks_are_absent(self):
        from pages.home import deep_link_ids

        ids, malformed = deep_link_ids(schedule='30', reschedule=None, match='', hard='7')
        assert ids == {'schedule': 30, 'reschedule': None, 'match': None, 'hard': 7}
        assert malformed is False

    def test_a_malformed_id_is_dropped_and_reported(self):
        from pages.home import deep_link_ids

        ids, malformed = deep_link_ids(schedule='abc', hard='-1', match='12')
        assert ids == {'schedule': None, 'hard': None, 'match': 12}
        assert malformed is True

    def test_the_route_no_longer_types_them_as_int(self):
        source = _read('pages/home.py')
        for name in ('schedule', 'reschedule', 'match', 'hard'):
            assert f'{name}: int | None' not in source, name


class TestBookingCopy:
    """A player's booking is final; nothing asks the opponent to confirm it."""

    def test_no_surface_promises_a_confirm_step(self):
        for rel in ('pages/home_tabs/player.py',
                    'theme/dialog/bracket_schedule_dialog.py',
                    'theme/dialog/challonge_schedule_dialog.py'):
            source = _read(rel)
            assert 'your opponent confirms' not in source, rel
            assert 'asked to confirm' not in source, rel
        for rel in ('theme/dialog/bracket_schedule_dialog.py',
                    'theme/dialog/challonge_schedule_dialog.py'):
            assert 'booked_notice(' in _read(rel), rel

    def test_the_board_refreshes_before_the_section_holding_the_dialog(self):
        source = _read('pages/home_tabs/player.py')
        after = source[source.index('waiting_section.refresh()') - 120:source.index('waiting_section.refresh()')]
        assert 'await table_view.refresh()' in after


class TestBookingDialog:
    def test_on_submit_runs_in_the_opening_client_and_tenant(self):
        source = _read('theme/dialog/bracket_schedule_dialog.py')
        assert 'self._client = context.client' in source
        assert 'self.tenant_id = get_current_tenant_id()' in source
        assert 'with self._client, self._scope():' in source

    def test_a_lost_race_closes_and_refreshes(self):
        source = _read('theme/dialog/bracket_schedule_dialog.py')
        branch = source[source.index('except AlreadyBookedError'):]
        branch = branch[:branch.index('except (PermissionError')]
        assert 'dialog.close()' in branch
        assert 'return' not in branch

    def test_no_dialog_defaults_to_now(self):
        for rel in ('theme/dialog/bracket_schedule_dialog.py',
                    'theme/dialog/challonge_schedule_dialog.py'):
            source = _read(rel)
            assert 'next_whole_hour_local()' in source, rel
            assert 'now_local()' not in source, rel


class TestRequestMatchEmptyState:
    def test_each_case_says_what_is_true(self):
        from theme.dialog.match_dialog import _no_requestable_message

        assert 'bracket' in _no_requestable_message(enrolled=True, enrolled_running=True)
        finished = _no_requestable_message(enrolled=True, enrolled_running=False)
        assert 'finished' in finished and 'bracket' not in finished
        assert 'opted into' in _no_requestable_message(enrolled=False, enrolled_running=False)


class TestBookingCaption:
    def test_the_ask_staff_clause_appears_only_when_every_tournament_allows_it(self):
        from types import SimpleNamespace

        from pages.home_tabs.player import _booking_caption

        yes, no = (SimpleNamespace(allow_reschedule_requests=v) for v in (True, False))
        assert _booking_caption([yes]).endswith('can ask staff to move it.')
        assert 'ask staff' not in _booking_caption([yes, no])

    def test_the_toast_follows_the_same_rule(self):
        from theme.dialog._helpers import booked_notice

        assert 'ask staff' in booked_notice('Bob', True)
        assert 'ask staff' not in booked_notice('Bob', False)
