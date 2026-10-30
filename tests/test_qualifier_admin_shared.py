"""The Async Qualifiers admin's pure helpers, and the paging bound P1 exists to keep.

``shared`` holds no I/O, so it is testable without a page or a database. The paging
assertions are the point of the file: an unpaginated ``ui.table`` resolves
``page_size`` to 0 — Quasar's "every row" — which is how the Runs tab came to render
3,129 rows into a 151,000-pixel page. A constant is easy to delete by accident, so
it is pinned here rather than trusted.
"""

from types import SimpleNamespace

from pages.admin_tabs.admin_qualifiers.shared import (
    BOARD_COLUMNS,
    BOARD_PAGE,
    POOL_PERMALINK_PREVIEW,
    QUEUE_PAGE_SIZE,
    RUNS_COLUMNS,
    RUNS_PAGE,
    board_rows,
    enum_value,
    live_race_color,
    live_race_label,
    other_runs_summary,
    run_rows,
    short_url,
)


def _run(**kw):
    defaults = dict(
        id=1, user_id=7, status='finished', review_status='approved', reattempted=False,
        elapsed_seconds=3600, measured_seconds=3700, score=99.44,
        user=SimpleNamespace(display_name='Runner One', username='runner_one'),
        permalink=SimpleNamespace(pool=SimpleNamespace(name='Pool A')),
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestPagingBounds:
    def test_every_qualifier_table_ships_a_page_size(self):
        for page in (RUNS_PAGE, BOARD_PAGE):
            assert page.get('rowsPerPage'), (
                'a table built without rowsPerPage renders every row it is given'
            )
            assert page['rowsPerPage'] > 0
            assert page.get('page') == 1

    def test_the_board_pages_larger_than_the_runs_table(self):
        assert BOARD_PAGE['rowsPerPage'] >= RUNS_PAGE['rowsPerPage']

    def test_the_card_surfaces_are_bounded_too(self):
        assert 0 < QUEUE_PAGE_SIZE <= 50
        assert 0 < POOL_PERMALINK_PREVIEW <= 25


class TestColumns:
    def test_the_runs_table_keeps_its_action_column_last(self):
        assert RUNS_COLUMNS[-1]['name'] == 'actions'

    def test_the_slots_column_is_not_sortable(self):
        slots = next(c for c in BOARD_COLUMNS if c['name'] == 'slots')
        assert not slots.get('sortable')

    def test_every_column_field_is_produced_by_its_row_builder(self):
        run_row = run_rows([_run()])[0]
        for column in RUNS_COLUMNS:
            if column['name'] == 'actions':
                continue
            assert column['field'] in run_row, f"{column['field']} is never populated"
        board_row = board_rows([SimpleNamespace(
            rank=1, username='A', actual=1.0, estimate=2.0,
            slots_filled=1, slots_total=2)])[0]
        for column in BOARD_COLUMNS:
            assert column['field'] in board_row


class TestRowBuilders:
    def test_a_voided_run_says_so_in_its_status(self):
        row = run_rows([_run(reattempted=True)])[0]
        assert row['status'] == 'Finished (voided)'
        assert row['grantable'] is False, 'an already-voided run offers nothing'

    def test_an_expired_run_is_distinguishable_from_a_chosen_forfeit(self):
        """``expired_at`` exists to tell the two apart and no surface read it."""
        chosen = run_rows([_run(status='forfeit')])[0]
        expired = run_rows([_run(status='forfeit', expired_at=object())])[0]
        assert chosen['status'] == 'Forfeited'
        assert expired['status'] == 'Forfeited (ran out of time)'

    def test_an_in_progress_run_has_no_review_verdict_to_show(self):
        """It was rendering 'pending', which read as "a reviewer has this"."""
        assert run_rows([_run(status='in_progress')])[0]['review'] == '—'
        assert run_rows([_run(review_status='approved')])[0]['review'] == 'Approved'

    def test_an_in_progress_run_is_not_grantable(self):
        assert run_rows([_run(status='in_progress')])[0]['grantable'] is False

    def test_a_forfeit_is_grantable_because_it_never_reaches_the_queue(self):
        assert run_rows([_run(status='forfeit')])[0]['grantable'] is True

    def test_an_unscored_run_renders_an_empty_score_not_a_zero(self):
        assert run_rows([_run(score=None)])[0]['score'] == ''

    def test_a_detached_permalink_falls_back_rather_than_raising(self):
        assert run_rows([_run(permalink=None)])[0]['pool'] == '—'

    def test_board_rows_carry_the_rank_the_scoring_function_emitted(self):
        entries = [SimpleNamespace(rank=1, username=n, actual=5.0, estimate=5.0,
                                   slots_filled=1, slots_total=1) for n in 'AB']
        assert [r['rank'] for r in board_rows(entries)] == [1, 1]


class TestOtherRunsSummary:
    def test_it_subtracts_the_run_being_reviewed(self):
        tally = {7: {'approved': 2, 'pending': 1}}
        line = other_runs_summary(tally, _run(review_status='pending'))
        assert '2 other runs' in line
        assert '2 approved' in line
        assert 'pending' not in line, 'the reviewed run was the only pending one'

    def test_a_runners_only_run_produces_no_line(self):
        assert other_runs_summary({7: {'approved': 1}}, _run()) == ''

    def test_an_unknown_runner_produces_no_line(self):
        assert other_runs_summary({}, _run()) == ''

    def test_it_names_the_runner_and_agrees_on_plurals(self):
        line = other_runs_summary({7: {'approved': 1, 'forfeit': 1}}, _run())
        assert line.startswith('Runner One: 1 other run in this qualifier')

    def test_a_voided_run_is_labelled_voided_not_by_its_review_status(self):
        tally = {7: {'voided': 1, 'approved': 1}}
        line = other_runs_summary(tally, _run(reattempted=True))
        assert '1 approved' in line and 'voided' not in line

    def test_a_non_finished_run_is_labelled_by_its_status(self):
        tally = {7: {'forfeit': 2}}
        line = other_runs_summary(tally, _run(status='forfeit', review_status='approved'))
        assert '1 other run' in line and '1 forfeit' in line


class TestSmallHelpers:
    def test_short_url_leaves_a_short_one_alone(self):
        assert short_url('https://x/y') == 'https://x/y'

    def test_short_url_elides_a_long_one(self):
        out = short_url('https://alttpr.com/en/h/' + 'a' * 100, limit=30)
        assert len(out) == 31 and out.endswith('…')

    def test_short_url_tolerates_none(self):
        assert short_url(None) == ''

    def test_enum_value_accepts_both_an_enum_and_a_string(self):
        assert enum_value(SimpleNamespace(value='finished')) == 'finished'
        assert enum_value('finished') == 'finished'

    def test_live_race_color_falls_back_for_an_unknown_status(self):
        assert live_race_color('scheduled') == 'grey'
        assert live_race_color(SimpleNamespace(value='in_progress')) == 'orange'
        assert live_race_color('something_new') == 'grey'


def test_a_live_race_status_reads_as_words_not_the_enum_value():
    from models import AsyncQualifierLiveRaceStatus as S

    assert live_race_label(S.IN_PROGRESS) == 'Racing'
    assert live_race_label(S.PENDING) == 'Room open'
    assert live_race_label('scheduled') == 'Scheduled'
    assert all('_' not in live_race_label(s) for s in S)


def test_the_unmatched_racer_remedy_does_not_send_qualifier_admins_to_a_staff_tab():
    """Admin → Users is STAFF-only and cannot link a racetime account anyway."""
    import inspect

    import pages.admin_tabs.admin_qualifiers.live_races as live_races

    assert 'Admin → Users' not in inspect.getsource(live_races)
