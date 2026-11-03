"""CheckInService: the Matcherino venue sync, account matching, and the desk's actions."""

# ruff: noqa: F811 — the shared fixtures are imported from check_in_support, and
# pytest injects them as same-named test arguments.

import json
from datetime import timedelta

import pytest

from application.errors import FeatureDisabledError
from application.events import check_in_live
from application.services.check_in_rules import handle_id, summarize
from application.services.check_in_service import CheckInService
from application.services.feature_flag_service import reset_flag_cache
from application.utils.clients.matcherino_client import (
    MatcherinoAPIError,
    MockMatcherinoClient,
    mock_purchase,
)
from models import (
    AuditLog,
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEventStatus,
    CheckInLinkMethod,
    FeatureFlag,
    TenantFeatureFlag,
    TenantMembership,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user
from tests.services.check_in_support import (  # noqa: F401 — fixtures
    VENUE,
    ScriptedClient,
    client,
    desk,
    event,
    service,
    staff,
)
from tests.services.check_in_support import (
    rows as _rows,
)


class TestAutoLink:
    async def test_each_identifier_links_its_account(self, service, client, staff, event):
        by_discord = await make_user(discord_id=300000000000000010, username='disc_person')
        by_twitch_id = await make_user(discord_id=11, username='twitch_person', twitch_user_id='700000010')
        by_matcherino_id = await make_user(discord_id=13, username='known', matcherino_user_id='500')
        by_handle = await make_user(discord_id=14, username='typed', matcherino_username='Typed Name#600')
        client.set([
            mock_purchase(100, 'DiscPerson', auth_provider='discord', auth_id='300000000000000010'),
            mock_purchase(200, 'TwitchPerson', auth_provider='twitch', auth_id='700000010'),
            mock_purchase(500, 'Known', auth_provider='gplus'),
            mock_purchase(600, 'Renamed Since', auth_provider='facebook'),
            mock_purchase(700, 'Nobody', auth_provider='gplus'),
        ])

        result = await service.sync_event(staff, event.id)

        rows = await _rows(event)
        assert result.added == 5 and result.auto_linked == 4
        assert (rows['100'].user_id, rows['100'].link_method) == (by_discord.id, CheckInLinkMethod.DISCORD_ID)
        assert (rows['200'].user_id, rows['200'].link_method) == (by_twitch_id.id, CheckInLinkMethod.TWITCH_ID)
        assert (rows['500'].user_id, rows['500'].link_method) == (by_matcherino_id.id, CheckInLinkMethod.MATCHERINO_ID)
        assert (rows['600'].user_id, rows['600'].link_method) == (by_handle.id, CheckInLinkMethod.MATCHERINO_HANDLE)
        assert rows['700'].user_id is None

    async def test_the_remembered_matcherino_id_wins_over_discord(self, service, client, staff, event):
        remembered = await make_user(discord_id=20, username='remembered', matcherino_user_id='100')
        await make_user(discord_id=999, username='discord-owner')
        client.set([mock_purchase(100, 'x', auth_provider='discord', auth_id='999')])

        await service.sync_event(staff, event.id)

        row = (await _rows(event))['100']
        assert row.user_id == remembered.id
        assert row.link_method == CheckInLinkMethod.MATCHERINO_ID

    async def test_a_match_fills_only_empty_matcherino_fields(self, service, client, staff, event):
        fresh = await make_user(discord_id=31, username='fresh')
        typed = await make_user(discord_id=32, username='typed', matcherino_username='my own#1')
        client.set([
            mock_purchase(100, 'Fresh', auth_provider='discord', auth_id='31'),
            mock_purchase(200, 'Typed', auth_provider='discord', auth_id='32'),
        ])

        await service.sync_event(staff, event.id)

        await fresh.refresh_from_db()
        await typed.refresh_from_db()
        assert (fresh.matcherino_user_id, fresh.matcherino_username) == ('100', 'Fresh#100')
        assert (typed.matcherino_user_id, typed.matcherino_username) == ('200', 'my own#1')

    async def test_two_registrations_for_one_account_link_only_the_first(self, service, client, staff, event):
        owner = await make_user(discord_id=40, username='owner', twitch_user_id='700000040')
        client.set([
            mock_purchase(100, 'Owner', auth_provider='discord', auth_id='40'),
            mock_purchase(200, 'Owner alt', auth_provider='twitch', auth_id='700000040'),
        ])

        await service.sync_event(staff, event.id)

        rows = await _rows(event)
        assert rows['100'].user_id == owner.id
        assert rows['200'].user_id is None

    async def test_a_later_sync_links_someone_who_has_since_signed_up(self, service, client, staff, event):
        client.set([mock_purchase(100, 'Late', auth_provider='discord', auth_id='50')])
        await service.sync_event(staff, event.id)
        assert (await _rows(event))['100'].user_id is None

        late = await make_user(discord_id=50, username='late')
        result = await service.sync_event(staff, event.id)

        assert result.auto_linked == 1
        assert (await _rows(event))['100'].user_id == late.id


class TestRememberedAccounts:
    async def test_a_typed_handle_links_but_is_not_promoted(self, service, client, staff, event):
        typed = await make_user(discord_id=15, username='typed', matcherino_username='Me#600')
        client.set([mock_purchase(600, 'Someone', auth_provider='gplus')])

        await service.sync_event(staff, event.id)

        await typed.refresh_from_db()
        assert (await _rows(event))['600'].user_id == typed.id
        assert typed.matcherino_user_id is None

    async def test_remembering_is_audited(self, service, client, staff, event):
        await make_user(discord_id=16, username='fresh')
        client.set([mock_purchase(100, 'Fresh', auth_provider='discord', auth_id='16')])

        await service.sync_event(staff, event.id)

        log = await AuditLog.get(action='user.profile_updated')
        details = json.loads(log.details) if isinstance(log.details, str) else log.details
        assert details['source'] == 'check_in'
        assert details['changed'] == {'matcherino_user_id': '100', 'matcherino_username': 'Fresh#100'}

    async def test_unlink_forgets_what_the_link_recorded(self, service, client, staff, desk, event):
        wrong = await make_user(discord_id=17, username='wrong')
        await TenantMembership.create(user=wrong, tenant_id=DEFAULT_TEST_TENANT_ID)
        client.set([mock_purchase(100, 'Real Owner', auth_provider='gplus')])
        await service.sync_event(staff, event.id)
        entrant = (await _rows(event))['100']
        await service.link(desk, entrant.id, wrong.id)

        await service.unlink(desk, entrant.id)

        await wrong.refresh_from_db()
        assert (wrong.matcherino_user_id, wrong.matcherino_username) == (None, None)

    async def test_unlink_keeps_a_handle_the_player_typed(self, service, client, staff, desk, event):
        owner = await make_user(discord_id=18, username='owner', matcherino_username='mine#100')
        client.set([mock_purchase(100, 'Owner', auth_provider='discord', auth_id='18')])
        await service.sync_event(staff, event.id)
        entrant = (await _rows(event))['100']

        await service.unlink(desk, entrant.id)

        await owner.refresh_from_db()
        assert owner.matcherino_user_id is None
        assert owner.matcherino_username == 'mine#100'


class TestRaces:
    async def test_two_desks_tapping_one_person_record_one_check_in(self, service, client, staff, desk, event):
        client.set([mock_purchase(100, 'A')])
        await service.sync_event(staff, event.id)
        entrant = (await _rows(event))['100']
        stale = await service.get_entrant(entrant.id)
        await service.check_in(desk, entrant.id)

        original = service.entrants.get_by_id
        calls = {'n': 0}

        async def first_call_stale(entrant_id):
            calls['n'] += 1
            return stale if calls['n'] == 1 else await original(entrant_id)

        service.entrants.get_by_id = first_call_stale
        outcome = await service.check_in(staff, entrant.id)

        assert outcome.already
        assert outcome.entrant.checked_in_by_id == desk.id
        assert await AuditLog.filter(action='check_in_entrant.checked_in').count() == 1

    async def test_a_sync_does_not_revert_an_edit_made_while_it_fetched(self, service, client, staff, event):
        from models import CheckInEvent

        class EditsMidFetch(ScriptedClient):
            async def fetch_sales(self, venue_id):
                await CheckInEvent.filter(id=event.id).update(status=CheckInEventStatus.CLOSED, name='Renamed')
                return await super().fetch_sales(venue_id)

        await CheckInService(client=EditsMidFetch([mock_purchase(100, 'A')])).sync_event(staff, event.id)

        await event.refresh_from_db()
        assert (event.status, event.name) == (CheckInEventStatus.CLOSED, 'Renamed')
        assert event.last_sync_count == 1

    async def test_concurrent_syncs_do_not_collide(self, service, client, staff, event):
        import asyncio

        client.set([mock_purchase(100, 'A'), mock_purchase(200, 'B')])

        results = await asyncio.gather(
            service.sync_event(staff, event.id), service.sync_event(staff, event.id, audit=False),
        )

        assert sorted(r.added for r in results) == [0, 2]
        assert await CheckInEntrant.filter(event=event).count() == 2

    async def test_a_link_made_during_a_sync_is_kept(self, service, client, staff, desk, event):
        await make_user(discord_id=19, username='auto-target')
        chosen = await make_user(discord_id=20, username='chosen')
        await TenantMembership.create(user=chosen, tenant_id=DEFAULT_TEST_TENANT_ID)
        client.set([mock_purchase(100, 'A', auth_provider='gplus')])
        await service.sync_event(staff, event.id)
        entrant = (await _rows(event))['100']

        class LinksMidFetch(ScriptedClient):
            async def fetch_sales(self, venue_id):
                await service.link(desk, entrant.id, chosen.id)
                return await super().fetch_sales(venue_id)

        await CheckInService(client=LinksMidFetch(
            [mock_purchase(100, 'A', auth_provider='discord', auth_id='19')],
        )).sync_event(staff, event.id)

        assert (await _rows(event))['100'].user_id == chosen.id


class TestSyncRoster:
    async def test_a_repeat_sync_changes_nothing(self, service, client, staff, event):
        client.set([mock_purchase(100, 'A'), mock_purchase(200, 'B')])
        await service.sync_event(staff, event.id)

        again = await service.sync_event(staff, event.id)

        assert (again.added, again.updated, again.withdrawn) == (0, 0, 0)
        assert await CheckInEntrant.filter(event=event).count() == 2

    async def test_leaving_withdraws_and_rejoining_restores(self, service, client, staff, event):
        client.set([mock_purchase(100, 'Stays'), mock_purchase(200, 'Leaves')])
        await service.sync_event(staff, event.id)

        client.set([mock_purchase(100, 'Stays')])
        left = await service.sync_event(staff, event.id)
        assert left.withdrawn == 1
        assert (await _rows(event))['200'].withdrawn_at is not None

        client.set([mock_purchase(100, 'Stays'), mock_purchase(200, 'Leaves')])
        back = await service.sync_event(staff, event.id)
        assert back.rejoined == 1
        assert (await _rows(event))['200'].withdrawn_at is None

    async def test_profile_changes_are_picked_up(self, service, client, staff, event):
        client.set([mock_purchase(100, 'Old Name')])
        await service.sync_event(staff, event.id)
        client.set([mock_purchase(100, 'New Name')])

        result = await service.sync_event(staff, event.id)

        row = (await _rows(event))['100']
        assert result.updated == 1
        assert row.display_name == 'New Name'
        assert row.source_data['displayName'] == 'New Name'

    async def test_an_empty_response_keeps_the_roster(self, service, client, staff, event):
        client.set([mock_purchase(100, 'A')])
        await service.sync_event(staff, event.id)
        client.set([])

        with pytest.raises(ValueError, match="roster wasn't changed"):
            await service.sync_event(staff, event.id)

        await event.refresh_from_db()
        assert (await _rows(event))['100'].withdrawn_at is None
        assert 'no badges' in event.last_sync_error

    async def test_an_api_failure_keeps_the_roster_and_says_so(self, service, client, staff, event):
        client.set([mock_purchase(100, 'A')])
        await service.sync_event(staff, event.id)
        client.error = MatcherinoAPIError('shape changed')

        with pytest.raises(ValueError, match='shape changed'):
            await service.sync_event(staff, event.id)

        await event.refresh_from_db()
        assert event.last_sync_error == 'shape changed'
        from datetime import datetime, timezone

        from application.services.check_in_sync_worker import _due
        assert not _due(event, datetime.now(timezone.utc))
        assert (await _rows(event))['100'].withdrawn_at is None

        client.error = None
        await service.sync_event(staff, event.id)
        await event.refresh_from_db()
        assert event.last_sync_error is None

    async def test_a_manual_sync_is_audited_and_a_poll_is_not(self, service, client, staff, event):
        client.set([mock_purchase(100, 'A')])
        await service.sync_event(staff, event.id)
        await service.sync_event(staff, event.id, audit=False)

        assert await AuditLog.filter(action='check_in_event.synced').count() == 1

    async def test_an_event_with_no_venue_or_comps_cannot_sync(self, service, staff):
        walkups_only = await service.create_event(staff, 'Walk-ups only')
        with pytest.raises(ValueError, match='nothing to sync'):
            await service.sync_event(staff, walkups_only.id)

    async def test_a_sync_tells_open_desks(self, service, client, staff, event, monkeypatch):
        seen = []
        monkeypatch.setattr(check_in_live, '_subscribers', {1: lambda *a: seen.append(a)})
        client.set([mock_purchase(100, 'A')])

        await service.sync_event(staff, event.id)

        assert seen[-1] == (event.id, None, check_in_live.ROSTER)


class TestDesk:
    async def _entrant(self, service, client, staff, event, user_id=100, name='A'):
        client.set([mock_purchase(user_id, name)])
        await service.sync_event(staff, event.id)
        return (await _rows(event))[str(user_id)]

    async def test_check_in_twice_reports_who_did_it(self, service, client, staff, desk, event):
        entrant = await self._entrant(service, client, staff, event)

        first = await service.check_in(desk, entrant.id)
        second = await service.check_in(staff, entrant.id)

        assert not first.already and second.already
        assert second.entrant.checked_in_by_id == desk.id
        assert await AuditLog.filter(action='check_in_entrant.checked_in').count() == 1

    async def test_undo_clears_the_check_in(self, service, client, staff, desk, event):
        entrant = await self._entrant(service, client, staff, event)
        await service.check_in(desk, entrant.id)

        undone = await service.undo_check_in(desk, entrant.id)

        assert undone.checked_in_at is None and undone.checked_in_by_id is None

    async def _refunded(self, service, client, staff, event):
        client.set([mock_purchase(100, 'Stays'), mock_purchase(200, 'Refunded')])
        await service.sync_event(staff, event.id)
        client.set([mock_purchase(100, 'Stays')])
        await service.sync_event(staff, event.id)
        return (await _rows(event))['200']

    async def test_a_withdrawn_buyer_cannot_be_checked_in_at_the_desk(self, service, client, staff, desk, event):
        refunded = await self._refunded(service, client, staff, event)

        with pytest.raises(ValueError, match='Only staff'):
            await service.check_in(desk, refunded.id)
        with pytest.raises(ValueError, match='Only staff'):
            await service.check_in(staff, refunded.id)
        with pytest.raises(PermissionError):
            await service.check_in(desk, refunded.id, despite_withdrawn=True)

        assert (await _rows(event))['200'].checked_in_at is None

    async def test_staff_can_check_in_a_withdrawn_buyer_anyway(self, service, client, staff, event):
        refunded = await self._refunded(service, client, staff, event)

        outcome = await service.check_in(staff, refunded.id, despite_withdrawn=True)

        assert not outcome.already
        assert (await _rows(event))['200'].checked_in_by_id == staff.id
        log = await AuditLog.get(action='check_in_entrant.checked_in')
        assert json.loads(log.details)['despite_withdrawn'] is True

    async def test_a_withdrawal_racing_the_tap_is_refused(self, service, client, staff, desk, event):
        entrant = await self._entrant(service, client, staff, event)
        await CheckInEntrant.filter(id=entrant.id).update(withdrawn_at=entrant.created_at)
        entrant.withdrawn_at = None

        assert not await service.entrants.claim_check_in(entrant, entrant.created_at, desk)

    async def test_a_member_without_the_role_cannot_work_the_desk(self, service, client, staff, event):
        entrant = await self._entrant(service, client, staff, event)
        member = await make_user(discord_id=60, username='member')
        with pytest.raises(PermissionError):
            await service.check_in(member, entrant.id)
        with pytest.raises(PermissionError):
            await service.sync_event(member, event.id)

    async def test_manual_link_is_remembered_for_the_next_event(self, service, client, staff, desk, event):
        entrant = await self._entrant(service, client, staff, event, name='Gplus Person')
        person = await make_user(discord_id=70, username='person')
        await TenantMembership.create(user=person, tenant_id=DEFAULT_TEST_TENANT_ID)

        await service.link(desk, entrant.id, person.id)

        await entrant.refresh_from_db()
        await person.refresh_from_db()
        assert (entrant.user_id, entrant.link_method, entrant.linked_by_id) == (
            person.id, CheckInLinkMethod.MANUAL, desk.id)
        assert person.matcherino_user_id == '100'

        next_event = await service.create_event(staff, 'SGL 2027', venue_id=VENUE + 1)
        await service.sync_event(staff, next_event.id)
        assert (await _rows(next_event))['100'].link_method == CheckInLinkMethod.MATCHERINO_ID

    async def test_link_refuses_someone_already_on_the_roster(self, service, client, staff, desk, event):
        client.set([mock_purchase(100, 'A', auth_provider='discord', auth_id='80'),
                    mock_purchase(200, 'B')])
        already = await make_user(discord_id=80, username='already')
        await TenantMembership.create(user=already, tenant_id=DEFAULT_TEST_TENANT_ID)
        await service.sync_event(staff, event.id)
        rows = await _rows(event)

        with pytest.raises(ValueError, match='already on this roster'):
            await service.link(desk, rows['200'].id, rows['100'].user_id)

    async def test_link_refuses_a_matcherino_account_held_by_someone_else(
        self, service, client, staff, desk, event,
    ):
        entrant = await self._entrant(service, client, staff, event)
        await make_user(discord_id=81, username='holder', matcherino_user_id='100')
        other = await make_user(discord_id=82, username='other')
        await TenantMembership.create(user=other, tenant_id=DEFAULT_TEST_TENANT_ID)

        with pytest.raises(ValueError, match='already linked to holder'):
            await service.link(desk, entrant.id, other.id)

    async def test_unlink_stops_the_sync_relinking(self, service, client, staff, desk, event):
        await make_user(discord_id=90, username='wrong')
        client.set([mock_purchase(100, 'A', auth_provider='discord', auth_id='90')])
        await service.sync_event(staff, event.id)
        entrant = (await _rows(event))['100']
        assert entrant.user_id is not None

        await service.unlink(desk, entrant.id)
        await service.sync_event(staff, event.id)

        entrant = (await _rows(event))['100']
        assert entrant.user_id is None
        assert entrant.link_method == CheckInLinkMethod.MANUAL

    async def test_suggestions_rank_similar_members(self, service, client, staff, event):
        entrant = await self._entrant(service, client, staff, event, name='Pat_Example')
        close = await make_user(discord_id=91, username='patexample')
        far = await make_user(discord_id=92, username='zzz')
        for user in (close, far):
            await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)

        suggestions = await service.suggest_users(await service.get_entrant(entrant.id))

        assert [u.id for u in suggestions] == [close.id]

    async def test_suggestion_counts_match_what_the_link_dialog_offers(self, service, client, staff, event):
        for discord_id, username in ((91, 'patexample'), (92, 'pat_exampl'), (95, 'linked')):
            user = await make_user(discord_id=discord_id, username=username)
            await TenantMembership.create(user=user, tenant_id=DEFAULT_TEST_TENANT_ID)
        client.set([
            mock_purchase(100, 'Pat_Example'), mock_purchase(101, 'Nobody Like Them'),
            mock_purchase(102, 'linked', auth_provider='discord', auth_id='95'),
        ])
        await service.sync_event(staff, event.id)
        rows = await _rows(event)

        counts = await service.suggestion_counts(event, await service.roster(event))

        pat = await service.get_entrant(rows['100'].id)
        assert counts[pat.id] == len(await service.suggest_users(pat)) == 2
        assert counts[rows['101'].id] == 0
        assert rows['102'].id not in counts

    async def test_member_search_skips_people_already_on_the_roster(self, service, client, staff, event):
        linked = await make_user(discord_id=94, username='needle_linked')
        await TenantMembership.create(user=linked, tenant_id=DEFAULT_TEST_TENANT_ID)
        client.set([mock_purchase(100, 'A', auth_provider='discord', auth_id='94')])
        await service.sync_event(staff, event.id)
        found = await make_user(discord_id=93, username='needle_person')
        await TenantMembership.create(user=found, tenant_id=DEFAULT_TEST_TENANT_ID)

        results = await service.search_members(event, 'needle')

        assert [u.id for u in results] == [found.id]


