"""Applying a saved layout to a live table — and never to its mobile card.

The mobile card is protected structurally, not conditionally: ``enable_mobile_grid``
(and each bespoke ``render_grid_slot``) bakes the card's ``item`` slot from the
**defaults** at build time, addressing ``props.row.<field>`` directly, so it never
reads ``table.columns`` or ``visible-columns``. That only holds while
``customize_table`` is called *after* the slot is generated, which is what the
byte-identical assertion and the AST order scan below exist to keep true.
"""

import ast
from pathlib import Path

import pytest
from nicegui import ui

from application.services.table_preference_service import TablePreferenceService
from application.table_preferences_context import table_prefs_scope
from theme.tables.mobile_grid import enable_mobile_grid
from theme.tables.preferences import (
    ROWS_PER_PAGE_OPTIONS,
    apply_saved_config,
    customization_of,
    customize_table,
)

REPO = Path(__file__).resolve().parents[2]

COLUMNS = [
    {'name': 'id', 'label': 'ID', 'field': 'id', 'hidden': True},
    {'name': 'username', 'label': 'Username', 'field': 'username'},
    {'name': 'pronouns', 'label': 'Pronouns', 'field': 'pronouns'},
    {'name': 'roles', 'label': 'Roles', 'field': 'roles'},
    {'name': 'actions', 'label': '', 'field': 'actions'},
]

ROWS = [{'id': 1, 'username': 'alice', 'pronouns': 'they/them', 'roles': 'Staff'}]

SAVED = {
    'columns': [
        {'name': 'roles', 'visible': True, 'width': 220},
        {'name': 'username', 'visible': True, 'width': None},
        {'name': 'pronouns', 'visible': False, 'width': None},
    ],
    'page_size': 25,
    'density': 'compact',
    'wrap': True,
}


@pytest.fixture
def table():
    # Building any element needs a slot context; there is no page here.
    from nicegui.client import Client

    with Client(lambda: None, request=None):
        # A fixture; each test adds the card and the key it needs.
        # mobile-grid: exempt, table-prefs: exempt
        yield ui.table(columns=[dict(c) for c in COLUMNS],
                       rows=[dict(r) for r in ROWS], row_key='id')


class TestTheMobileCardIsUntouched:
    def test_preferences_do_not_touch_the_mobile_card(self, table):
        enable_mobile_grid(table, COLUMNS, actions='<q-btn />')
        before = table.slots['item'].template

        with table_prefs_scope({'admin.users': SAVED}):
            customize_table(table, COLUMNS, key='admin.users')

        assert table.slots['item'].template == before

    def test_row_dicts_are_untouched_by_hiding(self, table):
        with table_prefs_scope({'admin.users': SAVED}):
            customize_table(table, COLUMNS, key='admin.users')
        assert table.rows == ROWS


class TestThePlanReachesTheTable:
    def test_visible_columns_prop_matches_the_plan(self, table):
        with table_prefs_scope({'admin.users': SAVED}):
            plan = customize_table(table, COLUMNS, key='admin.users')
        assert table._props['visible-columns'] == plan.visible
        assert 'pronouns' not in plan.visible
        assert 'id' not in plan.visible

    def test_column_order_follows_the_saved_order(self, table):
        with table_prefs_scope({'admin.users': SAVED}):
            customize_table(table, COLUMNS, key='admin.users')
        assert [c['name'] for c in table.columns][:2] == ['roles', 'username']

    def test_a_stored_width_reaches_the_column_style(self, table):
        with table_prefs_scope({'admin.users': SAVED}):
            customize_table(table, COLUMNS, key='admin.users')
        roles = next(c for c in table.columns if c['name'] == 'roles')
        assert 'width: 220px' in roles['style']

    def test_density_and_page_size_reach_the_props(self, table):
        with table_prefs_scope({'admin.users': SAVED}):
            customize_table(table, COLUMNS, key='admin.users')
        assert table._props['dense'] is True
        assert table._props['pagination']['rowsPerPage'] == 25
        assert table._props['hide-pagination'] is False

    def test_a_sized_table_gets_the_fixed_layout_class(self, table):
        with table_prefs_scope({'admin.users': SAVED}):
            customize_table(table, COLUMNS, key='admin.users')
        assert 'wiz-table--sized' in table.classes
        assert 'wiz-table--wrap' in table.classes

    def test_an_unsized_table_does_not(self, table):
        customize_table(table, COLUMNS, key='admin.users')
        assert 'wiz-table--sized' not in table.classes

    def test_an_unprimed_context_renders_defaults(self, table):
        # /platform and the public bracket views never prime; defaults are the
        # correct answer there, not a failure.
        plan = customize_table(table, COLUMNS, key='admin.users')
        assert plan.is_customized is False
        assert [c['name'] for c in table.columns] == [c['name'] for c in COLUMNS]

    def test_the_shipped_page_size_is_the_fallback(self, table):
        table.pagination = {'rowsPerPage': 15}
        plan = customize_table(table, COLUMNS, key='reports.example')
        assert plan.page_size == 15


