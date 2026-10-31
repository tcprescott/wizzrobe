"""The table findings the customization work closed, and the guards against their return.

Each class below pins one finding from the table UX audit. They are source scans
because that is where the regression would appear: a *new* board that declares a
column `'filterable': True` and calls it search, or a *new* family view that
re-adds the pagination handler, is the failure — and only a scan sees it.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRESENTATION = [REPO / 'pages', REPO / 'theme']
FAMILY_VIEWS = [REPO / 'theme' / 'tables' / f'{n}.py'
                for n in ('match', 'user', 'tournament')]


def _python_files():
    for root in PRESENTATION:
        for path in root.rglob('*.py'):
            if '__pycache__' not in str(path):
                yield path


def _column_dicts(path: Path):
    """Every column dict literal in a file, as ``(lineno, {keys: value})``."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if 'name' not in keys or 'field' not in keys:
            continue
        entry = {}
        for key, value in zip(node.keys, node.values, strict=False):
            if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                entry[key.value] = value.value
        yield node.lineno, entry


class TestTheDeadConventionsStayGone:
    """`filterable` and `clickable` are not Quasar properties and never were."""

    def test_no_column_declares_filterable_or_clickable(self):
        offenders = []
        for path in _python_files():
            source = path.read_text()
            for convention in ("'filterable'", "'clickable'"):
                if convention in source:
                    offenders.append(f'{path.relative_to(REPO)} ({convention})')
        assert not offenders, (
            "these are not QTable column properties — nothing reads them, so a "
            "column carrying one looks searchable and is not. Use "
            f"theme.tables.preferences.search_input instead: {offenders}")

    def test_search_binds_quasars_real_filter_prop(self):
        source = (REPO / 'theme' / 'tables' / 'preferences.py').read_text()
        assert 'bind_filter_from(' in source


class TestSortableColumnsSortOnSomethingSortable:
    def test_no_joined_roster_column_is_sortable(self):
        joined = {'players', 'commentators', 'trackers', 'roles', 'slots'}
        offenders = []
        for path in _python_files():
            for lineno, column in _column_dicts(path):
                if column.get('name') in joined and column.get('sortable'):
                    offenders.append(f'{path.relative_to(REPO)}:{lineno} {column["name"]}')
        assert not offenders, (
            'a joined or composite cell sorts as a string, which orders by '
            f'whoever happens to be listed first: {offenders}')

    def test_the_boards_the_audit_named_are_sortable_now(self):
        expected = {
            'pages/admin_tabs/admin_users.py': {'username', 'preferred_name',
                                                'pronouns', 'challonge'},
        }
        for rel, names in expected.items():
            sortable = {c.get('name') for _, c in _column_dicts(REPO / rel)
                        if c.get('sortable')}
            assert names <= sortable, f'{rel}: {sorted(names - sortable)} still unsortable'


class TestPagination:
    def test_the_family_views_do_not_refresh_on_pagination(self):
        offenders = [
            str(p.relative_to(REPO)) for p in FAMILY_VIEWS
            if "on('update:pagination'" in p.read_text()
        ]
        assert not offenders, offenders

    def test_the_family_views_ship_a_real_page_size(self):
        for path in FAMILY_VIEWS:
            source = path.read_text()
            assert "pagination={'rowsPerPage': 25, 'page': 1}" in source, path
            assert '# pagination=' not in source, path


class TestExport:
    def test_csv_export_lives_outside_the_reports(self):
        assert (REPO / 'theme' / 'tables' / 'export.py').exists()
        shared = (REPO / 'pages' / 'admin_tabs' / 'reports' / 'shared.py').read_text()
        assert 'from theme.tables.export import csv_export_button' in shared

    def test_csv_export_follows_the_visible_plan(self):
        for rel in ('theme/tables/match.py', 'theme/tables/tournament.py'):
            source = (REPO / rel).read_text()
            assert 'lambda: self._plan.columns if self._plan else self.columns' in source, rel
        users = (REPO / 'pages' / 'admin_tabs' / 'admin_users.py').read_text()
        assert 'table_view._plan.columns if table_view._plan else columns' in users


class TestStickyHeaders:
    def test_the_helper_sets_both_halves(self):
        source = (REPO / 'theme' / 'tables' / 'preferences.py').read_text()
        helper = source[source.index('def sticky_header'):]
        assert "classes(add='wiz-table--sticky')" in helper
        assert 'max-height' in helper

    def test_the_stylesheet_pairs_the_class_with_a_scroll_container(self):
        css = (REPO / 'static' / 'css' / 'styles.css').read_text()
        assert '.wiz-table--sticky:not(.q-table--grid) .q-table__middle' in css
        assert '.wiz-table--sticky thead tr th' in css

    def test_the_cap_skips_the_phone_grid(self):
        source = (REPO / 'theme' / 'tables' / 'preferences.py').read_text()
        helper = source[source.index('def sticky_header'):]
        assert "style(f'max-height" not in helper
        css = (REPO / 'static' / 'css' / 'styles.css').read_text()
        assert re.search(
            r'\.wiz-table--sticky:not\(\.q-table--grid\)\s*\{\s*max-height: var\(--wiz-sticky-max-height',
            css,
        )


class TestTheEventLog:
    def test_it_renders_over_props_cols_so_reordering_lands(self):
        source = (REPO / 'pages' / 'admin_tabs' / 'reports' / 'shared.py').read_text()
        assert 'v-for="col in props.cols"' in source
        assert not re.search(r'table-prefs:\s*exempt', source)
