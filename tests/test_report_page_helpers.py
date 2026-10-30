"""Pure helpers from the reports presentation layer.

There is no ``tests/pages/`` — report bodies are verified in the browser — but
these two are ordinary functions with no NiceGUI involvement, and both encode a
rule that is easy to get subtly wrong (a count that disagrees with the page it
labels; a URL that carries a hand-built tenant prefix).
"""

from datetime import date

from application.services.tenant_theme_service import THEME_PRESETS, TenantThemeService
from application.utils.color_contrast import contrast_ratio
from pages.admin_tabs.links import SCHEDULE, VOL_SCHEDULE, admin_url
from pages.admin_tabs.reports.insights import (
    HEALTH_DISPLAY_CELLS,
    HEALTH_SORTED_DISPLAY_FIELDS,
    health_display_rows,
)
from pages.admin_tabs.reports.shared import (
    CHART_AMBER,
    CHART_TEAL,
    _page_range_label,
    _refresh_params,
    chart_palette_for,
    reports_url,
)


class TestPageRangeLabel:
    def test_single_page_states_the_plain_count(self):
        assert _page_range_label(12, 1, 50, 'entries') == '12 entries'

    def test_exactly_one_full_page_is_not_a_range(self):
        assert _page_range_label(50, 1, 50, 'entries') == '50 entries'

    def test_first_of_several_pages_says_so(self):
        assert _page_range_label(124, 1, 50, 'entries') == 'Showing 1–50 of 124 entries'

    def test_middle_page(self):
        assert _page_range_label(124, 2, 50, 'entries') == 'Showing 51–100 of 124 entries'

    def test_last_page_is_clamped_to_the_total(self):
        assert _page_range_label(124, 3, 50, 'entries') == 'Showing 101–124 of 124 entries'

    def test_page_past_the_end_does_not_invert(self):
        assert _page_range_label(95, 3, 50, 'entries') == '95 entries — page 3 is past the end'

    def test_thousands_are_grouped(self):
        assert _page_range_label(12345, 2, 50, 'events') == 'Showing 51–100 of 12,345 events'


class TestAdminUrl:
    def test_root_relative_with_no_tenant_prefix(self):
        """NiceGUI prepends the client's path prefix; a hand-built /t/<slug>
        here would double it under path-mode tenancy."""
        assert admin_url(SCHEDULE, match_id=412) == '/admin/match-schedule?match_id=412'

    def test_absent_params_are_dropped(self):
        assert admin_url(VOL_SCHEDULE, day=None, other='') == '/admin/volunteer-shifts'

    def test_bare_section(self):
        assert admin_url(SCHEDULE) == '/admin/match-schedule'

    def test_dates_are_iso_formatted(self):
        assert admin_url('reports', start=date(2026, 1, 2)) == '/admin/reports?start=2026-01-02'

    def test_reports_url_is_admin_url_with_the_report_first(self):
        assert reports_url('crew', tournament_id=3) == '/admin/reports?report=crew&tournament_id=3'

    def test_reports_url_without_a_report_is_the_dashboard(self):
        assert reports_url() == '/admin/reports'
        assert reports_url(start=date(2026, 1, 2)) == '/admin/reports?start=2026-01-02'


class TestRefreshParams:
    """A filter change re-renders in place, so its params must arrive looking
    exactly like the ones a page load at the same URL would have delivered."""

    def test_dates_become_iso_strings(self):
        # A str, as the route annotates it: a date object would fail parse_date and silently reset the range.
        assert _refresh_params({'start': date(2026, 1, 2), 'end': date(2026, 1, 5)}) == {
            'start': '2026-01-02', 'end': '2026-01-05',
        }

    def test_absent_params_are_dropped_so_the_handler_default_applies(self):
        assert _refresh_params({'tournament_id': None, 'approval': ''}) == {}

    def test_ints_keep_their_type(self):
        assert _refresh_params({'tournament_id': 3, 'page': 2}) == {
            'tournament_id': 3, 'page': 2,
        }

    def test_zero_survives(self):
        assert _refresh_params({'page': 0}) == {'page': 0}

    def test_matches_what_the_url_would_have_carried(self):
        params = {'start': date(2026, 1, 2), 'tournament_id': 3, 'user_id': None}
        assert reports_url(report='crew', **params) == (
            '/admin/reports?report=crew&start=2026-01-02&tournament_id=3'
        )
        assert _refresh_params(params) == {'start': '2026-01-02', 'tournament_id': 3}


