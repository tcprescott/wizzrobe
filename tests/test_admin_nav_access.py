"""Who gets the Admin link, and what it opens for them.

The link (``AuthService.can_open_admin``) must agree with the drawer
``build_admin_tabs`` builds: offered exactly when there is a tab to land on.
The online-play roles (PRESET_MANAGER, SYNC_ADMIN, QUALIFIER_ADMIN) used to get
no link at all because the nav asked ``can_view_admin``, which deliberately
leaves them out of the admin API, the reports and staff help; that stays true.
A volunteer coordinator reaches Reports for Volunteer Coverage and nothing else.
"""

import pytest

from application.services import HelpService
from application.services.auth_service import AuthService
from application.tenant_context import tenant_scope
from models import FeatureFlag, Role, Tenant, TenantFeatureFlag, User, UserRole
from pages.admin import AdminAccess, build_admin_tabs
from pages.admin_tabs import reports as reports_pkg
from pages.admin_tabs.reports import VOLUNTEER_REPORTS

ALL_FLAGS = set(FeatureFlag)

_ACCESS_FIELD = {
    Role.STAFF: 'is_staff',
    Role.STREAM_MANAGER: 'is_stream_manager',
    Role.VOLUNTEER_COORDINATOR: 'is_volunteer_coordinator',
    Role.EQUIPMENT_MANAGER: 'is_equipment_manager',
    Role.PRESET_MANAGER: 'is_preset_manager',
    Role.SYNC_ADMIN: 'is_sync_admin',
    Role.QUALIFIER_ADMIN: 'is_qualifier_admin',
}

ONLINE_ROLES = (Role.PRESET_MANAGER, Role.SYNC_ADMIN, Role.QUALIFIER_ADMIN)


def _labels(role: Role, live: set) -> list[str]:
    access = AdminAccess(**{_ACCESS_FIELD[role]: True})
    return [t['label'] for t in build_admin_tabs(access, live, {}, None)]


async def _user_with(role: Role | None, discord_id: int, tenant_id: int) -> User:
    user = await User.create(discord_id=discord_id, username=f'u{discord_id}')
    if role is not None:
        await UserRole.create(user=user, role=role, tenant_id=tenant_id)
    return user


@pytest.fixture
async def flagless(db):
    """A community with every feature off (no flag rows)."""
    return await Tenant.create(name='Flagless', slug='flagless')


@pytest.fixture
async def all_on(db):
    """A second community with every feature on."""
    tenant = await Tenant.create(name='All on', slug='all-on')
    for flag in FeatureFlag:
        await TenantFeatureFlag.create(
            tenant_id=tenant.id, flag=flag.value, available=True, enabled=True,
        )
    return tenant


class TestTheLinkMatchesTheDrawer:
    @pytest.mark.parametrize('role', list(_ACCESS_FIELD))
    async def test_with_every_feature_on(self, all_on, role):
        with tenant_scope(all_on.id):
            user = await _user_with(role, 7000 + list(_ACCESS_FIELD).index(role), all_on.id)
            assert await AuthService.can_open_admin(user) is bool(_labels(role, ALL_FLAGS))
            assert await AuthService.can_open_admin(user) is True

    @pytest.mark.parametrize('role', list(_ACCESS_FIELD))
    async def test_with_every_feature_off(self, flagless, role):
        with tenant_scope(flagless.id):
            user = await _user_with(role, 7100 + list(_ACCESS_FIELD).index(role), flagless.id)
            assert await AuthService.can_open_admin(user) is bool(_labels(role, set()))

    async def test_feature_bound_roles_lose_the_link_with_their_feature(self, flagless):
        with tenant_scope(flagless.id):
            for i, role in enumerate((Role.QUALIFIER_ADMIN, Role.VOLUNTEER_COORDINATOR,
                                      Role.EQUIPMENT_MANAGER)):
                user = await _user_with(role, 7200 + i, flagless.id)
                assert await AuthService.can_open_admin(user) is False

    async def test_a_member_with_no_admin_role_gets_no_link(self, all_on):
        with tenant_scope(all_on.id):
            for i, role in enumerate((None, Role.PROCTOR, Role.VOLUNTEER, Role.CHECK_IN_DESK)):
                user = await _user_with(role, 7300 + i, all_on.id)
                assert await AuthService.can_open_admin(user) is False
        assert await AuthService.can_open_admin(None) is False

    async def test_a_role_in_another_community_grants_no_link(self, all_on):
        user = await _user_with(Role.PRESET_MANAGER, 7400, all_on.id)
        assert await AuthService.can_open_admin(user) is False


