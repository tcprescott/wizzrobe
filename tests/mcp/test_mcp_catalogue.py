"""Mechanical guardrails over the served tool catalogue.

The point of these is that they fail when someone adds a tool the *wrong* way.
``registry.register`` is the only path that attaches a gate, so a tool added via
``@mcp.tool()`` would serve data with no authorization at all and nothing else
in the suite would notice — its own happy-path test would pass.
"""

from mcpserver.auth import Gate
from mcpserver.registry import TOOL_SPECS
from models import Role
from tests.mcp.conftest import create_oauth_token, list_tools, mcp_session

# Tools that legitimately take no community, because they are how a caller
# discovers which communities exist.
GLOBAL_TOOLS = {'whoami', 'list_tenants', 'server_version'}

# Super-admin tools: they take no `tenant` either (they reach every community),
# and are listed only to a super admin.
PLATFORM_READ_TOOLS = {
    'platform_get_community',
    'platform_get_user',
    'platform_list_communities',
    'platform_list_feature_groups',
    'platform_list_log_levels',
    'platform_list_racetime_bots',
    'platform_list_users',
    'platform_service_health',
    'runtime_stats',
}
PLATFORM_WRITE_TOOLS = {
    'platform_create_community',
    'platform_create_feature_group',
    'platform_delete_feature_group',
    'platform_grant_community_staff',
    'platform_grant_racetime_bot',
    'platform_refresh_service_health',
    'platform_reset_log_level',
    'platform_restart_racetime_bot',
    'platform_revoke_racetime_bot',
    'platform_set_community_active',
    'platform_set_community_feature',
    'platform_set_community_feature_group',
    'platform_set_log_level',
    'platform_set_user_active',
    'platform_update_community',
    'platform_update_feature_group',
    'platform_update_racetime_bot',
}
PLATFORM_TOOLS = PLATFORM_READ_TOOLS | PLATFORM_WRITE_TOOLS

# A snapshot, so adding or renaming a tool is a deliberate diff rather than an
# invisible change to a published contract.
READ_TOOLS = {
    'get_export',
    'list_my_exports',
    'activity_trends',
    'capacity_forecast',
    'crew_coverage_report',
    'crew_participation_trends',
    'get_async_qualifier',
    'get_async_qualifier_leaderboard',
    'get_bracket',
    'get_bracket_standings',
    'get_check_in_roster',
    'get_equipment',
    'get_match',
    'get_member_details',
    'get_player_availability',
    'get_preset',
    'get_race_room',
    'get_schedule',
    'get_stage',
    'get_system_config',
    'get_tournament',
    'get_tournament_payouts',
    'get_user',
    'list_async_qualifier_live_races',
    'list_async_qualifiers',
    'list_audit_log',
    'list_bracket_entrants',
    'list_bracket_matches',
    'list_brackets',
    'list_check_in_events',
    'list_equipment',
    'list_feedback',
    'list_join_requests',
    'list_match_crew',
    'list_matches',
    'list_notification_settings',
    'list_presets',
    'list_race_room_profiles',
    'list_race_rooms',
    'list_randomizers',
    'list_sent_messages',
    'list_service_health',
    'list_speedgaming_episodes',
    'list_speedgaming_links',
    'list_stages',
    'list_tenants',
    'list_tournament_players',
    'list_tournament_signups',
    'list_tournaments',
    'list_triforce_texts',
    'list_unbooked_matches',
    'list_users',
    'list_volunteer_positions',
    'list_volunteer_shifts',
    'list_webhook_deliveries',
    'list_webhooks',
    'match_operations_report',
    'matches_active_at',
    'my_equipment_checkouts',
    'my_notification_settings',
    'my_reschedule_requests',
    'my_volunteer_shifts',
    'server_version',
    'stage_utilization',
    'suggest_match_time',
    'telemetry_summary',
    'telemetry_top',
    'tournament_health',
    'volunteer_coverage',
    'volunteer_hour_trends',
    'whoami',
}

