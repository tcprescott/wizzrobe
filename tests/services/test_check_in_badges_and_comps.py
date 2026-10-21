"""CheckInService: badges and tiers from venue sales, the volunteer chip, and comps."""

# ruff: noqa: F811 — the shared fixtures are imported from check_in_support, and
# pytest injects them as same-named test arguments.

import json
from datetime import timedelta

import pytest

from application.services.check_in_rules import active_badges, entrant_filters, lanyard_for, summarize
from application.services.check_in_sales import guess_lanyard
from application.services.feature_flag_service import reset_flag_cache
from application.utils.clients.matcherino_client import mock_purchase, mock_tier
from models import (
    AuditLog,
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEventStatus,
    CheckInLinkMethod,
    CheckInPass,
    CheckInTier,
    CheckInTierLanyard,
    FeatureFlag,
    Role,
    TenantFeatureFlag,
    TenantMembership,
    UserRole,
)
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user
from tests.services.check_in_support import (  # noqa: F401 — fixtures
    VENUE,
    client,
    desk,
    event,
    service,
    staff,
)
from tests.services.check_in_support import (
    rows as _rows,
)
from tests.services.check_in_support import (
    with_role as _with_role,
)

BASE = mock_tier(1, 'Base', 8000)
VIP = mock_tier(2, 'VIP', 11000)


async def _badges(event):
    return {b.matcherino_purchase_id: b for b in await CheckInPass.filter(event=event).prefetch_related('tier')}


class TestBadges:
    async def test_each_purchase_is_a_badge_under_its_tier(self, service, client, staff, event):
        client.set([
            mock_purchase(100, 'Two Badges', tier=BASE, purchase_id=1, code=11111111,
                          purchased_at='2026-03-01T00:00:00Z'),
            mock_purchase(100, 'Two Badges', tier=VIP, purchase_id=2, code=22222222,
                          purchased_at='2026-02-01T00:00:00Z'),
            mock_purchase(200, 'One Badge', tier=BASE, purchase_id=3),
        ], tiers=[BASE, VIP])

        result = await service.sync_event(staff, event.id)

        assert (result.total, result.badges, result.added) == (2, 3, 2)
        rows = await _rows(event)
        badges = await _badges(event)
        assert {b.entrant_id for b in badges.values()} == {rows['100'].id, rows['200'].id}
        assert (badges[1].code, badges[1].tier.title, badges[2].tier.title) == ('11111111', 'Base', 'VIP')
        assert rows['100'].registered_at.month == 2
        assert {t.title: t.amount_cents for t in await CheckInTier.filter(event=event)} == {
            'Base': 8000, 'VIP': 11000}

        entrant = await service.get_entrant(rows['100'].id)
        held = active_badges(entrant.passes)
        assert [b.tier.title for b in held] == ['VIP', 'Base']
        assert lanyard_for(entrant, held) == 'vip'

    async def test_buyer_contact_details_are_not_stored(self, service, client, staff, event):
        raw = mock_purchase(100, 'Private')
        raw.update(email='person@example.com', phone='555-0100', firstName='Pat', address1='1 Main St')
        raw['transactions'] = [{'amount': 8000}]
        raw['user']['email'] = 'person@example.com'
        client.set([raw])

        await service.sync_event(staff, event.id)

        badge = (await CheckInPass.filter(event=event))[0]
        entrant = (await _rows(event))['100']
        stored = json.dumps([badge.source_data, entrant.source_data])
        assert 'example.com' not in stored and '555-0100' not in stored and 'Main St' not in stored
        assert 'transactions' not in badge.source_data

    async def test_a_purchase_listed_twice_syncs_as_one_badge(self, service, client, staff, event):
        twice = mock_purchase(100, 'Twice', tier=BASE, purchase_id=1)
        client.set([twice, twice], tiers=[BASE])

        result = await service.sync_event(staff, event.id)

        assert (result.total, result.badges) == (1, 1)
        assert await CheckInPass.filter(event=event).count() == 1

    async def test_a_refund_withdraws_a_buyer_with_nothing_left(self, service, client, staff, event):
        client.set([mock_purchase(100, 'Refunded', purchase_id=1),
                    mock_purchase(200, 'Keeps one', purchase_id=2),
                    mock_purchase(200, 'Keeps one', purchase_id=3, tier=VIP)], tiers=[BASE, VIP])
        await service.sync_event(staff, event.id)

        client.set([mock_purchase(100, 'Refunded', purchase_id=1, refunded_at='2026-09-20T10:00:00Z'),
                    mock_purchase(200, 'Keeps one', purchase_id=2),
                    mock_purchase(200, 'Keeps one', purchase_id=3, tier=VIP,
                                  refunded_at='2026-09-20T10:00:00Z')], tiers=[BASE, VIP])
        result = await service.sync_event(staff, event.id)

        rows = await _rows(event)
        badges = await _badges(event)
        assert result.withdrawn == 1
        assert rows['100'].withdrawn_at is not None and rows['200'].withdrawn_at is None
        assert badges[1].refunded_at is not None and badges[3].refunded_at is not None

    async def test_a_purchase_that_leaves_the_feed_is_removed_not_deleted(self, service, client, staff, event):
        client.set([mock_purchase(100, 'A', purchase_id=1), mock_purchase(200, 'B', purchase_id=2)])
        await service.sync_event(staff, event.id)

        client.set([mock_purchase(100, 'A', purchase_id=1)])
        await service.sync_event(staff, event.id)

        badges = await _badges(event)
        assert badges[2].removed_at is not None and badges[1].removed_at is None
        assert (await _rows(event))['200'].withdrawn_at is not None

    async def test_a_buyer_refunded_before_the_first_sync_arrives_withdrawn(self, service, client, staff, event):
        client.set([mock_purchase(100, 'Too late', refunded_at='2026-09-20T10:00:00Z')])

        result = await service.sync_event(staff, event.id)

        assert (result.total, result.badges, result.added) == (0, 0, 1)
        assert (await _rows(event))['100'].withdrawn_at is not None

    async def test_a_renamed_tier_is_updated(self, service, client, staff, event):
        client.set([mock_purchase(100, 'A', tier=BASE)], tiers=[BASE])
        await service.sync_event(staff, event.id)

        renamed = mock_tier(1, 'Base Badge', 9000)
        client.set([mock_purchase(100, 'A', tier=renamed)], tiers=[renamed])
        await service.sync_event(staff, event.id)

        tier = await CheckInTier.get(event=event)
        assert (tier.title, tier.amount_cents) == ('Base Badge', 9000)

    async def test_a_tier_deleted_from_the_venue_still_resolves(self, service, client, staff, event):
        gone = mock_tier(9, 'Early Bird', 6000)
        client.set([mock_purchase(100, 'Early', tier=gone)], tiers=[BASE])

        await service.sync_event(staff, event.id)

        badge = (await _badges(event))[50_100]
        assert badge.tier.title == 'Early Bird'


