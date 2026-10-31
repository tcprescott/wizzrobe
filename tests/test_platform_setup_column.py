"""``/platform``'s Setup column.

A super-admin who provisions ten communities could not tell which of them had a
staff member, a tournament or an enrolled player. ``_refresh`` builds the rows,
so it is testable with a stub table rather than a browser.
"""

from application.repositories.user_role_repository import UserRoleRepository
from application.tenant_context import tenant_scope
from models import Role, Tenant, Tournament, TournamentPlayers, User
from pages.platform import _refresh


class _StubTable:
    """Just enough of ``ui.table`` for ``_refresh``."""

    def __init__(self):
        self.rows = []

    def update(self):
        pass


async def _rows():
    table = _StubTable()
    await _refresh(table)
    return {row['slug']: row for row in table.rows}


async def test_platform_rows_report_readiness(db):
    ready = await Tenant.create(name='Ready', slug='ready')
    await Tenant.create(name='Empty', slug='empty')
    user = await User.create(discord_id=8100, username='op')
    with tenant_scope(ready.id):
        await UserRoleRepository.add(user, Role.STAFF)
        tournament = await Tournament.create(name='T')
        await TournamentPlayers.create(tournament=tournament, user=user)

    rows = await _rows()
    assert rows['ready']['setup'] == 'Ready'
    assert rows['ready']['setup_ready'] is True
    assert rows['empty']['setup'] == '0 of 3'
    assert rows['empty']['setup_ready'] is False


async def test_the_tooltip_names_what_is_outstanding(db):
    partial = await Tenant.create(name='Partial', slug='partial')
    user = await User.create(discord_id=8101, username='op2')
    with tenant_scope(partial.id):
        await UserRoleRepository.add(user, Role.STAFF)

    row = (await _rows())['partial']
    assert row['setup'] == '1 of 3'
    assert 'tournament' in row['setup_missing'].lower()
    assert 'enrol' in row['setup_missing'].lower()


async def test_readiness_counts_only_required_steps(db):
    t = await Tenant.create(name='NoStage', slug='nostage')
    user = await User.create(discord_id=8102, username='op3')
    with tenant_scope(t.id):
        await UserRoleRepository.add(user, Role.STAFF)
        tournament = await Tournament.create(name='T')
        await TournamentPlayers.create(tournament=tournament, user=user)

    row = (await _rows())['nostage']
    assert row['setup'] == 'Ready'
    assert row['setup_missing'] == 'Nothing outstanding'


async def test_the_column_does_not_disturb_the_existing_row_fields(db):
    await Tenant.create(name='Plain', slug='plain', domain='plain.example')
    row = (await _rows())['plain']
    assert row['name'] == 'Plain'
    assert row['domain'] == 'plain.example'
    assert row['guild'] == '—'
    assert row['active_bool'] is True
