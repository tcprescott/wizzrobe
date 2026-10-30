"""Cross-tenant isolation for the PR 8 Discord Events mirror.

The sharpest edge of this feature: since a Discord guild can back several tenants
(``discord_guild_id`` is not unique), the reconciler must scope every
create/update/**cancel** to its own ``DiscordScheduledEvent`` rows and never
cancel a sibling tenant's event that happens to live in the same guild.

Covers the repository-level read isolation and the end-to-end regression the PR's
acceptance criteria call out: two tenants share one guild, each mirrors its own
matches, and cancelling a match in tenant A removes only A's event.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.repositories import DiscordScheduledEventRepository
from application.services.discord.discord_event_reconciler_service import DiscordEventReconcilerService
from application.services.discord.discord_service import MockDiscordService
from application.tenant_context import tenant_scope
from application.utils.mocks import mock_discord_data
from models import (
    DiscordEventSource,
    DiscordScheduledEvent,
    Match,
    MatchPlayers,
    Tournament,
    User,
)

SHARED_GUILD = 1000000000000000009


def _reconciler() -> DiscordEventReconcilerService:
    svc = DiscordEventReconcilerService()
    svc.discord = MockDiscordService()
    return svc


@pytest.fixture
async def shared_guild_tenants(two_tenants):
    """The canonical two tenants, both sharing one Discord guild (guild → many tenants)."""
    a, b = two_tenants
    for tenant in (a, b):
        tenant.discord_guild_id = SHARED_GUILD
        await tenant.save()
    return a, b


async def test_scheduled_event_reads_are_isolated(shared_guild_tenants):
    a, b = shared_guild_tenants
    repo = DiscordScheduledEventRepository()
    with tenant_scope(a.id):
        ra = await DiscordScheduledEvent.create(
            guild_id=SHARED_GUILD, discord_event_id=1001,
            source_type=DiscordEventSource.MATCH, source_id=5, title='A',
        )
    with tenant_scope(b.id):
        rb = await DiscordScheduledEvent.create(
            guild_id=SHARED_GUILD, discord_event_id=1002,
            source_type=DiscordEventSource.MATCH, source_id=5, title='B',
        )

    with tenant_scope(a.id):
        assert [x.id for x in await repo.list_all()] == [ra.id]
        assert await repo.get_by_id(rb.id) is None
        assert (await repo.get_by_source(DiscordEventSource.MATCH, 5)).id == ra.id
    with tenant_scope(b.id):
        assert [x.id for x in await repo.list_all()] == [rb.id]
        assert (await repo.get_by_source(DiscordEventSource.MATCH, 5)).id == rb.id


async def _enabled_match(tenant, *, name):
    with tenant_scope(tenant.id):
        tourn = await Tournament.create(name=name, discord_events_enabled=True)
        when = datetime.now(timezone.utc) + timedelta(days=1)
        match = await Match.create(tournament=tourn, scheduled_at=when, title=f'{name} R1')
        user = await User.create(discord_id=None, username=f'{name}p', display_name=f'{name} Player',
                                 is_placeholder=True, speedgaming_id=f'sg_{name}')
        await MatchPlayers.create(match=match, user=user)
    return tourn, match


async def test_shared_guild_cancel_only_touches_own_event(shared_guild_tenants):
    """Cancelling A's match removes only A's Discord event; B's survives."""
    mock_discord_data.reset_scheduled_events()
    a, b = shared_guild_tenants
    actor = await User.create(discord_id=1, username='sys', is_system=True)
    _, match_a = await _enabled_match(a, name='A')
    _, match_b = await _enabled_match(b, name='B')
    reconciler = _reconciler()

    with tenant_scope(a.id):
        ra = await reconciler.reconcile_tenant(a, actor=actor)
    with tenant_scope(b.id):
        rb = await reconciler.reconcile_tenant(b, actor=actor)
    assert ra.created == 1 and rb.created == 1
    assert len(mock_discord_data.scheduled_events_for(SHARED_GUILD)) == 2

    match_a.finished_at = datetime.now(timezone.utc)
    await match_a.save()
    with tenant_scope(a.id):
        ra2 = await reconciler.reconcile_tenant(a, actor=actor)
    assert ra2.cancelled == 1

    with tenant_scope(b.id):
        assert await DiscordScheduledEvent.filter(source_id=match_b.id).count() == 1
    with tenant_scope(a.id):
        assert await DiscordScheduledEvent.filter(source_id=match_a.id).count() == 0
    remaining = mock_discord_data.scheduled_events_for(SHARED_GUILD)
    assert len(remaining) == 1
    b_event_id = (await DiscordScheduledEvent.filter(source_id=match_b.id, tenant_id=b.id).first()).discord_event_id
    assert remaining[0]['id'] == b_event_id