class TestVolunteers:
    async def _shift(self):
        from datetime import datetime, timezone

        from models import VolunteerPosition, VolunteerShift
        position = await VolunteerPosition.create(name='Check-in Desk', tenant_id=DEFAULT_TEST_TENANT_ID)
        start = datetime(2026, 10, 10, 9, tzinfo=timezone.utc)
        return await VolunteerShift.create(position=position, starts_at=start, ends_at=start + timedelta(hours=4),
                                           tenant_id=DEFAULT_TEST_TENANT_ID)

    async def test_a_published_assignment_marks_a_volunteer_and_a_draft_does_not(self, service, staff):
        from models import VolunteerAssignment

        shift = await self._shift()
        published = await make_user(discord_id=301, username='published')
        drafted = await make_user(discord_id=302, username='drafted')
        await VolunteerAssignment.create(shift=shift, user=published, tenant_id=DEFAULT_TEST_TENANT_ID)
        await VolunteerAssignment.create(shift=shift, user=drafted, auto_generated=True,
                                         tenant_id=DEFAULT_TEST_TENANT_ID)

        assert await service.volunteer_user_ids() == {published.id}

    async def test_volunteering_off_marks_nobody_and_does_not_raise(self, service, staff):
        from models import VolunteerAssignment

        shift = await self._shift()
        person = await make_user(discord_id=303, username='person')
        await VolunteerAssignment.create(shift=shift, user=person, tenant_id=DEFAULT_TEST_TENANT_ID)
        await TenantFeatureFlag.filter(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.VOLUNTEERS.value,
        ).update(enabled=False)
        reset_flag_cache()

        assert await service.volunteer_user_ids() == set()

    def test_any_volunteer_assignment_earns_the_volunteer_lanyard(self):
        entrant = CheckInEntrant(source=CheckInEntrantSource.MATCHERINO, display_name='x', user_id=5)
        assert lanyard_for(entrant, volunteer=True) == 'volunteer'
        assert lanyard_for(entrant) is None


