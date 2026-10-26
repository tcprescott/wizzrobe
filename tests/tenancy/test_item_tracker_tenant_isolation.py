"""Cross-tenant isolation for ALTTP item tracker boards and their change logs.

A board's overlay token is a bearer credential, so it must resolve only inside
the community that owns it, and a board id from one community must find nothing
in another.
"""

from datetime import datetime, timezone

from application.repositories.item_tracker_repository import ItemTrackerRepository
from application.tenant_context import tenant_scope
from models import ItemTrackerBoard, ItemTrackerChange, Match, Tournament, User


async def _board(name: str, runner: User) -> ItemTrackerBoard:
    t = await Tournament.create(name=name, seed_generator='alttpr')
    m = await Match.create(tournament=t, scheduled_at=datetime.now(timezone.utc))
    return await ItemTrackerBoard.create(match=m, runner=runner, overlay_token=f'token-{name}')


async def test_board_and_change_reads_are_isolated(two_tenants):
    a, b = two_tenants
    runner = await User.create(discord_id=1, username='runner')
    when = datetime.now(timezone.utc)
    repo = ItemTrackerRepository()

    with tenant_scope(a.id):
        ba = await _board('A', runner)
        ca = await repo.add_change(ba.id, None, 'bow', 0, 1, when)
    with tenant_scope(b.id):
        bb = await _board('B', runner)
        cb = await ItemTrackerChange.create(board=bb, key='bow', before=0, after=1, created_at=when)

    with tenant_scope(a.id):
        assert await repo.get_by_token('token-B') is None
        assert await repo.get_by_id(bb.id) is None
        assert await repo.list_for_match(bb.match_id) == []
        assert await repo.get_for_runner(bb.match_id, runner.id) is None
        assert await repo.get_change(bb.id, cb.id) is None
        assert await repo.recent_changes(bb.id, 10) == []
        assert [c.id for c in await repo.recent_changes(ba.id, 10)] == [ca.id]
    with tenant_scope(b.id):
        assert (await repo.get_by_token('token-B')).id == bb.id
        assert await repo.get_by_token('token-A') is None
        assert await repo.get_change(ba.id, ca.id) is None


async def test_clearing_a_log_stays_in_its_own_tenant(two_tenants):
    a, b = two_tenants
    runner = await User.create(discord_id=1, username='runner')
    when = datetime.now(timezone.utc)
    repo = ItemTrackerRepository()
    with tenant_scope(a.id):
        ba = await _board('A', runner)
        await repo.add_change(ba.id, None, 'bow', 0, 1, when)
    with tenant_scope(b.id):
        await repo.clear_changes(ba.id)
    with tenant_scope(a.id):
        assert len(await repo.recent_changes(ba.id, 10)) == 1
