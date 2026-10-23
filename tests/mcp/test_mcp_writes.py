"""The write surface: who may reach it, and whether it actually writes.

Three layers, and each catches something the others cannot:

* **The connection gate.** A read-only grant — the consent screen's default —
  must be refused at every write tool. This is the load-bearing one: the tools
  are hidden from a read-only listing, and a check that only hid them would look
  identical in every client while a hand-written ``tools/call`` sailed through.
* **The role gate underneath it.** A writing grant is not a promotion. The token
  acts as its user, so a member without the role is refused exactly as they are
  in the web UI.
* **The write itself.** A tool that returns cleanly having changed nothing is
  the failure no gate test can see, so each one is asserted against the row.
"""

from datetime import datetime, timedelta, timezone

import pytest

from models import Commentator, Match, MatchPlayers, Role, Stage, Tournament, User
from tests.api_helpers import create_community_member
from tests.mcp.conftest import call_tool, create_oauth_token, mcp_session

TENANT = 'default'

# Every write tool with arguments that reach the gate rather than failing
# validation first. The gate runs before the tool body, so these need not name
# rows that exist.
WRITE_CALLS = {
    'export_tournaments': {},
    'export_volunteer_data': {'start': '2026-10-22T00:00:00', 'end': '2026-10-26T00:00:00'},
    'create_match': {
        'tournament_id': 1, 'scheduled_date': '2026-05-01',
        'scheduled_time': '18:00', 'player_ids': [1, 2],
    },
    'submit_match_request': {
        'tournament_id': 1, 'scheduled_date': '2026-05-01',
        'scheduled_time': '18:00', 'player_ids': [1],
    },
    'update_match': {'match_id': 1, 'comment': 'x'},
    'delete_match': {'match_id': 1},
    'extend_match_to_set': {
        'match_id': 1,
        'later_games': [{'scheduled_date': '2026-05-01', 'scheduled_time': '20:00'},
                        {'scheduled_date': '2026-05-01', 'scheduled_time': '22:00'}],
    },
    'set_match_stream_candidate': {'match_id': 1, 'flag': True},
    'assign_match_stage': {'match_id': 1, 'stage_id': 1},
    'assign_match_stations': {'match_id': 1, 'assignments': {}},
    'seat_match': {'match_id': 1},
    'start_match': {'match_id': 1},
    'finish_match': {'match_id': 1},
    'confirm_match': {'match_id': 1},
    'record_match_result': {'match_id': 1, 'winner_id': 1},
    'set_match_review': {'match_id': 1, 'needs_review': True},
    'generate_match_seed': {'match_id': 1},
    'set_crew_approval': {'crew_id': 1, 'role': 'commentator', 'approved': True},
    'signup_as_crew': {'match_id': 1, 'role': 'commentator'},
    'withdraw_crew_signup': {'match_id': 1, 'role': 'commentator'},
    'acknowledge_match': {'match_id': 1},
    'watch_match': {'match_id': 1},
    'unwatch_match': {'match_id': 1},
    'offer_match_for_stream': {'match_id': 1},
    'withdraw_stream_offer': {'match_id': 1},
    'check_in_attendee': {'entrant_id': 1},
    'undo_attendee_check_in': {'entrant_id': 1},
    'link_attendee': {'entrant_id': 1, 'user_id': 1},
    'unlink_attendee': {'entrant_id': 1},
    'add_walk_up': {'event_id': 1, 'user_id': 1},
    'sync_check_in_event': {'event_id': 1},
    'check_in_volunteer': {'assignment_id': 1},
    'approve_join_request': {'request_id': 1},
    'deny_join_request': {'request_id': 1},
    'create_tournament': {'name': 'Gate Cup'},
    'update_tournament': {'tournament_id': 1, 'name': 'x'},
    'delete_tournament': {'tournament_id': 1, 'confirmation': 'permanently delete'},
    'add_tournament_admin': {'tournament_id': 1, 'user_id': 1},
    'remove_tournament_admin': {'tournament_id': 1, 'user_id': 1},
    'add_crew_coordinator': {'tournament_id': 1, 'user_id': 1},
    'remove_crew_coordinator': {'tournament_id': 1, 'user_id': 1},
    'enroll_tournament_player': {'tournament_id': 1, 'user_id': 1},
    'remove_tournament_player': {'tournament_id': 1, 'user_id': 1},
    'drop_tournament_player': {'tournament_id': 1, 'user_id': 1, 'reason': 'withdrew'},
    'link_challonge_tournament': {'tournament_id': 1, 'challonge': 'abc'},
    'unlink_challonge_tournament': {'tournament_id': 1},
    'sync_challonge_tournament': {'tournament_id': 1},
    'update_tournament_discord_events': {'tournament_id': 1, 'enabled': True},
    'create_stage': {'name': 'Gate Stage'},
    'update_stage': {'stage_id': 1, 'name': 'x'},
    'delete_stage': {'stage_id': 1},
    'create_volunteer_position': {'name': 'Gate Desk'},
    'update_volunteer_position': {'position_id': 1, 'name': 'x'},
    'delete_volunteer_position': {'position_id': 1},
    'create_volunteer_shift': {
        'position_id': 1, 'starts_at': '2026-10-22T10:00:00+00:00', 'ends_at': '2026-10-22T12:00:00+00:00',
    },
    'update_volunteer_shift': {'shift_id': 1, 'slots_needed': 2},
    'delete_volunteer_shift': {'shift_id': 1},
    'assign_volunteer': {'shift_id': 1, 'user_id': 1},
    'unassign_volunteer': {'assignment_id': 1},
    'add_community_member': {'user_id': 1},
    'remove_community_member': {'user_id': 1},
    'grant_community_role': {'user_id': 1, 'role': 'proctor'},
    'revoke_community_role': {'user_id': 1, 'role': 'proctor'},
    'create_equipment': {'name': 'Gate Pad'},
    'update_equipment': {'equipment_id': 1, 'name': 'x'},
    'delete_equipment': {'equipment_id': 1},
    'check_out_equipment': {'equipment_id': 1},
    'check_in_equipment': {'equipment_id': 1},
    'set_tournament_prize_pool': {'tournament_id': 1, 'prize_pool': '100'},
    'set_tournament_payout_split': {'tournament_id': 1, 'lines': []},
}


