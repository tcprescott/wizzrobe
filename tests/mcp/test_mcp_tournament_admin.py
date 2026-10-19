"""Tournament administration tools: create, edit, delete, staff, roster.

The connection gate for every write is covered in ``test_mcp_writes.py``; these
assert each tool reaches its service, the service's permission check holds, and
another community's tournament stays out of reach.
"""

from models import (
    MembershipSource,
    Preset,
    Role,
    Tenant,
    TenantMembership,
    Tournament,
    TournamentPlayers,
    User,
)
from tests.api_helpers import create_community_member
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session

TENANT = 'default'


async def _member_token(**kwargs):
    user, raw = await create_oauth_token(**kwargs)
    await TenantMembership.create(user=user, tenant_id=1, source=MembershipSource.STAFF)
    return user, raw


class TestEditing:
    async def test_staff_create_then_configure(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        async with mcp_session() as client:
            is_error, created = await call_tool(
                client, raw, 'create_tournament', tenant=TENANT, name='SGL Cup',
                tournament_hours={'2026-10-22': ['10:00', '22:00']}, is_test=True,
            )
            assert not is_error, created
            tid = created['id']
            assert created['is_test'] is True

            is_error, updated = await call_tool(
                client, raw, 'update_tournament', tenant=TENANT, tournament_id=tid,
                rules_url='https://example.com/rules', stage_reminder_minutes=15,
                allow_player_match_requests=False, is_test=False,
            )
            assert not is_error, updated
        tournament = await Tournament.get(id=tid)
        assert tournament.rules_url == 'https://example.com/rules'
        assert tournament.stage_reminder_minutes == 15
        assert tournament.allow_player_match_requests is False
        assert tournament.is_test is False
        assert tournament.tournament_hours

    async def test_short_name_sets_trims_and_clears(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        async with mcp_session() as client:
            is_error, created = await call_tool(
                client, raw, 'create_tournament', tenant=TENANT,
                name='A Link to the Past Randomizer Main Bracket', short_name='  ALTTPR Main ',
            )
            assert not is_error, created
            assert created['short_name'] == 'ALTTPR Main'
            tid = created['id']

            is_error, too_long = await call_tool(
                client, raw, 'update_tournament', tenant=TENANT, tournament_id=tid,
                short_name='x' * 41,
            )
            assert is_error, too_long

            is_error, cleared = await call_tool(
                client, raw, 'update_tournament', tenant=TENANT, tournament_id=tid, short_name='',
            )
            assert not is_error, cleared
        tournament = await Tournament.get(id=tid)
        assert tournament.short_name is None
        assert tournament.short_label == 'A Link to the Past Randomizer Main Bracket'

    async def test_clear_removes_a_preset_and_the_hours(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        preset = await Preset.create(name='sgl2026', randomizer='ffr', settings={})
        tournament = await Tournament.create(
            name='FFR', preset=preset, seed_generator='ffr',
            tournament_hours={'2026-10-22': ['10:00', '22:00']},
        )
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'update_tournament', tenant=TENANT, tournament_id=tournament.id,
                clear=['preset_id', 'tournament_hours'],
            )
        assert not is_error, payload
        await tournament.refresh_from_db()
        assert tournament.preset_id is None
        assert tournament.seed_generator is None
        assert tournament.tournament_hours is None

    async def test_setting_and_clearing_one_field_is_refused(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        tournament = await Tournament.create(name='Cup')
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'update_tournament', tenant=TENANT, tournament_id=tournament.id,
                preset_id=1, clear=['preset_id'],
            )
        assert is_error, payload

    async def test_a_tournament_admin_edits_only_their_own(self, db):
        admin, raw = await _member_token(write=True)
        mine = await Tournament.create(name='Mine')
        other = await Tournament.create(name='Other')
        await mine.admins.add(admin)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'update_tournament', tenant=TENANT, tournament_id=mine.id,
                tournament_format='Swiss',
            )
            assert not is_error, payload
            is_error, payload = await call_tool(
                client, raw, 'update_tournament', tenant=TENANT, tournament_id=other.id,
                tournament_format='Swiss',
            )
            assert is_error and 'forbidden:' in payload, payload

    async def test_a_player_cannot_create_one(self, db):
        _, raw = await _member_token(write=True)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'create_tournament', tenant=TENANT, name='Nope',
            )
        assert is_error and 'forbidden:' in payload, payload
        assert not await Tournament.filter(name='Nope').exists()

    async def test_another_communitys_tournament_is_not_found(self, db):
        other = await Tenant.create(slug='other-community', name='Other', is_active=True)
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        theirs = await Tournament.create(name='Their Open', tenant_id=other.id)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'update_tournament', tenant=TENANT, tournament_id=theirs.id,
                name='Mine now',
            )
        assert is_error and 'not_found:' in payload, payload
        await theirs.refresh_from_db()
        assert theirs.name == 'Their Open'

    async def test_delete_needs_the_phrase_and_an_inactive_tournament(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        tournament = await Tournament.create(name='Old Cup', is_active=True)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'delete_tournament', tenant=TENANT, tournament_id=tournament.id,
                confirmation='permanently delete',
            )
            assert is_error, payload
            await call_tool(
                client, raw, 'update_tournament', tenant=TENANT, tournament_id=tournament.id,
                is_active=False,
            )
            is_error, payload = await call_tool(
                client, raw, 'delete_tournament', tenant=TENANT, tournament_id=tournament.id,
                confirmation='permanently delete',
            )
            assert not is_error, payload
        assert not await Tournament.filter(id=tournament.id).exists()


