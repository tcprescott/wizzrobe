"""The qualifier fixtures the AsyncQualifierService test modules share.

``test_async_qualifier_service`` (draw, run lifecycle, review, scoring,
lockdown) and ``test_async_qualifier_availability`` (why a run cannot be
started) were one module until it approached the 800-line guideline. These four
helpers are what both need, and what a third module would otherwise copy:

``submit_run`` refuses a claim longer than the run has existed — the server
stamps ``started_at`` at the draw and measures against it — so ``submit``
backdates the draw rather than each test remembering to. The two user factories
are ``make_``-prefixed because their callers bind the result to ``staff`` and
``player``, and a bare name would be shadowed by the first assignment.
"""

from datetime import datetime, timedelta, timezone

from models import AsyncQualifierRun, Role, TenantMembership, User, UserRole


async def make_staff(discord_id: int = 900001, username: str = 'staffy') -> User:
    u = await User.create(discord_id=discord_id, username=username)
    await UserRole.create(user=u, role=Role.STAFF, tenant_id=1)
    await TenantMembership.create(user=u, tenant_id=1)
    return u


async def make_player(discord_id: int, name: str, **extra) -> User:
    u = await User.create(discord_id=discord_id, username=name, **extra)
    await TenantMembership.create(user=u, tenant_id=1)
    return u


async def open_qualifier(service, actor, *, runs_per_pool=1, allowed_reattempts=0):
    """An open qualifier with one empty pool, ready to take permalinks."""
    now = datetime.now(timezone.utc)
    q = await service.create_qualifier(
        actor, name='Q', opens_at=now - timedelta(days=1), closes_at=now + timedelta(days=1),
        runs_per_pool=runs_per_pool, allowed_reattempts=allowed_reattempts,
    )
    pool = await service.create_pool(actor, q.id, name='Pool A')
    return q, pool


async def submit(service, runner, run, seconds: int):
    """Submit ``seconds`` on ``run``, backdating the draw so the wall clock agrees."""
    await AsyncQualifierRun.filter(id=run.id).update(
        started_at=datetime.now(timezone.utc) - timedelta(seconds=seconds),
    )
    return await service.submit_run(runner, run.id, elapsed_seconds=seconds)