async def _match(*, players: list[User] | None = None) -> Match:
    tournament = await Tournament.create(name='Spring Open', is_active=True)
    match = await Match.create(
        tournament=tournament,
        scheduled_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
    )
    for player in players or []:
        await MatchPlayers.create(match=match, user=player)
    return match


class TestTheConnectionGate:
    async def test_a_read_only_connection_is_refused_every_write(self, db):
        """Hiding the tools is not the gate; this is.

        A client that kept a cached listing, or one driving the JSON-RPC by
        hand, reaches these names whether or not they were advertised.
        """
        _, raw = await create_oauth_token(roles=[Role.STAFF])
        async with mcp_session() as client:
            for name, args in WRITE_CALLS.items():
                is_error, payload = await call_tool(
                    client, raw, name, tenant=TENANT, **args
                )
                assert is_error, name
                assert 'forbidden:' in payload, (name, payload)
                assert 'read' in payload.lower(), (name, payload)

    async def test_a_writing_connection_passes_the_connection_gate(self, db):
        """Same caller, same tools, box ticked — the refusal has to change.

        Asserted as "not refused for being read-only" rather than "succeeds",
        because these ids name nothing: a `not_found` here means the call got
        past the gate, which is what is under test.
        """
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        async with mcp_session() as client:
            for name, args in WRITE_CALLS.items():
                is_error, payload = await call_tool(
                    client, raw, name, tenant=TENANT, **args
                )
                if not is_error:
                    continue
                assert 'read-only' not in payload.lower(), (name, payload)

    async def test_the_membership_floor_still_answers_first(self, db):
        """A writing token must not disclose a community the user cannot see.

        If the read-only check ran first, a role-less user would learn from the
        wording that their *connection* was the problem — which is only true of
        a community that exists and that they might otherwise reach.
        """
        _, raw = await create_oauth_token()
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'update_match', tenant=TENANT, match_id=1, comment='x',
            )
        assert is_error
        assert 'not_found:' in payload, payload