class TestComps:
    """Comp rules: roles and volunteer hours put people on the roster without a badge."""

    @pytest.fixture
    async def window(self, db):
        from models import SystemConfiguration
        for key, value in (('event_start_date', '2026-10-09'), ('event_end_date', '2026-10-11'),
                           ('volunteer_comp_tiers', '8, 12, 16')):
            await SystemConfiguration.create(name=key, value=value, tenant_id=DEFAULT_TEST_TENANT_ID)

    async def _hours(self, user, hours, *, auto_generated=False):
        from datetime import datetime, timezone

        from models import VolunteerAssignment, VolunteerPosition, VolunteerShift
        position, _ = await VolunteerPosition.get_or_create(name='Desk', tenant_id=DEFAULT_TEST_TENANT_ID)
        start = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)
        shift = await VolunteerShift.create(position=position, starts_at=start,
                                            ends_at=start + timedelta(hours=hours),
                                            tenant_id=DEFAULT_TEST_TENANT_ID)
        await VolunteerAssignment.create(shift=shift, user=user, auto_generated=auto_generated,
                                         tenant_id=DEFAULT_TEST_TENANT_ID)

    async def _comp_event(self, service, staff, **kw):
        return await service.create_event(staff, 'Comps', comp_roles=[Role.STAFF], **kw)

    async def _by_user(self, event):
        return {row.user_id: row for row in await CheckInEntrant.filter(event=event) if row.user_id}

    async def test_a_comped_role_gets_a_comp_row_without_a_venue(self, service, staff):
        event = await self._comp_event(service, staff)

        result = await service.sync_event(staff, event.id)

        row = (await self._by_user(event))[staff.id]
        assert (row.source, row.link_method, row.comp_reasons) == (
            CheckInEntrantSource.COMP, CheckInLinkMethod.COMP, ['staff'])
        assert (result.added, result.comped) == (1, 1)
        assert lanyard_for(row) == 'staff'

    async def test_volunteers_at_the_lowest_tier_are_comped_and_drafts_do_not_count(
        self, service, staff, window,
    ):
        enough = await make_user(discord_id=401, username='enough')
        short = await make_user(discord_id=402, username='short')
        drafted = await make_user(discord_id=403, username='drafted')
        await self._hours(enough, 8)
        await self._hours(short, 7.5)
        await self._hours(drafted, 9, auto_generated=True)
        event = await service.create_event(staff, 'Vols', comp_volunteers=True)

        await service.sync_event(staff, event.id)

        rows = await self._by_user(event)
        assert set(rows) == {enough.id}
        assert rows[enough.id].comp_reasons == ['volunteer']

    async def test_volunteering_off_comps_no_volunteers(self, service, staff, window):
        person = await make_user(discord_id=404, username='person')
        await self._hours(person, 10)
        await TenantFeatureFlag.filter(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=FeatureFlag.VOLUNTEERS.value,
        ).update(enabled=False)
        reset_flag_cache()
        event = await service.create_event(staff, 'Vols', comp_volunteers=True)

        await service.sync_event(staff, event.id)

        assert await self._by_user(event) == {}

    async def test_losing_the_role_withdraws_and_regaining_restores(self, service, staff, desk):
        comped = await _with_role(410, 'comped', Role.STAFF)
        event = await self._comp_event(service, staff)
        await service.sync_event(staff, event.id)

        await UserRole.filter(user=comped, role=Role.STAFF).delete()
        lost = await service.sync_event(staff, event.id)
        row = (await self._by_user(event))[comped.id]
        assert lost.withdrawn == 1 and row.withdrawn_at is not None and row.comp_reasons is None

        await UserRole.create(user=comped, role=Role.STAFF, tenant_id=DEFAULT_TEST_TENANT_ID)
        back = await service.sync_event(staff, event.id)
        assert back.rejoined == 1
        assert (await self._by_user(event))[comped.id].withdrawn_at is None

    async def test_a_comped_buyer_keeps_one_row_and_survives_a_refund(self, service, client, staff):
        buyer = await _with_role(420, 'buyer', Role.STAFF)
        event = await service.create_event(staff, 'Both', venue_id=VENUE, comp_roles=[Role.STAFF])
        client.set([mock_purchase(100, 'Buyer', auth_provider='discord', auth_id='420')])
        await service.sync_event(staff, event.id)

        client.set([mock_purchase(100, 'Buyer', auth_provider='discord', auth_id='420',
                                  refunded_at='2026-09-20T10:00:00Z')])
        await service.sync_event(staff, event.id)

        rows = [r for r in await CheckInEntrant.filter(event=event) if r.user_id == buyer.id]
        assert len(rows) == 1
        assert rows[0].source == CheckInEntrantSource.MATCHERINO
        assert rows[0].comp_reasons == ['staff'] and rows[0].withdrawn_at is None

    async def test_a_comp_who_buys_later_merges_into_the_purchase(self, service, client, staff, desk):
        buyer = await _with_role(430, 'later', Role.STAFF)
        event = await service.create_event(
            staff, 'Later', venue_id=VENUE, comp_roles=[Role.STAFF], status=CheckInEventStatus.OPEN,
        )
        await service.sync_event(staff, event.id)
        comp_row = (await self._by_user(event))[buyer.id]
        await service.check_in(desk, comp_row.id)

        client.set([mock_purchase(100, 'Later', auth_provider='discord', auth_id='430')])
        await service.sync_event(staff, event.id)

        rows = [r for r in await CheckInEntrant.filter(event=event) if r.user_id == buyer.id]
        assert len(rows) == 1
        row = rows[0]
        assert (row.source, row.matcherino_user_id, row.comp_reasons) == (
            CheckInEntrantSource.MATCHERINO, '100', ['staff'])
        assert row.checked_in_by_id == desk.id

    async def test_a_manual_link_merges_a_comp_row(self, service, client, staff, desk):
        comped = await _with_role(440, 'manual', Role.STAFF)
        await TenantMembership.create(user=comped, tenant_id=DEFAULT_TEST_TENANT_ID)
        event = await service.create_event(
            staff, 'Manual', venue_id=VENUE, comp_roles=[Role.STAFF], status=CheckInEventStatus.OPEN,
        )
        client.set([mock_purchase(100, 'Gplus Name')])
        await service.sync_event(staff, event.id)
        purchase_row = (await _rows(event))['100']

        await service.link(desk, purchase_row.id, comped.id)

        rows = [r for r in await CheckInEntrant.filter(event=event) if r.user_id == comped.id]
        assert [r.id for r in rows] == [purchase_row.id]
        assert rows[0].comp_reasons == ['staff']

    async def test_the_link_dialog_offers_a_comp_but_the_walk_up_dialog_does_not(
        self, service, client, staff, desk,
    ):
        comped = await _with_role(445, 'needle_volunteer', Role.STAFF)
        await TenantMembership.create(user=comped, tenant_id=DEFAULT_TEST_TENANT_ID)
        event = await service.create_event(staff, 'Offer', venue_id=VENUE, comp_roles=[Role.STAFF])
        client.set([mock_purchase(100, 'Needle Volunteer')])
        await service.sync_event(staff, event.id)
        purchase_row = await service.get_entrant((await _rows(event))['100'].id)

        assert comped.id in [u.id for u in await service.search_members(event, 'needle', for_link=True)]
        assert comped.id in [u.id for u in await service.suggest_users(purchase_row)]
        assert comped.id not in [u.id for u in await service.search_members(event, 'needle')]

    async def test_the_comped_filter_covers_comp_rows_and_comped_buyers(self, service, client, staff):
        buyer = await _with_role(446, 'comped_buyer', Role.STAFF)
        event = await service.create_event(staff, 'Filter', venue_id=VENUE, comp_roles=[Role.STAFF])
        client.set([
            mock_purchase(100, 'Comped Buyer', tier=VIP, auth_provider='discord', auth_id='446'),
            mock_purchase(200, 'Plain Buyer', tier=BASE),
        ])
        await service.sync_event(staff, event.id)
        await service.add_walk_up(staff, event.id, name='Walk In')

        roster = await service.roster(event)
        comped = {r.display_name for r in roster if 'comped' in entrant_filters(r)}
        counts = summarize(roster)

        assert comped == {'staff', 'Comped Buyer'}
        assert (counts.comped, counts.walk_ups) == (2, 1)
        assert buyer.id in {r.user_id for r in roster}

    async def test_a_comp_row_cannot_be_relinked_unlinked_or_removed(self, service, staff, desk):
        event = await self._comp_event(service, staff, status=CheckInEventStatus.OPEN)
        await service.sync_event(staff, event.id)
        row = (await self._by_user(event))[staff.id]
        other = await make_user(discord_id=450, username='other')
        await TenantMembership.create(user=other, tenant_id=DEFAULT_TEST_TENANT_ID)

        with pytest.raises(ValueError, match='comp rules'):
            await service.link(desk, row.id, other.id)
        with pytest.raises(ValueError, match='comp rules'):
            await service.unlink(desk, row.id)
        with pytest.raises(ValueError, match='comp rules'):
            await service.remove_entrant(staff, row.id)

    async def test_only_community_roles_can_be_comped(self, service, staff):
        with pytest.raises(ValueError, match="can't be comped"):
            await service.create_event(staff, 'Bad', comp_roles=[Role.SUPER_ADMIN])
        with pytest.raises(ValueError, match="can't be comped"):
            await service.create_event(staff, 'Bad', comp_roles=['nonsense'])

    async def test_the_worker_syncs_a_comps_only_event(self, service, staff):
        from application.services import check_in_sync_worker

        event = await service.create_event(staff, 'Open comps', comp_roles=[Role.STAFF],
                                           status=CheckInEventStatus.OPEN)
        await check_in_sync_worker._tick()

        assert staff.id in await self._by_user(event)


