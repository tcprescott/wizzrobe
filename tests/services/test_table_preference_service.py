"""TablePreferenceService — validation, persistence and the global-storage decision.

``validate`` is the whole security surface (the config arrives from a browser),
so every rejection branch gets a test. The DB-backed half pins the two properties
the plan settled: preferences are keyed on ``(user, table_key)`` and are **not**
tenant-scoped.
"""

import pytest

from application.services.table_preference_service import TablePreferenceService
from application.tenant_context import tenant_scope
from models import UserTablePreference
from tests.factories import make_user

pytestmark = pytest.mark.anyio


class TestValidate:
    def test_rejects_a_non_object(self):
        with pytest.raises(ValueError):
            TablePreferenceService.validate(['columns'])

    def test_rejects_unknown_keys(self):
        with pytest.raises(ValueError, match='Unknown table preference'):
            TablePreferenceService.validate({'colours': []})

    def test_rejects_a_bad_page_size(self):
        with pytest.raises(ValueError, match='Rows per page'):
            TablePreferenceService.validate({'page_size': 37})

    def test_rejects_a_bad_density(self):
        with pytest.raises(ValueError, match='Density'):
            TablePreferenceService.validate({'density': 'cosy'})

    def test_rejects_a_non_boolean_wrap(self):
        with pytest.raises(ValueError, match='Wrap'):
            TablePreferenceService.validate({'wrap': 'yes'})

    def test_rejects_columns_that_are_not_a_list(self):
        with pytest.raises(ValueError, match='Columns must be a list'):
            TablePreferenceService.validate({'columns': {'name': 'a'}})

    def test_rejects_an_oversized_column_list(self):
        columns = [{'name': f'c{i}'} for i in range(TablePreferenceService.MAX_COLUMNS + 1)]
        with pytest.raises(ValueError, match='more than'):
            TablePreferenceService.validate({'columns': columns})

    def test_rejects_a_nameless_column(self):
        with pytest.raises(ValueError, match='needs a name'):
            TablePreferenceService.validate({'columns': [{'visible': True}]})

    def test_rejects_an_overlong_column_name(self):
        long_name = 'x' * (TablePreferenceService.MAX_NAME_LEN + 1)
        with pytest.raises(ValueError, match='may not exceed'):
            TablePreferenceService.validate({'columns': [{'name': long_name}]})

    def test_rejects_duplicate_column_names(self):
        with pytest.raises(ValueError, match='listed twice'):
            TablePreferenceService.validate(
                {'columns': [{'name': 'a'}, {'name': 'a'}]})

    def test_rejects_an_unknown_column_setting(self):
        with pytest.raises(ValueError, match='Unknown column setting'):
            TablePreferenceService.validate(
                {'columns': [{'name': 'a', 'colour': 'red'}]})

    def test_rejects_an_out_of_range_width(self):
        with pytest.raises(ValueError, match='between'):
            TablePreferenceService.validate(
                {'columns': [{'name': 'a', 'width': 9000}]})
        with pytest.raises(ValueError, match='between'):
            TablePreferenceService.validate(
                {'columns': [{'name': 'a', 'width': 5}]})

    def test_rejects_a_non_integer_width(self):
        with pytest.raises(ValueError, match='whole number'):
            TablePreferenceService.validate(
                {'columns': [{'name': 'a', 'width': '200'}]})

    def test_rejects_folded_that_is_not_an_object(self):
        with pytest.raises(ValueError, match='Folded groups must be an object'):
            TablePreferenceService.validate({'folded': ['Done']})

    def test_rejects_a_non_boolean_fold(self):
        with pytest.raises(ValueError, match='true or false'):
            TablePreferenceService.validate({'folded': {'Done': 'yes'}})

    def test_rejects_too_many_folded_groups(self):
        folded = {f'g{i}': True for i in range(TablePreferenceService.MAX_FOLDED + 1)}
        with pytest.raises(ValueError, match='more than'):
            TablePreferenceService.validate({'folded': folded})

    def test_rejects_an_overlong_group_name(self):
        long_name = 'x' * (TablePreferenceService.MAX_NAME_LEN + 1)
        with pytest.raises(ValueError, match='may not exceed'):
            TablePreferenceService.validate({'folded': {long_name: True}})

    def test_normalises_a_partial_config(self):
        clean = TablePreferenceService.validate(
            {'columns': [{'name': '  username  '}]})
        assert clean == {'columns': [{'name': 'username', 'visible': True, 'width': None}]}

    def test_an_empty_config_is_valid(self):
        assert TablePreferenceService.validate({}) == {}