class TestTheRoleGateUnderneath:
    async def test_writing_is_not_a_promotion(self, db):
        """The token acts as its user; approving writes grants no role."""
        _, raw = await create_oauth_token(roles=[Role.VOLUNTEER], write=True)
        match = await _match()
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'delete_match', tenant=TENANT, match_id=match.id,
            )
        assert is_error, payload
        assert 'forbidden:' in payload, payload
        assert await Match.filter(id=match.id).exists()

    async def test_broadcast_tech_runs_only_a_match_on_a_stage(self, db):
        """Their Proctor Station lists stage matches only; this is the gate
        that keeps an off-stage match out of reach over MCP too."""
        _, raw = await create_oauth_token(roles=[Role.STREAM_MANAGER], write=True)
        player = await create_community_member(discord_id=772101, username='p-stage')
        off_stage = await _match(players=[player])
        on_stage = await _match(players=[player])
        stage = await Stage.create(name='Main Stage', is_active=True)
        await Match.filter(id=on_stage.id).update(stage_id=stage.id)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'seat_match', tenant=TENANT, match_id=off_stage.id,
            )
            assert is_error, payload
            assert 'forbidden:' in payload, payload
            is_error, payload = await call_tool(
                client, raw, 'seat_match', tenant=TENANT, match_id=on_stage.id,
            )
            assert not is_error, payload