# The tools that change data. Served only to a connection whose consent screen
# was approved with the write box ticked, so they are snapshotted separately —
# a write tool that drifted into the read set would be one every read-only
# client is suddenly offered.
WRITE_TOOLS = {
    'export_tournaments',
    'export_volunteer_data',
    'acknowledge_match',
    'add_community_member',
    'add_crew_coordinator',
    'add_tournament_admin',
    'add_walk_up',
    'approve_join_request',
    'assign_match_stage',
    'assign_match_stations',
    'assign_volunteer',
    'check_in_attendee',
    'check_in_equipment',
    'check_in_volunteer',
    'check_out_equipment',
    'confirm_match',
    'create_equipment',
    'create_match',
    'create_stage',
    'create_tournament',
    'create_volunteer_position',
    'create_volunteer_shift',
    'delete_equipment',
    'delete_match',
    'delete_stage',
    'delete_tournament',
    'delete_volunteer_position',
    'delete_volunteer_shift',
    'deny_join_request',
    'drop_tournament_player',
    'enroll_tournament_player',
    'finish_match',
    'generate_match_seed',
    'grant_community_role',
    'link_attendee',
    'link_challonge_tournament',
    'offer_match_for_stream',
    'record_match_result',
    'remove_community_member',
    'remove_crew_coordinator',
    'remove_tournament_admin',
    'remove_tournament_player',
    'reset_notification_settings',
    'revoke_community_role',
    'seat_match',
    'set_crew_approval',
    'set_match_review',
    'set_match_stream_candidate',
    'set_my_notification',
    'set_my_notification_preset',
    'set_notification_setting',
    'set_staff_notification_setting',
    'set_tournament_payout_split',
    'set_tournament_prize_pool',
    'sign_up_for_tournament',
    'signup_as_crew',
    'start_match',
    'submit_match_request',
    'sync_challonge_tournament',
    'sync_check_in_event',
    'unassign_volunteer',
    'undo_attendee_check_in',
    'unlink_attendee',
    'unlink_challonge_tournament',
    'unwatch_match',
    'update_equipment',
    'update_match',
    'update_stage',
    'update_tournament',
    'update_tournament_discord_events',
    'update_volunteer_position',
    'update_volunteer_shift',
    'watch_match',
    'withdraw_crew_signup',
    'withdraw_from_tournament',
    'withdraw_stream_offer',
}

EXPECTED_TOOLS = READ_TOOLS | WRITE_TOOLS
EVERY_TOOL = EXPECTED_TOOLS | PLATFORM_TOOLS

# Writes that remove something rather than adding or amending it. Clients use
# the distinction to decide how hard to ask before proceeding.
DESTRUCTIVE_TOOLS = {
    'delete_equipment',
    'delete_match',
    'delete_stage',
    'delete_tournament',
    'delete_volunteer_position',
    'delete_volunteer_shift',
    'drop_tournament_player',
    'platform_delete_feature_group',
    'platform_revoke_racetime_bot',
    'platform_set_community_active',
    'platform_set_user_active',
    'remove_community_member',
    'remove_crew_coordinator',
    'remove_tournament_admin',
    'remove_tournament_player',
    'reset_notification_settings',
    'revoke_community_role',
    'unassign_volunteer',
    'undo_attendee_check_in',
    'unlink_attendee',
    'unlink_challonge_tournament',
    'withdraw_crew_signup',
    'withdraw_from_tournament',
    'withdraw_stream_offer',
}


