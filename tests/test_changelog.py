"""The in-app changelog: the shipped files, their parser, and the page's wiring.

The files are the part worth guarding. Entries are written by hand in PRs, so the
tests below are what keep it readable: every heading parses, entries run newest
first without overlapping, and each one covers whole Monday-to-Sunday weeks —
the changelog summarizes a week or more at a time, never a single day. The
per-PR ``changes/`` files get the same scrutiny, since a misnamed one is
silently dropped from the page.
"""

import inspect
import re
from datetime import date
from itertools import pairwise

import pytest

import pages.changelog as changelog_page
import pages.help as help_page
import theme.base as base
from application.changelog import parse_changelog, reload_entries
from application.changelog.catalog import (
    CATEGORIES,
    CHANGELOG_PATH,
    CHANGES_DIR,
    ENTRY_HEADING_RE,
    UNSORTED,
    UNTITLED_WEEK,
    all_entries,
    apply_changes,
    change_file_date,
    split_categories,
)
from middleware.auth import protected_routes


def _headings() -> list[str]:
    text = CHANGELOG_PATH.read_text(encoding='utf-8')
    return [line for line in text.splitlines() if line.startswith('## ')]


class TestShippedChangelog:
    def setup_method(self):
        reload_entries()

    def test_every_heading_is_an_entry(self):
        bad = [h for h in _headings() if not ENTRY_HEADING_RE.match(h)]
        assert not bad, f'Entry headings must read "## YYYY-MM-DD/YYYY-MM-DD Title": {bad}'
        text = CHANGELOG_PATH.read_text(encoding='utf-8')
        assert len(parse_changelog(text)) == len(_headings())

    def test_nothing_before_the_first_entry(self):
        text = CHANGELOG_PATH.read_text(encoding='utf-8')
        assert text.startswith('## ')

    def test_file_is_newest_first(self):
        starts = [ENTRY_HEADING_RE.match(h)['start'] for h in _headings()]
        assert starts == sorted(starts, reverse=True)

    def test_entries_cover_whole_weeks(self):
        for entry in all_entries():
            assert entry.start.weekday() == 0, f'{entry.title}: start on a Monday'
            assert entry.end.weekday() == 6, f'{entry.title}: end on a Sunday'

    def test_entries_do_not_overlap(self):
        entries = all_entries()
        for newer, older in pairwise(entries):
            assert older.end < newer.start, f'{older.title} overlaps {newer.title}'

    def test_every_entry_has_a_body(self):
        for entry in all_entries():
            assert entry.blocks, f'{entry.title} has no content'

    def test_every_line_has_a_category(self):
        for entry in all_entries():
            stray = [s.category for s in entry.sections if s.category not in CATEGORIES]
            assert not stray, (
                f'{entry.title}: put every bullet under one of '
                f'{", ".join("### " + c for c in CATEGORIES)}'
            )


def _change_files():
    return [p for p in CHANGES_DIR.iterdir() if not p.name.startswith('.')]


class TestShippedChangeFiles:
    def test_every_change_file_is_named_by_date(self):
        for path in _change_files():
            assert change_file_date(path.name), (
                f'changes/{path.name}: name it YYYY-MM-DD-short-topic.md (lowercase, hyphens)'
            )

    def test_every_change_file_is_categorized_bullets(self):
        """A ``## `` heading would start a second entry inside another week's;
        prose breaks the list; a bullet outside a category lands in "More changes"."""
        for path in _change_files():
            runs = split_categories(path.read_text(encoding='utf-8'), path.name)
            assert runs, f'changes/{path.name} is empty'
            for name, blocks in runs:
                assert name in CATEGORIES, (
                    f'changes/{path.name}: start with a category heading, one of '
                    f'{", ".join("### " + c for c in CATEGORIES)}'
                )
                assert {b.kind for b in blocks} == {'bullets'}, f'changes/{path.name}: bullets only'


class TestChangeFiles:
    def _entries(self, text: str):
        return parse_changelog(text)

    def test_a_change_joins_the_entry_covering_its_date(self):
        entries = self._entries('## 2026-10-05/2026-10-11 Check-in\n\n### New\n\n- One\n')
        [entry] = apply_changes(entries, [(date(2026, 10, 9), '### New\n- Two\n')])
        assert entry.title == 'Check-in'
        [bullets] = entry.blocks
        assert [item[0].text for item in bullets.items] == ['One', 'Two']

    def test_a_heading_only_entry_takes_its_weeks_changes(self):
        entries = self._entries('## 2026-10-12/2026-10-18 Help for staff\n')
        [entry] = apply_changes(entries, [(date(2026, 10, 13), '- A\n'), (date(2026, 10, 18), '- B\n')])
        assert entry.title == 'Help for staff'
        assert len(entry.blocks[0].items) == 2

    def test_a_change_with_no_entry_opens_its_week(self):
        entries = self._entries('## 2026-10-05/2026-10-11 Older\n\n- X\n')
        newer, older = apply_changes(entries, [(date(2026, 10, 15), '- Y\n'), (date(2026, 10, 12), '- Z\n')])
        assert (newer.start, newer.end, newer.title) == (date(2026, 10, 12), date(2026, 10, 18), UNTITLED_WEEK)
        assert [item[0].text for item in newer.blocks[0].items] == ['Y', 'Z']
        assert older.title == 'Older'

    def test_a_multi_week_entry_absorbs_any_day_it_covers(self):
        entries = self._entries('## 2026-08-31/2026-09-27 Quiet month\n\n- X\n')
        [entry] = apply_changes(entries, [(date(2026, 9, 16), '- Y\n')])
        assert len(entry.blocks[0].items) == 2

    @pytest.mark.parametrize('name', [
        '2026-10-12-help-skills.md',
        '2026-10-12-a.md',
    ])
    def test_reads_a_well_formed_name(self, name):
        assert change_file_date(name) == date(2026, 10, 12)

    @pytest.mark.parametrize('name', [
        'help-skills.md',
        '2026-10-12.md',
        '2026-10-12-Help.md',
        '2026-10-12-help_skills.md',
        '2026-02-30-impossible.md',
        '2026-10-12-help.txt',
    ])
    def test_rejects_a_malformed_name(self, name):
        assert change_file_date(name) is None


