"""Drag-to-resize: what reaches the table, and the two guards in the client.

The drag itself is browser behaviour and no unit test can prove it. What can be
pinned here is everything around it — that a stored width becomes a real column
style, that the fixed-layout class is *conditional*, that a hand-crafted
``emitEvent`` cannot store a 9000px column, and that the client keeps its
desktop-only and offline guards.
"""

from pathlib import Path

import pytest
from nicegui import ui

from application.services.table_preference_service import TablePreferenceService
from application.table_preferences_context import table_prefs_scope
from tests.factories import make_user
from theme.tables.preferences import customize_table

REPO = Path(__file__).resolve().parents[2]
CLIENT = (REPO / 'static' / 'js' / 'table-columns.js').read_text()

COLUMNS = [
    {'name': 'username', 'label': 'Username', 'field': 'username'},
    {'name': 'roles', 'label': 'Roles', 'field': 'roles'},
    {'name': 'actions', 'label': '', 'field': 'actions'},
]

pytestmark = pytest.mark.anyio


@pytest.fixture
def table():
    # Building any element needs a slot context; there is no page here.
    from nicegui.client import Client

    with Client(lambda: None, request=None):
        # A fixture, not a rendered surface.
        # mobile-grid: exempt, table-prefs: exempt
        yield ui.table(columns=[dict(c) for c in COLUMNS], rows=[], row_key='id')


class TestWidthsReachTheTable:
    def test_a_stored_width_pins_the_column_from_both_sides(self, table):
        saved = {'columns': [{'name': 'username', 'visible': True, 'width': 260}]}
        with table_prefs_scope({'admin.users': saved}):
            customize_table(table, COLUMNS, key='admin.users')
        username = next(c for c in table.columns if c['name'] == 'username')
        for style in (username['style'], username['headerStyle']):
            assert 'width: 260px' in style
            assert 'min-width: 260px' in style
            assert 'max-width: 260px' in style

    def test_a_sized_table_does_not_switch_to_a_fixed_layout(self, table):
        css = (REPO / 'static' / 'css' / 'styles.css').read_text()
        assert 'table-layout: fixed' not in css

    def test_a_table_with_no_widths_does_not_get_fixed_layout(self, table):
        with table_prefs_scope({'admin.users': {'density': 'compact'}}):
            customize_table(table, COLUMNS, key='admin.users')
        assert 'wiz-table--sized' not in table.classes

    def test_every_header_carries_its_column_name(self, table):
        customize_table(table, COLUMNS, key='admin.users')
        assert all(f"wiz-col-{c['name']}" in c['headerClasses'] for c in table.columns)

    def test_the_table_carries_its_key(self, table):
        customize_table(table, COLUMNS, key='admin.users')
        assert table._props['data-wiz-table-key'] == 'admin.users'


class TestTheStore:
    async def test_set_width_null_clears_the_column(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.set_width(user, 'admin.users', 'username', 300)
        config = await service.set_width(user, 'admin.users', 'username', None)
        assert config['columns'] == [
            {'name': 'username', 'visible': True, 'width': None}]

    async def test_width_out_of_range_is_rejected(self, db):
        user = await make_user()
        for bad in (9000, 1, -5):
            with pytest.raises(ValueError):
                await TablePreferenceService().set_width(user, 'admin.users', 'x', bad)


class TestTheClient:
    def test_it_guards_on_the_desktop_breakpoint(self):
        assert "matchMedia" in CLIENT
        assert "(min-width: 1024px)" in CLIENT

    def test_it_refuses_to_write_while_offline(self):
        assert 'wiz-offline' in CLIENT
        assert 'isOffline()' in CLIENT

    def test_it_emits_on_pointerup_not_pointermove(self):
        move = CLIENT.index("addEventListener('pointermove'")
        handler = CLIENT[move:CLIENT.index('function finish(', move)]
        assert 'emit(' not in handler, 'no server traffic during the drag'
        assert "addEventListener('pointerup', function () { finish(true); });" in CLIENT

    def test_it_uses_pointer_capture(self):
        assert 'setPointerCapture' in CLIENT

    def test_double_click_clears_the_width(self):
        assert "addEventListener('dblclick'" in CLIENT
        assert 'emit(hit.key, hit.column, null)' in CLIENT

    def test_its_bounds_match_the_service(self):
        assert f'MIN_WIDTH = {TablePreferenceService.MIN_WIDTH}' in CLIENT
        assert f'MAX_WIDTH = {TablePreferenceService.MAX_WIDTH}' in CLIENT

    def test_it_injects_no_handle_elements(self):
        assert 'createElement' not in CLIENT

    def test_the_handler_captures_the_client_at_build_time(self):
        source = (REPO / 'theme' / 'tables' / 'preferences.py').read_text()
        wiring = source[source.index('def _wire_width_drags'):]
        assert 'client = context.client' in wiring
        assert 'with client:' in wiring


class TestTheStylesheet:
    def test_a_pinned_column_truncates_rather_than_spilling(self):
        css = (REPO / 'static' / 'css' / 'styles.css').read_text()
        assert '.wiz-table--sized thead th[style*="max-width"]' in css
        assert 'text-overflow: ellipsis' in css

    def test_the_resize_affordance_is_hidden_on_mobile(self):
        css = (REPO / 'static' / 'css' / 'styles.css').read_text()
        mobile = css[css.index('@media (max-width: 1023.98px) {\n    .wiz-table thead th::after'):]
        assert 'display: none;' in mobile[:400]