class TestWalkUps:
    async def test_staff_add_a_named_walk_up_checked_in(self, service, staff, event):
        entrant = await service.add_walk_up(staff, event.id, name='  Walk In  ')

        assert entrant.source == CheckInEntrantSource.WALK_UP
        assert entrant.display_name == 'Walk In'
        assert entrant.checked_in_by_id == staff.id

    async def test_a_member_walk_up_is_linked(self, service, staff, event):
        member = await make_user(discord_id=95, username='member', display_name='Member Name')
        await TenantMembership.create(user=member, tenant_id=DEFAULT_TEST_TENANT_ID)

        entrant = await service.add_walk_up(staff, event.id, user_id=member.id, check_in=False)

        assert (entrant.user_id, entrant.display_name, entrant.checked_in_at) == (member.id, 'Member Name', None)
        assert entrant.link_method == CheckInLinkMethod.WALK_UP

    async def test_desk_volunteers_cannot_add_or_remove_walk_ups(self, service, staff, desk, event):
        with pytest.raises(PermissionError):
            await service.add_walk_up(desk, event.id, name='Nope')
        entrant = await service.add_walk_up(staff, event.id, name='Typo')
        with pytest.raises(PermissionError):
            await service.remove_entrant(desk, entrant.id)

    async def test_a_walk_up_needs_a_name_or_member(self, service, staff, event):
        with pytest.raises(ValueError, match='Pick a member'):
            await service.add_walk_up(staff, event.id, name='  ')

    async def test_only_walk_ups_can_be_removed(self, service, client, staff, event):
        walk_up = await service.add_walk_up(staff, event.id, name='Typo')
        await service.remove_entrant(staff, walk_up.id)
        assert not await CheckInEntrant.exists(id=walk_up.id)

        client.set([mock_purchase(100, 'Registered')])
        await service.sync_event(staff, event.id)
        with pytest.raises(ValueError, match='Refund their badge'):
            await service.remove_entrant(staff, (await _rows(event))['100'].id)


