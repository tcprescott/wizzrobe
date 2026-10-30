"""My Schedule's section panels, and the Event tab's view switcher.

Both surfaces regressed the same way — presentation the Python suite cannot
render, so the defect lived in a prop string and a stack of margin classes until
someone opened a browser.

*The switcher*: ``ui.toggle(...).props('color=primary')`` sets the **inactive**
background on a ``q-btn-toggle``. All three segments came out gold, so no option
looked chosen — Brackets and Schedule rendered pixel-identical.

*The panels*: four sections stacked with ``page-container`` + ``header-row`` +
``separator-spacing`` each, which is ~4em of dead space between every title and
its own content and no visible boundary at all between one section and the next.

Both are pinned by source inspection rather than a render, for the reason
``test_home_nav`` gives: assembling these pages needs a request, a tenant and a
signed-in user, none of which this test has or needs.
"""

import inspect

import pytest

from pages.home_tabs import availability, event, my_crew, my_schedule, player

SECTION_MODULES = [availability, my_crew, player]


class TestTheEventViewSwitcher:
    def test_it_does_not_set_the_inactive_segment_colour(self):
        """``color=`` on a q-btn-toggle paints the *un*selected segments.

        Setting it to primary was the whole bug: every segment was gold, so the
        selected one was indistinguishable from the two beside it.
        """
        props = [
            line for line in inspect.getsource(event.event_tab).splitlines()
            if '.props(' in line and not line.strip().startswith('#')
        ]
        assert props, 'the switcher sets no props at all — did it move?'
        assert not any('color=primary' in line for line in props)

    def test_it_carries_the_segmented_style(self):
        assert "classes('wiz-segmented')" in inspect.getsource(event.event_tab)

    def test_it_shares_the_left_edge_with_the_views_it_switches(self):
        """The views below all use ``page-container``; the switcher used
        ``q-px-md``, so it sat on its own inset."""
        assert 'page-container' in inspect.getsource(event.event_tab)


class TestEverySectionIsAPanel:
    @pytest.mark.parametrize('module', SECTION_MODULES, ids=lambda m: m.__name__)
    def test_the_section_builds_a_panel(self, module):
        assert 'section_panel(' in inspect.getsource(module)

    @pytest.mark.parametrize('module', SECTION_MODULES, ids=lambda m: m.__name__)
    def test_the_section_no_longer_carries_its_own_page_header(self, module):
        """``header-row`` + ``section-title`` is the page-level heading pattern.
        Inside a stack of four it produced four competing page titles."""
        src = inspect.getsource(module)
        assert 'header-row' not in src
        assert "classes('section-title')" not in src

    def test_the_stack_does_not_separate_panels_with_rules(self):
        """A panel has its own edge; a separator between two of them is a second
        boundary drawn over the first."""
        src = inspect.getsource(my_schedule.my_schedule_tab)
        assert 'separator' not in src
        assert 'wiz-section-stack' in src


class TestWhatWaitsOnThePlayerComesFirst:
    def test_the_pick_a_time_lists_render_above_the_match_board(self):
        """A matchup with no time on it is the only thing on this tab waiting on
        the reader. Below the board and its filter card it was off-screen, which
        is where the "matchup ready" DM used to land."""
        src = inspect.getsource(player.render_player_dashboard)
        brackets = src.index('await waiting_section()')
        board = src.index('table_view = MatchTableView(')
        requests = src.index('await requests_section()')
        assert brackets < board < requests