class TestCatalogue:
    async def test_a_writing_connection_is_served_every_tool(self, db):
        _, raw = await create_oauth_token(write=True)
        async with mcp_session() as client:
            tools = await list_tools(client, raw)
        assert {t['name'] for t in tools} == EXPECTED_TOOLS

    async def test_a_read_only_connection_is_served_no_write_tools(self, db):
        """The default grant must not advertise what it cannot call.

        Every write is refused at the gate regardless — this is about not
        spending a read-only client's context on nineteen tools it will never
        be allowed to use, and not letting the model plan a write it cannot do.
        """
        _, raw = await create_oauth_token()
        async with mcp_session() as client:
            tools = await list_tools(client, raw)
        assert {t['name'] for t in tools} == READ_TOOLS

    async def test_a_super_admin_is_also_served_the_platform_tools(self, db):
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            tools = await list_tools(client, raw)
        assert {t['name'] for t in tools} == EVERY_TOOL

    async def test_a_read_only_super_admin_gets_no_platform_writes(self, db):
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN])
        async with mcp_session() as client:
            tools = await list_tools(client, raw)
        assert {t['name'] for t in tools} == READ_TOOLS | PLATFORM_READ_TOOLS

    async def test_every_served_tool_is_gated(self, db):
        """The load-bearing one: nothing reached the server bypassing register()."""
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            tools = await list_tools(client, raw)
        ungated = [t['name'] for t in tools if t['name'] not in TOOL_SPECS]
        assert not ungated, f'tools served without a gate: {ungated}'

    async def test_every_tool_is_described_and_typed(self, db):
        """An undescribed tool is one the model will misuse or ignore."""
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            tools = await list_tools(client, raw)
        for tool in tools:
            assert tool.get('description', '').strip(), tool['name']
            schema = tool.get('inputSchema') or {}
            assert schema.get('type') == 'object', tool['name']

    async def test_every_tool_declares_an_output_schema(self, db):
        """Without one, the SDK returns the payload as a text blob.

        A bare ``-> dict`` annotation produces no output schema, so the client
        gets JSON-in-a-string with no declared shape while a ``Dict[str, Any]``
        one right beside it returns proper ``structuredContent``. The difference
        is invisible in the tool source and only shows up at the client, which
        is exactly why it is asserted here.
        """
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            tools = await list_tools(client, raw)
        missing = [t['name'] for t in tools if not t.get('outputSchema')]
        assert not missing, f'tools returning unstructured text: {missing}'

    async def test_the_read_only_hint_matches_the_declared_kind(self, db):
        """Clients skip the confirmation prompt on a readOnlyHint tool.

        So a write mis-annotated as a read is a change the user is never asked
        about, and a read mis-annotated as a write is a prompt on every lookup.
        """
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            tools = await list_tools(client, raw)
        for tool in tools:
            annotations = tool.get('annotations') or {}
            expected = tool['name'] not in WRITE_TOOLS | PLATFORM_WRITE_TOOLS
            assert annotations.get('readOnlyHint') is expected, tool['name']
            assert annotations.get('destructiveHint') is (
                tool['name'] in DESTRUCTIVE_TOOLS
            ), tool['name']

    async def test_community_scoped_tools_declare_a_tenant_argument(self, db):
        """A tool without `tenant` cannot be scoped, so it must be a GLOBAL one.

        Catches the reverse mistake too: a genuinely global tool that grew a
        tenant argument nobody honours.
        """
        _, raw = await create_oauth_token(roles=[Role.SUPER_ADMIN], write=True)
        async with mcp_session() as client:
            tools = await list_tools(client, raw)
        for tool in tools:
            properties = (tool.get('inputSchema') or {}).get('properties') or {}
            has_tenant = 'tenant' in properties
            if tool['name'] in GLOBAL_TOOLS | PLATFORM_TOOLS:
                assert not has_tenant, f'{tool["name"]} should not take a tenant'
            else:
                assert has_tenant, f'{tool["name"]} is missing a tenant argument'

    def test_gate_registry_agrees_with_global_list(self):
        """The two notions of "global" must not drift apart."""
        declared_global = {
            name for name, spec in TOOL_SPECS.items() if spec.gate is Gate.GLOBAL
        }
        assert declared_global == GLOBAL_TOOLS

    def test_gate_registry_agrees_with_the_write_snapshot(self):
        """``write=True`` is what hides a tool and refuses a read-only token.

        A tool that changes data but was registered without it would be served
        to every connection and refuse none of them, which no other assertion
        here would catch.
        """
        declared_write = {name for name, spec in TOOL_SPECS.items() if spec.write}
        assert declared_write == WRITE_TOOLS | PLATFORM_WRITE_TOOLS

    def test_gate_registry_agrees_with_the_platform_snapshot(self):
        """``Gate.PLATFORM`` is what requires a super admin and hides the tool from everyone else."""
        declared = {name for name, spec in TOOL_SPECS.items() if spec.gate is Gate.PLATFORM}
        assert declared == PLATFORM_TOOLS