class TestEvents:
    async def test_a_venue_belongs_to_one_event(self, service, staff, event):
        with pytest.raises(ValueError, match='already used'):
            await service.create_event(staff, 'Again', venue_id=VENUE)

    async def test_a_live_rosters_venue_cannot_change(self, service, client, staff, event):
        client.set([mock_purchase(100, 'A')])
        await service.sync_event(staff, event.id)

        with pytest.raises(ValueError, match='Create a new event'):
            await service.update_event(staff, event.id, 'SGL 2026', VENUE + 5, CheckInEventStatus.OPEN, 5)

    async def test_desk_volunteers_cannot_set_up_events(self, service, desk):
        with pytest.raises(PermissionError):
            await service.create_event(desk, 'Nope')

    async def test_update_validates_and_audits(self, service, staff, event):
        with pytest.raises(ValueError, match='Sync every'):
            await service.update_event(staff, event.id, 'SGL', VENUE, CheckInEventStatus.OPEN, 0)

        await service.update_event(staff, event.id, 'SGL 2026', VENUE, CheckInEventStatus.CLOSED, 5)

        await event.refresh_from_db()
        assert event.status == CheckInEventStatus.CLOSED
        assert await AuditLog.filter(action='check_in_event.updated').count() == 1

    async def test_delete_removes_the_roster(self, service, client, staff, event):
        client.set([mock_purchase(100, 'A')])
        await service.sync_event(staff, event.id)

        await service.delete_event(staff, event.id)

        assert await CheckInEntrant.all().count() == 0

    async def test_preview_reports_a_missing_venue(self, service, staff):
        class Missing(MockMatcherinoClient):
            async def fetch_venue(self, venue_id):
                raise MatcherinoAPIError('Matcherino has no venue 5.')

        with pytest.raises(ValueError, match="Couldn't read that venue"):
            await CheckInService(client=Missing()).preview_venue(staff, 5)

    async def test_preview_lists_badge_types_dearest_first(self, service, staff):
        preview = await service.preview_venue(staff, VENUE)

        assert preview.venue.title == 'Mock Matcherino Venue'
        assert [t.title for t in preview.tiers][:2] == ['Super VIP Tier Badge', 'VIP Tier Badge']

    async def test_the_flag_being_off_hides_everything(self, service, staff):
        await TenantFeatureFlag.filter(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.EVENT_CHECK_IN.value,
        ).update(enabled=False)
        reset_flag_cache()

        with pytest.raises(FeatureDisabledError):
            await service.list_events()
        with pytest.raises(FeatureDisabledError):
            await service.create_event(staff, 'Hidden')