class TestOnlinePlayRolesStayNarrow:
    @pytest.mark.parametrize('role', ONLINE_ROLES)
    async def test_they_still_are_not_admin_api_or_staff_help_readers(self, all_on, role):
        with tenant_scope(all_on.id):
            user = await _user_with(role, 7500 + ONLINE_ROLES.index(role), all_on.id)
            assert await AuthService.can_view_admin(user) is False

    def test_a_preset_manager_sees_presets_and_settings_only(self):
        assert _labels(Role.PRESET_MANAGER, ALL_FLAGS) == ['Seed Presets', 'Settings']

    def test_a_sync_admin_sees_the_sync_surfaces_only(self):
        assert _labels(Role.SYNC_ADMIN, ALL_FLAGS) == ['SpeedGaming', 'Racetime', 'Settings']

    def test_a_qualifier_admin_sees_qualifiers_only(self):
        assert _labels(Role.QUALIFIER_ADMIN, ALL_FLAGS) == ['Qualifiers']
        assert _labels(Role.QUALIFIER_ADMIN, set()) == []

    @pytest.mark.parametrize('role', ONLINE_ROLES)
    def test_none_of_them_sees_the_staff_tabs(self, role):
        labels = set(_labels(role, ALL_FLAGS))
        assert not labels & {'Match Schedule', 'Members', 'Tournaments', 'Reports',
                             'Service Health', 'Payouts', 'Check-in Roster'}


class TestVolunteerCoordinatorReports:
    def _reports_tab(self, access: AdminAccess, live: set) -> dict | None:
        tabs = build_admin_tabs(access, live, {'report': None}, None)
        return next((t for t in tabs if t['label'] == 'Reports'), None)

    def test_a_coordinator_gets_reports_limited_to_volunteer_coverage(self):
        tab = self._reports_tab(AdminAccess(is_volunteer_coordinator=True), ALL_FLAGS)
        assert tab is not None
        assert tab['content'][2]['only'] == VOLUNTEER_REPORTS

    def test_no_reports_tab_without_volunteers(self):
        live = ALL_FLAGS - {FeatureFlag.VOLUNTEERS}
        assert self._reports_tab(AdminAccess(is_volunteer_coordinator=True), live) is None

    def test_staff_reports_are_not_limited(self):
        access = AdminAccess(is_staff=True, is_volunteer_coordinator=True)
        assert 'only' not in self._reports_tab(access, ALL_FLAGS)['content'][2]

    async def test_a_limited_viewer_is_sent_to_their_report_whatever_they_ask_for(self, monkeypatch):
        calls = []

        async def volunteers(**params):
            calls.append(('volunteers', params))

        async def dashboard(**params):
            calls.append(('dashboard', params))

        monkeypatch.setitem(reports_pkg._REPORT_HANDLERS, 'volunteers', volunteers)
        monkeypatch.setattr(reports_pkg, 'dashboard_page', dashboard)
        monkeypatch.setattr(reports_pkg, '_track_report_view', lambda report: None)

        for asked in (None, 'audit', 'crew', 'volunteers'):
            await reports_pkg._render_report(asked, {}, ALL_FLAGS, VOLUNTEER_REPORTS)
        assert [c[0] for c in calls] == ['volunteers'] * 4
        assert all(c[1]['all_reports_link'] is False for c in calls)

        calls.clear()
        await reports_pkg._render_report('volunteers', {}, set(), VOLUNTEER_REPORTS)
        assert calls == []


class TestPresetHelp:
    async def test_a_preset_manager_reads_the_presets_article(self, db):
        user = await _user_with(Role.PRESET_MANAGER, 7600, 1)
        assert await HelpService.get_article('staff-presets', user) is not None
        assert 'staff-presets' in [a.slug for a in await HelpService.list_articles(user)]

    async def test_it_stays_in_the_staff_group(self, db):
        user = await _user_with(Role.STAFF, 7601, 1)
        article = await HelpService.get_article('staff-presets', user)
        assert HelpService.for_staff(article)

    async def test_a_preset_manager_does_not_gain_other_staff_articles(self, db):
        user = await _user_with(Role.PRESET_MANAGER, 7602, 1)
        assert await HelpService.get_article('staff-schedule', user) is None

    async def test_a_player_cannot_read_it(self, db):
        user = await _user_with(None, 7603, 1)
        assert await HelpService.get_article('staff-presets', user) is None