class TestPersistence:
    async def test_save_is_upsert_idempotent(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.save(user, 'admin.users', {'page_size': 25})
        await service.save(user, 'admin.users', {'page_size': 50})
        assert await UserTablePreference.filter(user_id=user.id).count() == 1
        assert (await service.get(user, 'admin.users'))['page_size'] == 50

    async def test_get_returns_none_for_an_unsaved_table(self, db):
        user = await make_user()
        assert await TablePreferenceService().get(user, 'admin.users') is None

    async def test_reset_removes_the_row(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.save(user, 'admin.users', {'density': 'compact'})
        await service.reset(user, 'admin.users')
        assert await service.get(user, 'admin.users') is None

    async def test_reset_all_clears_every_table(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.save(user, 'admin.users', {'density': 'compact'})
        await service.save(user, 'admin.schedule', {'density': 'compact'})
        await service.reset_all(user)
        assert await service.all_for_user(user) == {}

    async def test_prime_returns_every_table_keyed_by_key(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.save(user, 'admin.users', {'page_size': 10})
        await service.save(user, 'admin.schedule', {'page_size': 50})
        primed = await service.prime(user)
        assert set(primed) == {'admin.users', 'admin.schedule'}
        assert primed['admin.schedule']['page_size'] == 50

    async def test_prime_returns_empty_for_anonymous(self):
        assert await TablePreferenceService().prime(None) == {}

    async def test_set_width_touches_one_column_only(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.save(user, 'admin.users', {'columns': [
            {'name': 'username', 'visible': True, 'width': 200},
            {'name': 'roles', 'visible': False, 'width': None},
        ]})
        config = await service.set_width(user, 'admin.users', 'roles', 300)
        by_name = {c['name']: c for c in config['columns']}
        assert by_name['username'] == {'name': 'username', 'visible': True, 'width': 200}
        assert by_name['roles']['width'] == 300
        assert by_name['roles']['visible'] is False

    async def test_set_width_none_clears_a_stored_width(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.set_width(user, 'admin.users', 'username', 250)
        config = await service.set_width(user, 'admin.users', 'username', None)
        assert config['columns'][0]['width'] is None

    async def test_set_width_rejects_an_out_of_range_value(self, db):
        user = await make_user()
        with pytest.raises(ValueError):
            await TablePreferenceService().set_width(user, 'admin.users', 'username', 9999)

    async def test_set_page_size_keeps_the_rest_of_the_layout(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.save(user, 'admin.users', {
            'columns': [{'name': 'username', 'visible': True, 'width': 200}],
            'density': 'compact', 'page_size': 25,
        })
        config = await service.set_page_size(user, 'admin.users', 100)
        assert config['page_size'] == 100
        assert config['density'] == 'compact'
        assert config['columns'][0]['width'] == 200
        assert (await service.get(user, 'admin.users'))['page_size'] == 100

    async def test_set_page_size_rejects_a_size_the_footer_does_not_offer(self, db):
        user = await make_user()
        with pytest.raises(ValueError, match='Rows per page'):
            await TablePreferenceService().set_page_size(user, 'admin.users', 15)

    async def test_set_folded_keeps_the_rest_of_the_layout(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.save(user, 'proctor.station', {'page_size': 50})
        await service.set_folded(user, 'proctor.station', 'Done', False)
        await service.set_folded(user, 'proctor.station', 'In play', True)

        assert await service.get(user, 'proctor.station') == {
            'page_size': 50, 'folded': {'Done': False, 'In play': True}}

    async def test_the_gear_save_keeps_folded_groups(self, db):
        user = await make_user()
        service = TablePreferenceService()
        await service.set_folded(user, 'proctor.station', 'In play', True)
        await service.save(user, 'proctor.station', {'density': 'compact'})

        assert await service.get(user, 'proctor.station') == {
            'density': 'compact', 'folded': {'In play': True}}

    async def test_preferences_are_not_tenant_scoped(self, two_tenants):
        tenant_a, tenant_b = two_tenants
        user = await make_user()
        service = TablePreferenceService()
        with tenant_scope(tenant_a.id):
            await service.save(user, 'admin.users', {'page_size': 50})
        with tenant_scope(tenant_b.id):
            assert (await service.get(user, 'admin.users'))['page_size'] == 50

    async def test_preferences_are_per_user(self, db):
        alice = await make_user(1, 'alice')
        bob = await make_user(2, 'bob')
        service = TablePreferenceService()
        await service.save(alice, 'admin.users', {'page_size': 50})
        assert await service.get(bob, 'admin.users') is None
