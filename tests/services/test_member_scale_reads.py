"""Whole-community reads stay cheap at the size an imported Discord server makes.

Hydrating a ``User`` sets about forty fields; on a 2,000-member community the
check-in desk's member reads held the one event loop for most of a second each.
These pin the reads that scan every member to light rows, hydrating only the
people they return.
"""

# ruff: noqa: F811 — the shared fixtures are imported from check_in_support.

import pytest

from application.repositories.user_repository import UserRepository
from application.services import TenantMembershipService, UserService
from application.services.check_in_service import SUGGESTION_LIMIT
from application.utils.clients.matcherino_client import mock_purchase
from models import Role, TenantMembership, Tournament, User, UserRole
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user
from tests.services.check_in_support import (  # noqa: F401 — fixtures
    client,
    event,
    service,
    staff,
)
from tests.services.check_in_support import (
    rows as _rows,
)
from theme.tables.user import UserTableView

MEMBERS = 40


@pytest.fixture
async def crowd(db):
    users = [
        await make_user(discord_id=1000 + i, username=f'patexample{i:02d}', display_name=f'Pat Example {i}')
        for i in range(MEMBERS)
    ]
    for user in users:
        await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
    return users


@pytest.fixture
def hydrations(monkeypatch):
    """How many ``User`` objects the code under test builds from database rows."""
    count = {'n': 0}
    original = User._init_from_db.__func__

    def counting(cls, **kwargs):
        if cls is User:
            count['n'] += 1
        return original(cls, **kwargs)

    monkeypatch.setattr(User, '_init_from_db', classmethod(counting))
    return count


class TestCheckInMemberReads:
    async def test_search_builds_only_the_users_it_returns(self, service, staff, event, crowd, hydrations):
        results = await service.search_members(event, 'pat example', limit=5)

        assert [u.username for u in results] == [f'patexample{i:02d}' for i in range(5)]
        assert hydrations['n'] <= 5 + 2  # the results, plus the event and actor reads

    async def test_suggestions_build_only_the_users_they_offer(self, service, client, staff, event, crowd, hydrations):
        client.set([mock_purchase(100, 'Pat_Example')])
        await service.sync_event(staff, event.id)
        entrant = await service.get_entrant((await _rows(event))['100'].id)
        hydrations['n'] = 0

        suggestions = await service.suggest_users(entrant)

        assert len(suggestions) == SUGGESTION_LIMIT
        assert hydrations['n'] <= SUGGESTION_LIMIT + 2

    async def test_desk_counts_build_no_users(self, service, client, staff, event, crowd, hydrations):
        client.set([mock_purchase(100, 'Pat_Example')])
        await service.sync_event(staff, event.id)
        entrants = await service.roster(event)
        hydrations['n'] = 0

        counts = await service.suggestion_counts(event, entrants)

        assert counts == {(await _rows(event))['100'].id: SUGGESTION_LIMIT}
        assert hydrations['n'] == 0


class TestPickerRows:
    async def test_summaries_are_the_same_people_as_the_full_read(self, db, crowd):
        inactive = await make_user(discord_id=5000, username='gone', is_active=False)
        outsider = await make_user(discord_id=5001, username='outsider')
        await TenantMembership.create(user=inactive, tenant_id=DEFAULT_TEST_TENANT_ID)

        for kwargs in ({}, {'include_inactive': True}, {'include_user_ids': [outsider.id]}):
            full = await UserRepository.get_community_people(**kwargs)
            light = await UserService().get_community_people_summaries(**kwargs)
            assert [(u.id, u.preferred_name, u.discord_id, u.is_active) for u in full] == \
                [(p.id, p.preferred_name, p.discord_id, p.is_active) for p in light]


class TestMembersTable:
    async def test_rows_match_the_per_user_format(self, db, crowd):
        member = crowd[0]
        await UserRole.create(user=member, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
        await UserRole.create(user=member, role=Role.SUPER_ADMIN, tenant_id=None)
        tournament = await Tournament.create(name='T', tenant_id=DEFAULT_TEST_TENANT_ID)
        await tournament.admins.add(member)
        await tournament.crew_coordinators.add(member, crowd[1])

        class Table:
            def __init__(self) -> None:
                self.rows: list = []

            def update(self) -> None:
                pass

        view = UserTableView.__new__(UserTableView)
        view.before_refresh = view.decorate_row = None
        view.table = Table()
        view._tenant_id = DEFAULT_TEST_TENANT_ID
        view.get_query = lambda: User.filter(
            tenant_memberships__tenant_id=DEFAULT_TEST_TENANT_ID).exclude(is_system=True).distinct()

        await view.refresh()

        expected = [
            UserTableView._format_user_row(u, DEFAULT_TEST_TENANT_ID)
            for u in await view.get_query().order_by('username').prefetch_related(
                'roles', 'admin_tournaments', 'crew_coordinated_tournaments')
        ]
        assert view.table.rows == expected
        by_id = {row['id']: row for row in view.table.rows}
        assert by_id[member.id]['roles'] == 'Staff, TA(1), CC(1)'
        assert by_id[crowd[1].id]['roles'] == 'CC(1)'


class TestMembershipReads:
    async def test_addable_users_are_everyone_outside_the_community(self, db, crowd):
        outsider = await make_user(discord_id=6000, username='outsider')
        system = await make_user(discord_id=6001, username='system', is_system=True)

        addable = {u.id for u in await TenantMembershipService().addable_users()}

        assert outsider.id in addable
        assert system.id not in addable
        assert not addable & {u.id for u in crowd}

    async def test_memberships_by_user_builds_no_users(self, db, crowd, hydrations):
        memberships = await TenantMembershipService().memberships_by_user()

        assert {u.id for u in crowd} <= set(memberships)
        assert hydrations['n'] == 0