async def test_summary_counts_withdrawn_rows_separately(service, staff, client, event):
    client.set([mock_purchase(100, 'Here'), mock_purchase(200, 'Gone')])
    await service.sync_event(staff, event.id)
    client.set([mock_purchase(100, 'Here')])
    await service.sync_event(staff, event.id)
    await service.add_walk_up(staff, event.id, name='Walk')

    counts = summarize(await service.roster(event))

    assert (counts.total, counts.checked_in, counts.not_yet, counts.withdrawn, counts.walk_ups,
            counts.unlinked) == (2, 1, 1, 1, 1, 2)


def test_handle_id_reads_the_trailing_number():
    assert handle_id('jemgold#100234') == '100234'
    assert handle_id('name # 42 ') == '42'
    assert handle_id('no id here') is None
    assert handle_id(None) is None


class TestWorker:
    async def test_the_tick_syncs_due_open_events(self, service, staff, event, monkeypatch):
        from application.services import check_in_sync_worker

        monkeypatch.setattr(
            'application.services.check_in_service.get_matcherino_client',
            lambda refresh_token: MockMatcherinoClient([mock_purchase(100, 'Polled')]),
        )
        await check_in_sync_worker._tick()

        assert await CheckInEntrant.filter(event=event).count() == 1
        assert await AuditLog.filter(action='check_in_event.synced').count() == 0

    async def test_the_tick_skips_a_tenant_with_check_in_off(self, service, staff, event, monkeypatch):
        from application.services import check_in_sync_worker

        await TenantFeatureFlag.filter(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.EVENT_CHECK_IN.value,
        ).update(enabled=False)
        reset_flag_cache()
        monkeypatch.setattr(
            'application.services.check_in_service.get_matcherino_client',
            lambda refresh_token: MockMatcherinoClient([mock_purchase(100, 'Polled')]),
        )
        await check_in_sync_worker._tick()

        assert await CheckInEntrant.filter(event=event).count() == 0

    async def test_a_refused_login_logs_at_info_and_other_failures_escalate(
        self, staff, event, monkeypatch, caplog,
    ):
        import logging

        from application.services import check_in_sync_worker
        from application.utils.clients.matcherino_client import MatcherinoAuthError

        failing = ScriptedClient([])
        failing.error = MatcherinoAuthError('Matcherino refused the saved login')
        monkeypatch.setattr(
            'application.services.check_in_service.get_matcherino_client', lambda refresh_token: failing,
        )
        monkeypatch.setattr(check_in_sync_worker, '_due', lambda event, now, badge_changed=None: True)

        def levels():
            return [r.levelno for r in caplog.records if r.name == check_in_sync_worker.__name__]

        with caplog.at_level(logging.INFO, logger=check_in_sync_worker.__name__):
            await check_in_sync_worker._tick()
            await check_in_sync_worker._tick()
            assert levels() == [logging.INFO, logging.INFO]

            failing.error = MatcherinoAPIError('shape changed')
            await check_in_sync_worker._tick()
            await check_in_sync_worker._tick()
            assert levels()[-2:] == [logging.ERROR, logging.WARNING]

        await event.refresh_from_db()
        assert event.last_sync_error == 'shape changed'

    async def test_an_event_is_not_due_inside_its_interval(self, event):
        from application.services.check_in_sync_worker import _due
        from tests.factories import utc

        now = utc(2026, 10, 5, 12, 0)
        event.last_synced_at = now - timedelta(minutes=2)
        event.last_sync_error = None
        assert not _due(event, now)
        event.last_synced_at = now - timedelta(minutes=6)
        assert _due(event, now)