class TestTheWritesLand:
    async def test_create_match_returns_the_new_match(self, db):
        user, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        tournament = await Tournament.create(name='Spring Open', is_active=True)
        player = await create_community_member(discord_id=771001, username='player-one')
        other = await create_community_member(discord_id=771002, username='player-two')
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'create_match', tenant=TENANT,
                tournament_id=tournament.id,
                scheduled_date='2026-05-01', scheduled_time='18:00',
                player_ids=[player.id, other.id],
            )
        assert not is_error, payload
        assert payload['tournament']['id'] == tournament.id
        assert {p['user']['id'] for p in payload['players']} == {player.id, other.id}
        assert await Match.filter(id=payload['id']).exists()

    async def test_update_match_changes_the_row(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        match = await _match()
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'update_match', tenant=TENANT,
                match_id=match.id, comment='moved for the restream',
            )
        assert not is_error, payload
        assert payload['comment'] == 'moved for the restream'
        await match.refresh_from_db()
        assert match.comment == 'moved for the restream'

    async def test_assign_stage_and_clear_it(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        match = await _match()
        stage = await Stage.create(name='Main Stage', is_active=True)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'assign_match_stage', tenant=TENANT,
                match_id=match.id, stage_id=stage.id,
            )
            assert not is_error, payload
            assert payload['stage']['id'] == stage.id

            # Omitting the id clears the stage rather than leaving it alone —
            # the one argument whose absence means something.
            is_error, payload = await call_tool(
                client, raw, 'assign_match_stage', tenant=TENANT,
                match_id=match.id,
            )
        assert not is_error, payload
        assert payload['stage'] is None

    async def test_delete_match_removes_it(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        match = await _match()
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'delete_match', tenant=TENANT, match_id=match.id,
            )
        assert not is_error, payload
        assert payload['ok'] is True
        assert not await Match.filter(id=match.id).exists()

    async def test_a_crew_coordinator_approves_a_signup(self, db):
        coordinator, raw = await create_oauth_token(roles=[Role.VOLUNTEER], write=True)
        match = await _match()
        await match.tournament.crew_coordinators.add(coordinator)
        crew = await create_community_member(discord_id=771010, username='caster')
        signup = await Commentator.create(match=match, user=crew)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'set_crew_approval', tenant=TENANT,
                crew_id=signup.id, role='commentator', approved=True,
            )
        assert not is_error, payload
        await signup.refresh_from_db()
        assert signup.approved is True

    async def test_crew_approval_refuses_a_plain_volunteer(self, db):
        _, raw = await create_oauth_token(roles=[Role.VOLUNTEER], write=True)
        match = await _match()
        crew = await create_community_member(discord_id=771011, username='caster')
        signup = await Commentator.create(match=match, user=crew)
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'set_crew_approval', tenant=TENANT,
                crew_id=signup.id, role='commentator', approved=True,
            )
        assert is_error and 'forbidden:' in payload, payload
        await signup.refresh_from_db()
        assert signup.approved is False

    async def test_a_tournament_in_another_community_cannot_be_scheduled_into(self, db):
        """A bare id must not be able to plant a match across the boundary.

        The permission check short-circuits for staff and the scheduling-hours
        check falls through for a tournament it cannot see, so before the scoped
        resolve in `create_match` this produced a real match — and enrolled its
        players — in the other community's tournament.
        """
        from models import Tenant

        other = await Tenant.create(slug='other-community', name='Other', is_active=True)
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        theirs = await Tournament.create(
            name='Their Open', is_active=True, tenant_id=other.id,
        )
        player = await User.create(discord_id=772001, username='p1')
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'create_match', tenant=TENANT,
                tournament_id=theirs.id, scheduled_date='2026-05-01',
                scheduled_time='18:00', player_ids=[player.id],
            )
        assert is_error, payload
        assert 'not_found:' in payload, payload
        assert not await Match.filter(tournament_id=theirs.id).exists()

    async def test_stations_keyed_by_user_id_are_refused(self, db):
        """Applying nothing and reporting success is the worst answer here.

        The keys are participation-row ids and the near-miss is a user id, so a
        wrong key set used to mean a clean `MatchResponse`, an audit row and an
        event for a write that changed nothing.
        """
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        player = await User.create(discord_id=772002, username='p2')
        match = await _match(players=[player])
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'assign_match_stations', tenant=TENANT,
                match_id=match.id, assignments={str(player.id + 5000): 'A'},
            )
        assert is_error, payload
        assert 'invalid_request:' in payload, payload
        assert 'user ids' in payload, payload

    async def test_a_match_in_another_community_is_not_found(self, db):
        """The write path hand-scopes its load; a bare id must not cross over."""
        from models import Tenant

        other = await Tenant.create(slug='other-community', name='Other', is_active=True)
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        tournament = await Tournament.create(
            name='Their Open', is_active=True, tenant_id=other.id,
        )
        match = await Match.create(
            tournament=tournament, tenant_id=other.id,
            scheduled_at=datetime(2026, 5, 1, 18, 0, tzinfo=timezone.utc),
        )
        async with mcp_session() as client:
            is_error, payload = await call_tool(
                client, raw, 'seat_match', tenant=TENANT, match_id=match.id,
            )
        assert is_error
        assert 'not_found:' in payload, payload


class TestOrientationReportsTheGrant:
    async def test_whoami_reports_a_read_only_connection(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF])
        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'whoami')
        assert not is_error, payload
        assert payload['can_write'] is False

    async def test_whoami_reports_a_writing_connection(self, db):
        _, raw = await create_oauth_token(roles=[Role.STAFF], write=True)
        async with mcp_session() as client:
            is_error, payload = await call_tool(client, raw, 'whoami')
        assert not is_error, payload
        assert payload['can_write'] is True