class TestStaffAndRoster:
    async def test_admins_and_coordinators(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        helper = await create_community_member(username='helper')
        tournament = await Tournament.create(name='Cup')
        async with mcp_session() as client:
            for tool in ('add_tournament_admin', 'add_crew_coordinator'):
                is_error, payload = await call_tool(
                    client, raw, tool, tenant=TENANT, tournament_id=tournament.id, user_id=helper.id,
                )
                assert not is_error, (tool, payload)
            assert await tournament.admins.filter(id=helper.id).exists()
            assert await tournament.crew_coordinators.filter(id=helper.id).exists()
            for tool in ('remove_tournament_admin', 'remove_crew_coordinator'):
                is_error, payload = await call_tool(
                    client, raw, tool, tenant=TENANT, tournament_id=tournament.id, user_id=helper.id,
                )
                assert not is_error, (tool, payload)
        assert not await tournament.admins.filter(id=helper.id).exists()
        assert not await tournament.crew_coordinators.filter(id=helper.id).exists()

    async def test_enroll_list_and_remove_a_player(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        player = await create_community_member(username='runner', dm_notifications=False)
        tournament = await Tournament.create(name='Cup')
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'enroll_tournament_player', tenant=TENANT,
                tournament_id=tournament.id, user_id=player.id,
            )
            assert not is_error, payload
            is_error, roster = await call_tool(
                client, raw, 'list_tournament_players', tenant=TENANT, tournament_id=tournament.id,
            )
            assert not is_error, roster
            assert [u['id'] for u in roster['result']] == [player.id]
            is_error, payload = await call_tool(
                client, raw, 'remove_tournament_player', tenant=TENANT,
                tournament_id=tournament.id, user_id=player.id,
            )
            assert not is_error, payload
        assert not await TournamentPlayers.filter(tournament=tournament, user=player).exists()

    async def test_a_non_member_cannot_be_enrolled(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        stranger = await User.create(discord_id=883001, username='stranger')
        tournament = await Tournament.create(name='Cup')
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'enroll_tournament_player', tenant=TENANT,
                tournament_id=tournament.id, user_id=stranger.id,
            )
        assert is_error, payload
        assert not await TournamentPlayers.filter(tournament=tournament).exists()

    async def test_a_player_cannot_read_the_roster(self, db):
        _, raw = await _member_token()
        tournament = await Tournament.create(name='Cup')
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'list_tournament_players', tenant=TENANT, tournament_id=tournament.id,
            )
        assert is_error and 'forbidden:' in payload, payload


class TestDiscordEvents:
    async def test_staff_turn_events_on(self, db, monkeypatch):
        from application.services.discord import discord_event_sync_service
        from application.services.discord.discord_service import MockDiscordService

        monkeypatch.setattr(discord_event_sync_service, 'DiscordService', MockDiscordService)
        tenant = await Tenant.get(id=1)
        tenant.discord_guild_id = 424242
        await tenant.save()
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        tournament = await Tournament.create(name='Cup')
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'update_tournament_discord_events', tenant=TENANT,
                tournament_id=tournament.id, enabled=True, duration_minutes=90,
            )
        assert not is_error, payload
        assert payload['discord_events_enabled'] is True
        await tournament.refresh_from_db()
        assert tournament.discord_event_duration_minutes == 90

    async def test_a_non_admin_is_refused_whether_or_not_the_tournament_exists(self, db):
        """The permission check comes first, as on REST, so the answer can't be
        used to probe which tournament ids exist."""
        _, raw = await _member_token(write=True)
        tournament = await Tournament.create(name='Cup')
        async with mcp_session() as client:
            for tournament_id in (tournament.id, tournament.id + 999):
                is_error, payload = await call_tool(
                    client, raw, 'update_tournament_discord_events', tenant=TENANT,
                    tournament_id=tournament_id, enabled=True,
                )
                assert is_error and 'forbidden:' in payload, (tournament_id, payload)