class TestCategories:
    def test_groups_lines_in_page_order_across_files(self):
        entries = parse_changelog(
            '## 2026-10-05/2026-10-11 Week\n\n### Fixed\n\n- F1\n\n### New\n\n- N1\n'
        )
        [entry] = apply_changes(entries, [
            (date(2026, 10, 6), '### Security\n- S1\n\n### New\n- N2\n'),
            (date(2026, 10, 7), '### Fixed\n- F2\n'),
        ])
        assert [(s.category, s.count) for s in entry.sections] == [
            ('New', 2), ('Fixed', 2), ('Security', 1),
        ]
        [new] = entry.sections[0].blocks
        assert [item[0].text for item in new.items] == ['N1', 'N2']

    def test_an_uncategorized_line_shows_last_instead_of_vanishing(self):
        [entry] = apply_changes([], [
            (date(2026, 10, 6), '- Old style\n'),
            (date(2026, 10, 7), '### Typo\n- Misfiled\n\n### Improved\n- Fine\n'),
        ])
        assert [(s.category, s.count) for s in entry.sections] == [('Improved', 1), (UNSORTED, 2)]

    def test_split_ignores_an_empty_run(self):
        assert [name for name, _ in split_categories('### New\n\n### Fixed\n- X\n', 't')] == ['Fixed']


class TestParser:
    def test_reads_an_entry(self):
        [entry] = parse_changelog('## 2026-10-05/2026-10-11 Check-in\n\n- One\n- Two\n')
        assert entry.start == date(2026, 10, 5)
        assert entry.end == date(2026, 10, 11)
        assert entry.title == 'Check-in'
        assert entry.anchor == '2026-10-05'
        assert [b.kind for b in entry.blocks] == ['bullets']

    def test_sorts_newest_first(self):
        text = '## 2026-01-05/2026-01-11 Older\n\nA\n\n## 2026-02-02/2026-02-08 Newer\n\nB\n'
        assert [e.title for e in parse_changelog(text)] == ['Newer', 'Older']

    def test_skips_a_malformed_heading_instead_of_raising(self):
        text = '## Oct 5 Typo\n\nLost\n\n## 2026-10-05/2026-10-11 Kept\n\nShown\n'
        assert [e.title for e in parse_changelog(text)] == ['Kept']

    def test_skips_an_impossible_or_backwards_interval(self):
        text = '## 2026-02-30/2026-03-01 Bad date\n\nX\n\n## 2026-03-08/2026-03-02 Backwards\n\nY\n'
        assert parse_changelog(text) == []

    def test_period_widens_only_as_far_as_needed(self):
        def period(header: str) -> str:
            return parse_changelog(f'## {header} T\n\nx\n')[0].period

        assert period('2026-10-05/2026-10-11') == 'Oct 5 – 11, 2026'
        assert period('2026-09-28/2026-10-04') == 'Sep 28 – Oct 4, 2026'
        assert period('2025-12-29/2026-01-04') == 'Dec 29, 2025 – Jan 4, 2026'


class TestChangelogPage:
    def test_registers_the_route_as_public(self):
        assert "@public_page('/changelog', scope='community')" in inspect.getsource(changelog_page.create)

    def test_route_never_joins_protected_routes(self):
        changelog_page.create()
        assert '/changelog' not in protected_routes

    def test_page_tolerates_an_anonymous_visitor(self):
        source = inspect.getsource(changelog_page)
        assert not re.search(r'(?<!storage\.)\buser\.\w', source)

    def test_never_renders_through_markdown(self):
        assert 'ui.markdown' not in inspect.getsource(changelog_page)

    def test_frontend_registers_the_page(self):
        with open('frontend.py', encoding='utf-8') as fh:
            assert 'changelog.create()' in fh.read()

    def test_help_index_links_the_page(self):
        assert "ui.navigate.to('/changelog')" in inspect.getsource(help_page)

    def test_page_links_back_to_help(self):
        assert "'/help'" in inspect.getsource(changelog_page.create)

    def test_stays_out_of_the_drawer(self):
        assert '/changelog' not in inspect.getsource(base)