class TestConsentDecidesTheGrant:
    async def test_approving_without_the_box_mints_a_read_only_token(self, db):
        from application.services.mcp_auth_service import READ_SCOPE, WRITE_SCOPE

        _, token = await _approve(allow_write=False)
        assert token.read_only is True
        assert token.scope == READ_SCOPE
        assert WRITE_SCOPE not in (token.scope or '')

    async def test_approving_with_the_box_mints_a_writing_token(self, db):
        from application.services.mcp_auth_service import WRITE_SCOPE

        _, token = await _approve(allow_write=True)
        assert token.read_only is False
        assert WRITE_SCOPE in token.scope

    @pytest.mark.parametrize('typed', [None, '', 'this is fine', 'dangerous'])
    async def test_write_without_the_phrase_is_refused(self, db, typed):
        """Ticking the box is not enough; the sentence has to be typed."""
        service, user, txn = await _pending_grant()
        with pytest.raises(ValueError, match='this is dangerous'):
            await service.approve(txn, user, allow_write=True, write_confirmation=typed)
        # A typo must not burn the request: the same screen can try again.
        raw_code, _, _ = await service.approve(
            txn, user, allow_write=True, write_confirmation='this is dangerous',
        )
        assert raw_code

    @pytest.mark.parametrize('typed', ['This Is Dangerous', '  this  is dangerous '])
    async def test_case_and_spacing_are_forgiven(self, db, typed):
        service, user, txn = await _pending_grant()
        assert await service.approve(txn, user, allow_write=True, write_confirmation=typed)

    async def test_read_only_needs_no_phrase(self, db):
        service, user, txn = await _pending_grant()
        assert await service.approve(txn, user, allow_write=False)

    async def test_a_client_requesting_write_still_gets_read_only(self, db):
        """The client asks; the human decides. Nothing else does.

        Without this, a client that pads its authorize request with
        `scope=wizzrobe:write` would be granted it by default, and the box on
        the consent screen would be decoration.
        """
        _, token = await _approve(allow_write=False, requested_scopes=[
            'wizzrobe:read', 'wizzrobe:write',
        ])
        assert token.read_only is True

    async def test_a_refresh_cannot_widen_a_read_only_grant(self, db):
        """Rotation re-keys the row, so the grant is fixed at consent."""
        from application.services import McpAuthService

        _, token = await _approve(allow_write=False)
        await McpAuthService().exchange_refresh(token)
        await token.refresh_from_db()
        assert token.read_only is True


async def _pending_grant(requested_scopes: list[str] | None = None):
    """Park one authorization request; return ``(service, user, txn)``."""
    import secrets

    from application.services import McpAuthService
    from application.services.mcp_auth_service import PendingAuthorization
    from models import McpOAuthClient

    service = McpAuthService()
    client = await McpOAuthClient.create(
        client_id=f'consent-{secrets.token_hex(4)}',
        client_name='Consent Test Client',
        redirect_uris=['http://localhost/callback'],
        grant_types=['authorization_code', 'refresh_token'],
        response_types=['code'],
    )
    user = await User.create(
        discord_id=secrets.randbelow(10 ** 12) + 1, username=f'c-{secrets.token_hex(3)}',
    )
    txn = service.begin_authorization(PendingAuthorization(
        client_id=client.client_id,
        redirect_uri='http://localhost/callback',
        redirect_uri_provided_explicitly=True,
        code_challenge='challenge',
        scopes=requested_scopes or ['wizzrobe:read'],
        state=None,
        resource=None,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    ))
    return service, user, txn


async def _approve(*, allow_write: bool, requested_scopes: list[str] | None = None):
    """Drive one grant through consent and return ``(user, token)``."""
    from application.services.mcp_auth_service import WRITE_CONFIRMATION_PHRASE
    from models import ApiToken

    service, user, txn = await _pending_grant(requested_scopes)
    client_id = service.get_pending(txn).client_id
    raw_code, _, _ = await service.approve(
        txn, user, allow_write=allow_write,
        write_confirmation=WRITE_CONFIRMATION_PHRASE if allow_write else None,
    )
    code = await service.load_code(client_id, raw_code)
    await service.exchange_code(code)
    return user, await ApiToken.get(user=user)