class TestHealthDisplayRows:
    """A sortable column must hold the value Quasar sorts, not its rendering.

    Quasar sorts the raw field value. The Tournament-health table declared four
    formatted columns sortable — three percentages rendered as ``'85%'`` and a
    score that fell back to ``'—'`` — so ``'100%' < '10%' < '9%'`` as text and
    the report ranked tournaments backwards while looking entirely plausible.
    The number now lives in the field and the rendering beside it in
    ``<name>_display``.
    """

    @staticmethod
    def _row(**over):
        base = {
            'tournament_name': 'Spring', 'health_score': 82.0,
            'matches_total': 10, 'matches_past': 8, 'matches_finished': 6,
            'completion_pct': 75.0, 'on_time_pct': 9.0, 'coverage_pct': 100.0,
            'avg_start_delay_min': 3.0, 'avg_duration_min': 41.0,
            'expected_avg_min': 40,
        }
        base.update(over)
        return base

    def test_every_sortable_column_holds_a_number(self):
        [row] = health_display_rows([self._row()])
        for field in HEALTH_SORTED_DISPLAY_FIELDS:
            assert isinstance(row[field], (int, float)), field

    def test_percentages_sort_numerically_not_lexically(self):
        rows = health_display_rows([
            self._row(tournament_name='a', completion_pct=9.0),
            self._row(tournament_name='b', completion_pct=10.0),
            self._row(tournament_name='c', completion_pct=100.0),
        ])
        ordered = [r['tournament_name'] for r in sorted(rows, key=lambda r: r['completion_pct'])]
        assert ordered == ['a', 'b', 'c']

    def test_the_rendered_string_is_unchanged(self):
        [row] = health_display_rows([self._row()])
        assert row['completion_pct_display'] == '75%'
        assert row['on_time_pct_display'] == '9%'
        assert row['coverage_pct_display'] == '100%'
        assert row['health_score_display'] == 82.0

    def test_an_unscoreable_tournament_keeps_none_in_the_field_and_a_dash_on_screen(self):
        [row] = health_display_rows([self._row(health_score=None, coverage_pct=None)])
        assert row['health_score'] is None
        assert row['health_score_display'] == '—'
        assert row['coverage_pct'] is None
        assert row['coverage_pct_display'] == '—'

    def test_every_sortable_formatted_column_has_a_cell_template(self):
        assert set(HEALTH_DISPLAY_CELLS) == set(HEALTH_SORTED_DISPLAY_FIELDS)
        for name, snippet in HEALTH_DISPLAY_CELLS.items():
            assert f'props.row.{name}_display' in snippet

    def test_unsortable_columns_may_stay_formatted(self):
        [row] = health_display_rows([self._row()])
        assert row['matches'] == '6/8'
        assert row['avg_duration_min'] == '41 (40)'


class TestChartPalette:
    """Charts are canvas, painted once for both modes, in the community's colour."""

    def test_default_accent_is_used_as_is(self):
        assert chart_palette_for('#9c6b12').primary == '#9c6b12'

    def test_every_preset_clears_3_to_1_on_both_card_surfaces(self):
        surfaces = ('#ffffff', '#f5f5f4', '#fafaf9', '#1f1d1a', '#161513', '#262420')
        for preset in THEME_PRESETS.values():
            primary = chart_palette_for(preset['primary']).primary
            assert min(contrast_ratio(primary, s) for s in surfaces) >= 3.0, (preset, primary)

    def test_a_dark_accent_is_lifted_not_replaced(self):
        primary = TenantThemeService.chart_series_colour('#6d28d9')
        assert primary != '#6d28d9'
        assert primary[1:3] > '6d'

    def test_second_series_is_never_the_accents_hue(self):
        assert chart_palette_for('#9c6b12').secondary == CHART_TEAL
        assert chart_palette_for('#17a097').secondary == CHART_AMBER

    def test_area_wash_is_the_primary_colour(self):
        assert chart_palette_for('#9c6b12').primary_area == 'rgba(156, 107, 18, 0.18)'


def test_schedule_drill_links_only_matches_the_viewers_board_shows():
    """A tournament admin's board holds only their tournaments, so a report row
    from any other tournament gets no "Open on the schedule board" link."""
    from pages.admin_tabs.reports.shared import schedule_drill

    url_for, enabled = schedule_drill(frozenset({7}))
    assert enabled
    assert url_for({'match_id': 1, 'tournament_id': 7})
    assert url_for({'match_id': 2, 'tournament_id': 8}) is None

    url_for, enabled = schedule_drill(None)
    assert enabled and url_for({'match_id': 2, 'tournament_id': 8})

    _url_for, enabled = schedule_drill(frozenset())
    assert not enabled
