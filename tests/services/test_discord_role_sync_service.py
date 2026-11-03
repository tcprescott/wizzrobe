"""DiscordRoleSyncService: the Discord roles Wizzrobe hands out.

Runs on the real (SQLite) schema with a scripted Discord standing in for the
bot, so each test can say what the server holds, what the bot may do, and
whether Discord answers at all.
"""

from datetime import datetime, timedelta, timezone
from typing import ClassVar

import pytest

from application.errors import FeatureDisabledError
from application.services.discord import discord_role_sync_service as drss
from application.services.discord.discord_role_sync_service import (
    DiscordRoleSyncService,
    role_problem,
    take_pending,
)
from application.services.feature_flag_service import reset_flag_cache
from application.tenant_context import tenant_scope
from models import (
    AuditLog,
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEvent,
    CheckInEventStatus,
    CheckInPass,
    CheckInTier,
    CheckInTierLanyard,
    DiscordRoleGrant,
    DiscordRoleMapping,
    DiscordRoleRule,
    DiscordRoleRuleKind,
    DiscordRoleSyncState,
    FeatureFlag,
    Match,
    MatchPlayers,
    Role,
    Tenant,
    TenantFeatureFlag,
    Tournament,
    TournamentPlayers,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user

GUILD = 9000
BOT_TOP = 10
COMPETITOR = 501
NOW_PLAYING = 502
VIP = 503
ALTTPR = 504
ABOVE_BOT = 505
BOOSTER = 506


class FakeDiscord:
    """The guild-ops surface the sync uses, backed by dicts a test can change."""

    def __init__(self):
        self.roles = [
            {'id': COMPETITOR, 'name': 'Competitor', 'color': 0, 'mappable': True, 'position': 5},
            {'id': NOW_PLAYING, 'name': 'Now Playing', 'color': 0, 'mappable': True, 'position': 4},
            {'id': VIP, 'name': 'SGL VIP', 'color': 0, 'mappable': True, 'position': 3},
            {'id': ALTTPR, 'name': 'ALTTPR', 'color': 0, 'mappable': True, 'position': 2},
            {'id': ABOVE_BOT, 'name': 'Admins', 'color': 0, 'mappable': True, 'position': 11},
            {'id': BOOSTER, 'name': 'Server Booster', 'color': 0, 'mappable': False, 'position': 1},
        ]
        self.access = {'manage_roles': True, 'top_position': BOT_TOP}
        self.members: dict[int, set[int]] = {}
        self.down = False
        self.refuse: set[int] = set()
        self.writes: list[tuple[str, int, int]] = []

    async def list_guild_roles(self, guild_id):
        if self.down:
            return False, 'Discord bot is not connected. Please try again in a moment.'
        return True, [dict(r) for r in self.roles]

    async def bot_role_access(self, guild_id):
        return True, dict(self.access)

    async def member_role_snapshot(self, guild_id, user_ids):
        return True, {uid: frozenset(self.members[uid]) for uid in user_ids if uid in self.members}

    async def add_role_to_user(self, guild_id, user_id, role_id, reason=None):
        if self.down:
            return False, 'Discord bot is not connected. Please try again in a moment.'
        if role_id in self.refuse:
            return False, 'Bot lacks permissions or role hierarchy prevents this action'
        self.members[user_id].add(role_id)
        self.writes.append(('add', user_id, role_id))
        return True, 'Role added to user'

    async def remove_role_from_user(self, guild_id, user_id, role_id, reason=None):
        if self.down:
            return False, 'Discord bot is not connected. Please try again in a moment.'
        self.members[user_id].discard(role_id)
        self.writes.append(('remove', user_id, role_id))
        return True, 'Role removed from user'


@pytest.fixture
async def discord(db, monkeypatch):
    fake = FakeDiscord()
    monkeypatch.setattr(drss, 'DiscordService', lambda: fake)
    await Tenant.filter(id=DEFAULT_TEST_TENANT_ID).update(discord_guild_id=GUILD)
    take_pending()
    return fake


@pytest.fixture
async def staff(db):
    user = await make_user(discord_id=1, username='staff')
    await UserRole.create(user=user, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
    return user


@pytest.fixture
def service():
    return DiscordRoleSyncService()


async def player(discord, discord_id, *, in_server=True, held=()):
    user = await make_user(discord_id=discord_id, username=f'p{discord_id}')
    if in_server:
        discord.members[discord_id] = set(held)
    return user


async def enroll(user, tournament):
    await TournamentPlayers.create(tournament=tournament, user=user)


class TestRoleProblem:
    access: ClassVar[dict] = {'manage_roles': True, 'top_position': 10}

    def test_assignable_role_has_no_problem(self):
        assert role_problem({'mappable': True, 'position': 3}, self.access) is None

    def test_role_at_or_above_bot_is_refused(self):
        assert 'above the bot' in role_problem({'mappable': True, 'position': 10}, self.access)
        assert 'above the bot' in role_problem({'mappable': True, 'position': 12}, self.access)

    def test_missing_manage_roles_is_refused_first(self):
        problem = role_problem({'mappable': True, 'position': 12}, {'manage_roles': False, 'top_position': 10})
        assert 'Manage Roles' in problem

    def test_managed_and_missing_roles_are_refused(self):
        assert 'Discord manages' in role_problem({'mappable': False, 'position': 1}, self.access)
        assert 'no longer exists' in role_problem(None, self.access)


class TestSetRule:
    async def test_saves_rule_audits_and_asks_for_a_sync(self, discord, staff, service):
        rule = await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        assert rule.key == 'any_tournament'
        assert rule.discord_role_name == 'Competitor'
        assert await AuditLog.filter(action='discord_role.rule_set').count() == 1
        assert DEFAULT_TEST_TENANT_ID in take_pending()

    async def test_blocks_role_above_the_bot(self, discord, staff, service):
        with pytest.raises(ValueError, match='above the bot'):
            await service.set_rule(staff, DiscordRoleRuleKind.LIVE_MATCH, ABOVE_BOT)
        assert await DiscordRoleRule.all().count() == 0

    async def test_blocks_when_bot_lacks_manage_roles(self, discord, staff, service):
        discord.access['manage_roles'] = False
        with pytest.raises(ValueError, match='Manage Roles'):
            await service.set_rule(staff, DiscordRoleRuleKind.LIVE_MATCH, NOW_PLAYING)

    async def test_blocks_managed_role(self, discord, staff, service):
        with pytest.raises(ValueError, match='Discord manages'):
            await service.set_rule(staff, DiscordRoleRuleKind.LIVE_MATCH, BOOSTER)

    async def test_blocks_role_used_by_role_mappings(self, discord, staff, service):
        await DiscordRoleMapping.create(
            guild_id=GUILD, discord_role_id=COMPETITOR, discord_role_name='Competitor', app_role=Role.PROCTOR,
        )
        with pytest.raises(ValueError, match='Role Mappings'):
            await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)

    async def test_discord_down_refuses_to_save(self, discord, staff, service):
        discord.down = True
        with pytest.raises(ValueError, match="Couldn't check"):
            await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)

    async def test_clearing_deletes_the_rule(self, discord, staff, service):
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        assert await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, None) is None
        assert await DiscordRoleRule.all().count() == 0
        assert await AuditLog.filter(action='discord_role.rule_cleared').count() == 1

    async def test_non_staff_is_refused(self, discord, service):
        nobody = await make_user(discord_id=77)
        with pytest.raises(PermissionError):
            await service.set_rule(nobody, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)

    async def test_flag_off_refuses(self, discord, staff, service):
        await TenantFeatureFlag.filter(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.DISCORD_ROLE_SYNC.value,
        ).update(enabled=False)
        with pytest.raises(FeatureDisabledError):
            await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)

    async def test_role_mapping_refuses_a_role_wizzrobe_gives(self, discord, staff, service):
        from application.services.discord.discord_role_mapping_service import DiscordRoleMappingService

        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        with pytest.raises(ValueError, match='already gives this Discord role'):
            await DiscordRoleMappingService().add_mapping(
                GUILD, COMPETITOR, 'Competitor', staff, app_role=Role.PROCTOR,
            )