class TestLanyards:
    """One lanyard per person: Staff > Volunteer > VIP > Base > Day Pass."""

    def _badge(self, lanyard, *, refunded=False):
        """A stand-in badge: the rule reads only the tier's lanyard and the two dates."""
        from datetime import datetime, timezone
        from types import SimpleNamespace
        return SimpleNamespace(
            tier=SimpleNamespace(lanyard=lanyard), removed_at=None,
            refunded_at=datetime(2026, 9, 1, tzinfo=timezone.utc) if refunded else None,
        )

    def _entrant(self, comp_reasons=None):
        return CheckInEntrant(source=CheckInEntrantSource.MATCHERINO, display_name='x', user_id=5,
                              comp_reasons=comp_reasons)

    def test_precedence(self):
        vip, base, day = (self._badge(lanyard) for lanyard in (
            CheckInTierLanyard.VIP, CheckInTierLanyard.BASE, CheckInTierLanyard.DAY_PASS))
        assert lanyard_for(self._entrant(['staff']), [vip], volunteer=True) == 'staff'
        assert lanyard_for(self._entrant(), [vip], volunteer=True) == 'volunteer'
        assert lanyard_for(self._entrant(['volunteer']), [vip]) == 'volunteer'
        assert lanyard_for(self._entrant(), [day, base, vip]) == 'vip'
        assert lanyard_for(self._entrant(), [day, base]) == 'base'
        assert lanyard_for(self._entrant(), [day]) == 'day_pass'

    def test_a_refunded_badge_earns_nothing(self):
        refunded = self._badge(CheckInTierLanyard.VIP, refunded=True)
        assert lanyard_for(self._entrant(), [refunded, self._badge(CheckInTierLanyard.DAY_PASS)]) == 'day_pass'
        assert lanyard_for(self._entrant(), [refunded]) is None

    def test_new_badge_types_get_a_lanyard_guessed_from_their_title(self):
        assert guess_lanyard('SG Live SUPER VIP Tier Badge') == CheckInTierLanyard.VIP
        assert guess_lanyard('SG Live Day Pass') == CheckInTierLanyard.DAY_PASS
        assert guess_lanyard('SG Live Base Tier Badge') == CheckInTierLanyard.BASE

    async def test_staff_set_a_lanyard_and_the_sync_keeps_it(self, service, client, staff, desk, event):
        day = mock_tier(7, 'Day Pass', 4000)
        client.set([mock_purchase(100, 'A', tier=day)], tiers=[day])
        await service.sync_event(staff, event.id)
        tier = await CheckInTier.get(event=event)
        assert tier.lanyard == CheckInTierLanyard.DAY_PASS

        with pytest.raises(PermissionError):
            await service.set_tier_lanyard(desk, tier.id, 'base')
        await service.set_tier_lanyard(staff, tier.id, 'base')
        await service.sync_event(staff, event.id)

        await tier.refresh_from_db()
        assert tier.lanyard == CheckInTierLanyard.BASE
        assert await AuditLog.filter(action='check_in_tier.updated').count() == 1
        with pytest.raises(ValueError, match="isn't a lanyard"):
            await service.set_tier_lanyard(staff, tier.id, 'staff')
