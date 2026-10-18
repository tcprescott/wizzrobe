"""The pure reconciler behind per-user table layouts.

No DB, no NiceGUI: ``effective_columns`` takes the columns the code ships today
plus whatever blob the user saved last year, and decides what to paint. Each test
below names the rule it pins from ``theme/tables/preferences.py``.
"""

import copy

from theme.tables.preferences import (
    TABLE_KEYS,
    ColumnPlan,
    TableKeys,
    effective_columns,
)

DEFAULTS = [
    {'name': 'username', 'label': 'Username', 'field': 'username'},
    {'name': 'pronouns', 'label': 'Pronouns', 'field': 'pronouns'},
    {'name': 'roles', 'label': 'Roles', 'field': 'roles'},
    {'name': 'actions', 'label': '', 'field': 'actions'},
]


def names(plan: ColumnPlan) -> list[str]:
    return [c['name'] for c in plan.columns]


class TestDefaults:
    def test_no_saved_config_returns_defaults_unchanged(self):
        plan = effective_columns(DEFAULTS, None)
        assert names(plan) == ['username', 'pronouns', 'roles', 'actions']
        assert plan.visible == ['username', 'pronouns', 'roles', 'actions']
        assert plan.is_customized is False

    def test_an_empty_saved_config_is_the_same_as_none(self):
        assert effective_columns(DEFAULTS, {}).is_customized is False

    def test_a_hidden_default_stays_hidden(self):
        defaults = [{'name': 'id', 'label': 'ID', 'hidden': True}, *DEFAULTS]
        plan = effective_columns(defaults, None)
        assert 'id' not in plan.visible


class TestReconciliation:
    def test_saved_order_is_honoured(self):
        saved = {'columns': [
            {'name': 'roles', 'visible': True},
            {'name': 'username', 'visible': True},
            {'name': 'pronouns', 'visible': False},
            {'name': 'actions', 'visible': True},
        ]}
        plan = effective_columns(DEFAULTS, saved)
        assert names(plan) == ['roles', 'username', 'pronouns', 'actions']
        assert plan.visible == ['roles', 'username', 'actions']
        assert plan.is_customized is True

    def test_a_saved_column_that_no_longer_exists_is_dropped(self):
        saved = {'columns': [
            {'name': 'deleted_last_year', 'visible': True},
            {'name': 'username', 'visible': True},
        ]}
        plan = effective_columns(DEFAULTS, saved)
        assert 'deleted_last_year' not in names(plan)
        assert names(plan) == ['username', 'pronouns', 'roles', 'actions']

    def test_a_new_default_column_appears_for_a_user_who_saved_earlier(self):
        # The rule that ages: a column added after someone saved must not be
        # invisible to them forever.
        saved = {'columns': [
            {'name': 'username', 'visible': True},
            {'name': 'actions', 'visible': True},
            {'name': 'pronouns', 'visible': False},
        ]}
        plan = effective_columns(DEFAULTS, saved)
        assert names(plan)[-1] == 'roles'
        assert 'roles' in plan.visible

    def test_a_required_column_cannot_be_hidden(self):
        saved = {'columns': [
            {'name': 'username', 'visible': True},
            {'name': 'actions', 'visible': False},
        ]}
        plan = effective_columns(DEFAULTS, saved)
        assert 'actions' in plan.visible

    def test_hiding_everything_falls_back_to_defaults(self):
        saved = {'columns': [
            {'name': 'username', 'visible': False},
            {'name': 'pronouns', 'visible': False},
            {'name': 'roles', 'visible': False},
            {'name': 'actions', 'visible': False},
        ]}
        plan = effective_columns(DEFAULTS, saved)
        assert plan.visible == ['username', 'pronouns', 'roles', 'actions']
        assert plan.is_customized is False

    def test_a_duplicate_saved_name_is_used_once(self):
        saved = {'columns': [
            {'name': 'roles', 'visible': True},
            {'name': 'roles', 'visible': False},
        ]}
        plan = effective_columns(DEFAULTS, saved)
        assert names(plan).count('roles') == 1
        assert 'roles' in plan.visible


class TestWidths:
    def test_widths_become_style_and_header_style(self):
        saved = {'columns': [{'name': 'username', 'visible': True, 'width': 260}]}
        plan = effective_columns(DEFAULTS, saved)
        username = next(c for c in plan.columns if c['name'] == 'username')
        assert 'width: 260px' in username['style']
        assert 'width: 260px' in username['headerStyle']
        assert plan.widths == {'username': 260}
        assert plan.has_widths is True

    def test_a_plan_without_widths_reports_none(self):
        assert effective_columns(DEFAULTS, None).has_widths is False

    def test_an_existing_style_is_preserved(self):
        defaults = [dict(DEFAULTS[0], style='text-align: right'), *DEFAULTS[1:]]
        saved = {'columns': [{'name': 'username', 'visible': True, 'width': 120}]}
        plan = effective_columns(defaults, saved)
        username = next(c for c in plan.columns if c['name'] == 'username')
        assert 'text-align: right' in username['style']
        assert 'width: 120px' in username['style']

    def test_default_column_dicts_are_not_mutated(self):
        before = copy.deepcopy(DEFAULTS)
        saved = {'columns': [{'name': 'username', 'visible': False, 'width': 300},
                             {'name': 'roles', 'visible': True}]}
        effective_columns(DEFAULTS, saved)
        assert DEFAULTS == before


class TestScalarPreferences:
    def test_shipped_values_are_the_fallback(self):
        plan = effective_columns(DEFAULTS, None, page_size=25, density='compact', wrap=True)
        assert (plan.page_size, plan.density, plan.wrap) == (25, 'compact', True)

    def test_stored_values_win(self):
        saved = {'page_size': 50, 'density': 'compact', 'wrap': True}
        plan = effective_columns(DEFAULTS, saved, page_size=0)
        assert (plan.page_size, plan.density, plan.wrap) == (50, 'compact', True)
        assert plan.is_customized is True

    def test_storing_the_shipped_value_is_not_a_customization(self):
        plan = effective_columns(DEFAULTS, {'page_size': 25}, page_size=25)
        assert plan.is_customized is False


class TestKeys:
    def test_every_declared_key_is_namespaced(self):
        assert all('.' in key for key in TABLE_KEYS)

    def test_keys_are_unique(self):
        declared = [v for k, v in vars(TableKeys).items()
                    if not k.startswith('_') and isinstance(v, str)]
        assert len(declared) == len(set(declared))

    def test_a_footer_page_size_alone_does_not_light_the_gear(self):
        # Rows per page is the footer's, not the gear's; the gear's dot only
        # tracks what the gear itself can change.
        plan = effective_columns(DEFAULTS, {'page_size': 100}, page_size=25)
        assert plan.page_size == 100
        assert plan.is_customized is False
