"""DiscordRoleRule / DiscordRoleGrant / DiscordRoleSyncState stay inside their community."""

from application.repositories import DiscordRoleSyncRepository
from application.tenant_context import tenant_scope
from models import DiscordRoleGrant, DiscordRoleRule, DiscordRoleRuleKind, DiscordRoleSyncState
from tests.factories import make_user


async def test_rules_grants_and_state_do_not_leak_between_tenants(two_tenants):
    a, b = two_tenants
    repo = DiscordRoleSyncRepository()
    member = await make_user(discord_id=850, username='leak_role_sync')

    with tenant_scope(a.id):
        await repo.upsert_rule('any_tournament', DiscordRoleRuleKind.ANY_TOURNAMENT, 9001, 'Competitor')
        await repo.add_grant(member.id, 9001)
        await repo.save_state(in_place=1)

    with tenant_scope(b.id):
        assert await repo.list_rules() == []
        assert await repo.get_rule('any_tournament') is None
        assert await repo.rule_role_ids() == set()
        assert await repo.list_grants() == []
        assert await repo.get_state() is None
        assert await repo.delete_all_rules() == 0
        await repo.delete_grant(member.id, 9001)

    with tenant_scope(a.id):
        assert [r.discord_role_id for r in await repo.list_rules()] == [9001]
        assert await repo.list_grants() == [(member.id, 9001)]
        state = await repo.get_state()
        assert state is not None and state.in_place == 1

    assert await DiscordRoleRule.filter(tenant_id=b.id).count() == 0
    assert await DiscordRoleGrant.filter(tenant_id=b.id).count() == 0
    assert await DiscordRoleSyncState.filter(tenant_id=b.id).count() == 0