class TestSync:
    async def test_gives_roles_to_qualifying_players(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        alice = await player(discord, 101)
        bob = await player(discord, 102)
        await enroll(alice, cup)
        await enroll(bob, cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.set_rule(staff, DiscordRoleRuleKind.TOURNAMENT, ALTTPR, tournament_id=cup.id)

        result = await service.sync_now(staff)

        assert result.added == 4
        assert discord.members[101] == {COMPETITOR, ALTTPR}
        assert discord.members[102] == {COMPETITOR, ALTTPR}
        assert await DiscordRoleGrant.all().count() == 4
        state = await DiscordRoleSyncState.get(tenant_id=DEFAULT_TEST_TENANT_ID)
        assert state.in_place == 4 and state.last_error is None
        rule = await DiscordRoleRule.get(key='any_tournament')
        assert rule.holding == 2 and rule.problem is None

    async def test_inactive_tournament_does_not_count_as_playing(self, discord, staff, service):
        old = await Tournament.create(name='Last year', is_active=False)
        await enroll(await player(discord, 101), old)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.sync_now(staff)
        assert discord.members[101] == set()

    async def test_second_run_is_a_no_op(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        await enroll(await player(discord, 101), cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.sync_now(staff)
        discord.writes.clear()
        result = await service.sync_now(staff)
        assert discord.writes == [] and result.added == result.removed == 0

    async def test_withdrawn_player_loses_only_the_role_we_gave(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        alice = await player(discord, 101)
        bob = await player(discord, 102, held={COMPETITOR})
        await enroll(alice, cup)
        await enroll(bob, cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.sync_now(staff)
        assert await DiscordRoleGrant.filter(user=bob).count() == 0

        await TournamentPlayers.all().delete()
        result = await service.sync_now(staff)

        assert result.removed == 1
        assert discord.members[101] == set()
        assert discord.members[102] == {COMPETITOR}
        assert await DiscordRoleGrant.all().count() == 0

    async def test_clearing_a_rule_takes_its_roles_back(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        await enroll(await player(discord, 101), cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.sync_now(staff)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, None)
        await service.sync_now(staff)
        assert discord.members[101] == set()

    async def test_clear_all_removes_everything_we_gave(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        await enroll(await player(discord, 101), cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.set_rule(staff, DiscordRoleRuleKind.TOURNAMENT, ALTTPR, tournament_id=cup.id)
        await service.sync_now(staff)
        result = await service.clear_all(staff)
        assert result.removed == 2
        assert discord.members[101] == set()
        assert await DiscordRoleRule.all().count() == 0

    async def test_live_match_role_follows_start_and_finish(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        alice = await player(discord, 101)
        match = await Match.create(tournament=cup, started_at=datetime.now(timezone.utc))
        await MatchPlayers.create(match=match, user=alice)
        await service.set_rule(staff, DiscordRoleRuleKind.LIVE_MATCH, NOW_PLAYING)
        await service.sync_now(staff)
        assert discord.members[101] == {NOW_PLAYING}

        await Match.filter(id=match.id).update(finished_at=datetime.now(timezone.utc))
        await service.sync_now(staff)
        assert discord.members[101] == set()

    async def test_abandoned_live_match_does_not_count(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        alice = await player(discord, 101)
        stale = await Match.create(tournament=cup, started_at=datetime.now(timezone.utc) - timedelta(days=2))
        await MatchPlayers.create(match=stale, user=alice)
        await service.set_rule(staff, DiscordRoleRuleKind.LIVE_MATCH, NOW_PLAYING)
        await service.sync_now(staff)
        assert discord.members[101] == set()

    async def test_badge_role_given_on_purchase_and_taken_on_refund(self, discord, staff, service):
        alice = await player(discord, 101)
        event = await CheckInEvent.create(name='SGL', status=CheckInEventStatus.OPEN)
        tier = await CheckInTier.create(event=event, matcherino_pass_id=1, title='VIP', lanyard=CheckInTierLanyard.VIP)
        entrant = await CheckInEntrant.create(
            event=event, source=CheckInEntrantSource.MATCHERINO, matcherino_user_id='m1',
            display_name='Alice', user=alice,
        )
        badge = await CheckInPass.create(
            event=event, tier=tier, entrant=entrant, matcherino_purchase_id=1,
            buyer_matcherino_user_id='m1', code='ABC',
        )
        await service.set_rule(staff, DiscordRoleRuleKind.BADGE, VIP, lanyard=CheckInTierLanyard.VIP)
        await service.sync_now(staff)
        assert discord.members[101] == {VIP}

        await CheckInPass.filter(id=badge.id).update(refunded_at=datetime.now(timezone.utc))
        await service.sync_now(staff)
        assert discord.members[101] == set()

    async def test_unreachable_people_are_listed(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        await enroll(await player(discord, 101, in_server=False), cup)
        nolink = await make_user(discord_id=None, username='nolink')
        await enroll(nolink, cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        result = await service.sync_now(staff)
        assert result.unreachable == 2
        state = await DiscordRoleSyncState.get(tenant_id=DEFAULT_TEST_TENANT_ID)
        assert {p['reason'] for p in state.unreachable} == {drss.NO_DISCORD, drss.NOT_IN_SERVER}

    async def test_rule_broken_later_is_marked_and_skipped(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        await enroll(await player(discord, 101), cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.sync_now(staff)
        await TournamentPlayers.all().delete()
        next(r for r in discord.roles if r['id'] == COMPETITOR)['position'] = 20
        discord.writes.clear()

        await service.sync_now(staff)

        rule = await DiscordRoleRule.get(key='any_tournament')
        assert 'above the bot' in rule.problem
        assert discord.writes == []
        assert await DiscordRoleGrant.all().count() == 1

    async def test_discord_down_changes_nothing_and_records_why(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        await enroll(await player(discord, 101), cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        discord.down = True
        result = await service.sync_now(staff)
        assert 'not connected' in result.error
        assert discord.members[101] == set()
        assert await DiscordRoleGrant.all().count() == 0
        state = await DiscordRoleSyncState.get(tenant_id=DEFAULT_TEST_TENANT_ID)
        assert 'not connected' in state.last_error

    async def test_one_refused_add_does_not_stop_the_rest(self, discord, staff, service):
        cup = await Tournament.create(name='Cup')
        await enroll(await player(discord, 101), cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.set_rule(staff, DiscordRoleRuleKind.TOURNAMENT, ALTTPR, tournament_id=cup.id)
        discord.refuse.add(ALTTPR)
        result = await service.sync_now(staff)
        assert discord.members[101] == {COMPETITOR}
        assert result.error and '1 role change failed' in result.error
        assert await DiscordRoleGrant.all().count() == 1


class TestCommunityWalls:
    async def test_rule_cannot_name_another_communitys_tournament(self, discord, staff, service, two_tenants):
        _a, tenant_b = two_tenants
        with tenant_scope(tenant_b.id):
            theirs = await Tournament.create(name='Theirs')
        with pytest.raises(ValueError):
            await service.set_rule(staff, DiscordRoleRuleKind.TOURNAMENT, ALTTPR, tournament_id=theirs.id)

    async def test_sync_reads_only_this_communitys_players(self, discord, staff, service, two_tenants):
        _a, tenant_b = two_tenants
        outsider = await player(discord, 101)
        with tenant_scope(tenant_b.id):
            theirs = await Tournament.create(name='Theirs')
            await enroll(outsider, theirs)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.sync_now(staff)
        assert discord.members[101] == set()

    async def test_staff_of_another_community_is_refused(self, discord, service, two_tenants):
        _a, tenant_b = two_tenants
        other = await make_user(discord_id=55, username='other')
        await UserRole.create(user=other, role=Role.STAFF, tenant_id=tenant_b.id)
        with pytest.raises(PermissionError):
            await service.set_rule(other, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)


class TestWorker:
    async def test_event_marks_community_and_tick_syncs_it(self, discord, staff, service, monkeypatch):
        from application.events import Event, EventType
        from application.services.discord import discord_role_sync_worker as worker

        cup = await Tournament.create(name='Cup')
        alice = await player(discord, 101)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await service.sync_now(staff)
        monkeypatch.setattr(worker, '_last_full', {DEFAULT_TEST_TENANT_ID: float('inf')})
        await enroll(alice, cup)

        worker._on_event(Event.create(EventType.TOURNAMENT_ENROLLED, {}))
        await worker._tick()

        assert discord.members[101] == {COMPETITOR}

    async def test_tick_skips_community_with_flag_off(self, discord, staff, service, monkeypatch):
        from application.services.discord import discord_role_sync_worker as worker

        cup = await Tournament.create(name='Cup')
        await enroll(await player(discord, 101), cup)
        await service.set_rule(staff, DiscordRoleRuleKind.ANY_TOURNAMENT, COMPETITOR)
        await TenantFeatureFlag.filter(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.DISCORD_ROLE_SYNC.value,
        ).update(enabled=False)
        reset_flag_cache()
        monkeypatch.setattr(worker, '_last_full', {})

        await worker._tick()

        assert discord.members[101] == set()