class TestRowsPerPageLivesInTheFooter:
    """One control for rows per page: the table footer, and it saves."""

    def test_the_footer_offers_exactly_what_the_service_accepts(self):
        assert set(ROWS_PER_PAGE_OPTIONS) == set(TablePreferenceService.ALLOWED_PAGE_SIZES)

    def test_every_customized_table_gets_the_footer_options(self, table):
        customize_table(table, COLUMNS, key='admin.users')
        assert table._props['rows-per-page-options'] == ROWS_PER_PAGE_OPTIONS
        assert table._props['rows-per-page-label'] == 'Rows per page:'

    def test_the_footer_stays_on_all(self, table):
        # Hiding it on "All" would leave no way to pick a size again.
        with table_prefs_scope({'admin.users': {'page_size': 0}}):
            customize_table(table, COLUMNS, key='admin.users')
        assert table._props['pagination']['rowsPerPage'] == 0
        assert table._props['hide-pagination'] is False

    def test_a_server_paged_table_has_no_footer_and_ignores_a_saved_size(self, table):
        with table_prefs_scope({'admin.users': {'page_size': 25}}):
            plan = customize_table(table, COLUMNS, key='admin.users', pager=False)
        assert plan.page_size == 0
        assert table._props['hide-pagination'] is True
        assert 'rows-per-page-options' not in table._props

    def test_the_preferences_dialog_has_no_rows_per_page_control(self):
        source = (REPO / 'theme/dialog/table_preferences_dialog.py').read_text()
        assert "ui.label('Rows per page')" not in source
        assert 'set_page_size' not in source

    def test_every_shipped_page_size_is_one_the_footer_offers(self):
        # A table built with pagination=15 would show 15 in the footer, and the
        # footer could never save it back.
        import re
        allowed = set(ROWS_PER_PAGE_OPTIONS)
        bad = []
        for path in [*(REPO / 'pages').rglob('*.py'), *(REPO / 'theme').rglob('*.py')]:
            text = path.read_text()
            for m in re.finditer(r"(?:pagination=|'rowsPerPage':\s*)(\d+)", text):
                if int(m.group(1)) not in allowed:
                    bad.append(f'{path.relative_to(REPO)}: {m.group(0)}')
        assert not bad, bad


class TestReapplying:
    def test_apply_saved_config_replaces_the_plan(self, table):
        customize_table(table, COLUMNS, key='admin.users')
        apply_saved_config(table, SAVED)
        assert [c['name'] for c in table.columns][:2] == ['roles', 'username']
        assert customization_of(table).plan.is_customized is True

    def test_apply_none_restores_the_defaults(self, table):
        with table_prefs_scope({'admin.users': SAVED}):
            customize_table(table, COLUMNS, key='admin.users')
        apply_saved_config(table, None)
        assert [c['name'] for c in table.columns] == [c['name'] for c in COLUMNS]
        assert table._props['visible-columns'] == [
            'username', 'pronouns', 'roles', 'actions']

    def test_a_table_without_a_key_cannot_be_reapplied(self, table):
        with pytest.raises(ValueError):
            apply_saved_config(table, SAVED)


class TestTheCarriers:
    def test_capture_render_context_carries_preferences(self):
        # The wave's subtlest bug: a background refresh reverting to defaults.
        from theme.tables.admin_crud import capture_render_context

        with table_prefs_scope({'admin.users': SAVED}):
            context = capture_render_context()
        assert context[2] == {'admin.users': SAVED}

    def test_base_layout_primes_and_scopes(self):
        source = (REPO / 'theme' / 'base.py').read_text()
        assert 'TablePreferenceService().prime(self.user)' in source
        # Both carriers: the page build and the lazy tab build.
        assert source.count('table_prefs_scope(') >= 2


class TestCallOrder:
    """A comment is not enforcement; the AST is."""

    def _order_in(self, path: Path) -> list[tuple[str, int]]:
        tree = ast.parse(path.read_text())
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ('enable_mobile_grid', 'customize_table',
                                    'render_grid_slot'):
                    calls.append((node.func.id, node.lineno))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ('render_grid_slot',):
                    calls.append((node.func.attr, node.lineno))
        return sorted(calls, key=lambda c: c[1])

    def test_customize_table_is_called_after_the_grid_slot(self):
        offenders = []
        for root in (REPO / 'pages', REPO / 'theme'):
            for path in root.rglob('*.py'):
                if '__pycache__' in str(path) or path.name == 'preferences.py':
                    continue
                calls = self._order_in(path)
                names = [c[0] for c in calls]
                if 'customize_table' not in names:
                    continue
                card_lines = [line for name, line in calls
                              if name in ('enable_mobile_grid', 'render_grid_slot')]
                custom_lines = [line for name, line in calls if name == 'customize_table']
                if card_lines and min(custom_lines) < max(card_lines):
                    offenders.append(path.relative_to(REPO))
        assert not offenders, (
            'customize_table must be called after the mobile card slot is '
            f'generated: {offenders}')
