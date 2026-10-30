"""Dev seed for event check-in.

Runs the real ``CheckInService`` sync against the MOCK_MATCHERINO venue
(``MOCK_TIERS`` and ``MOCK_PURCHASES``), so the dev database holds what a sync
actually produces (the ``CheckInTier`` and ``CheckInPass`` rows come from that
sync, not from inserts here) and pressing Sync in dev changes nothing. The sales
are lined up with fixtures (see the comment above ``MOCK_PURCHASES``) so that one
sync yields every link method the sync still produces, all four badge types, a
buyer holding two badges and a refunded buyer; on top of it the desk is put into
the states a volunteer meets:

* ``Player Three`` linked to player_three by hand, several buyers unlinked;
* jemgold and blueshell link to player_one and player_two, whose published
  shifts from ``seed_volunteers`` give them the Volunteer chip; player_three's
  only shift is an unpublished draft, so Player Three gets none;
* the event comps Staff and volunteers: staff_user, who bought no badge, gets
  a ``COMP`` row; player_one (exactly 8 scheduled hours, the lowest comp tier)
  and player_two (over every tier) add a comp to the badges they bought;
  proctor_user, under 8 hours, gets nothing;
* someone whose badge disappeared from the venue (withdrawn);
* player_one already checked in by the check-in desk volunteer;
* two walk-ups checked in by staff, one a member (racer_09) and one name-only;
* racer_05, racer_07 and player_three checked in too, so the check-in test DM
  shows every state: player_one's landed, racer_09's was refused by Discord,
  racer_07's hit a Discord outage, player_three has DMs off, racer_05's is queued;
* a draft event with no venue and last year's closed event, for the admin list;
* a saved Matcherino login whose refused-login alerts go to staff_user, so
  Admin → Check-in shows the configured card. Under MOCK_MATCHERINO it is never
  sent anywhere.

Idempotent: re-running syncs again (a no-op against the same roster) and skips
anything already in place. Only tenants with check-in live get rows — the
service refuses the rest, which is the point of the flag.
"""

from datetime import datetime, timezone

from application.services.check_in_service import CheckInService
from application.services.feature_flag_service import FeatureFlagService, reset_flag_cache
from application.services.matcherino_login_service import MatcherinoLoginService
from application.utils.clients.matcherino_client import (
    MOCK_REMEMBERED_ID,
    MOCK_TWITCH_RACER_ID,
    MockMatcherinoClient,
)
from models import (
    CheckInDmStatus,
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEvent,
    CheckInEventStatus,
    FeatureFlag,
    MatcherinoLogin,
    Role,
    Tenant,
    User,
)
from scripts.seed_support import backfill

LIVE_EVENT = 'Wizzrobe Live — Check-in'
DRAFT_EVENT = 'Spring Meetup (walk-ups only)'
CLOSED_EVENT = 'Wizzrobe Live 2025'
VENUE_ID = 900100
WITHDRAWN_ID = '900099'
PLACEHOLDER_LOGIN = 'placeholder-matcherino-login'


async def seed_check_in_identities(users: dict[str, User]) -> None:
    """Give two racers the identifiers the mock sales match on. Global."""
    await backfill(users['racer_05'], matcherino_user_id=str(MOCK_REMEMBERED_ID))
    await backfill(users['racer_07'], twitch_user_id=MOCK_TWITCH_RACER_ID)
    print('  check-in identities ok (racer_05/07 match the mock Matcherino sales)')


async def seed_check_in_for_tenant(tenant: Tenant, users: dict[str, User]) -> None:
    reset_flag_cache()
    if not await FeatureFlagService().is_enabled(FeatureFlag.EVENT_CHECK_IN):
        print(f'    [{tenant.slug}] check-in skipped (feature not live here)')
        return

    staff, desk = users['staff_user'], users['checkin_desk']
    service = CheckInService(client=MockMatcherinoClient())

    event = await CheckInEvent.get_or_none(tenant=tenant, name=LIVE_EVENT)
    if event is None:
        event = await service.create_event(
            staff, LIVE_EVENT, venue_id=VENUE_ID, status=CheckInEventStatus.OPEN,
            comp_roles=[Role.STAFF], comp_volunteers=True,
        )
    elif not event.comp_roles:
        event = await service.update_event(
            staff, event.id, event.name, event.matcherino_venue_id, event.status,
            event.sync_interval_minutes, comp_roles=[Role.STAFF], comp_volunteers=True,
        )
    if not await CheckInEntrant.exists(event=event, matcherino_user_id=WITHDRAWN_ID):
        await CheckInEntrant.create(
            tenant=tenant, event=event, source=CheckInEntrantSource.MATCHERINO,
            matcherino_user_id=WITHDRAWN_ID, display_name='Changed Their Mind',
            auth_provider='gplus',
            withdrawn_at=datetime.now(timezone.utc),
        )
    await service.sync_event(staff, event.id, audit=False)

    roster = await service.roster(event)
    rows = {row.matcherino_user_id: row for row in roster if row.matcherino_user_id}
    manual = rows.get('900003')
    if manual is not None and manual.user_id is None and manual.link_method is None:
        await service.link(staff, manual.id, users['player_three'].id)
    first = rows.get('100234')
    if first is not None and first.checked_in_at is None:
        await service.check_in(desk, first.id)
    if not any(row.user_id == users['racer_09'].id for row in roster):
        await service.add_walk_up(staff, event.id, user_id=users['racer_09'].id)
    if not any(row.display_name == 'Walk-in Wendy' for row in roster):
        await service.add_walk_up(staff, event.id, name='Walk-in Wendy')
    if not any(row.display_name == 'Racer Six' for row in roster):
        await service.add_walk_up(staff, event.id, name='Racer Six')
    for name in ('racer_05', 'racer_07', 'player_three'):
        row = await CheckInEntrant.get_or_none(event=event, user=users[name])
        if row is not None and row.checked_in_at is None:
            await service.check_in(desk, row.id)
    now = datetime.now(timezone.utc)
    for name, status in (
        ('player_one', CheckInDmStatus.SENT), ('racer_09', CheckInDmStatus.BLOCKED),
        ('racer_07', CheckInDmStatus.FAILED), ('player_three', CheckInDmStatus.OPTED_OUT),
    ):
        await CheckInEntrant.filter(event=event, user=users[name]).update(dm_status=status, dm_checked_at=now)

    if not await CheckInEvent.exists(tenant=tenant, name=DRAFT_EVENT):
        await service.create_event(staff, DRAFT_EVENT)
    if not await CheckInEvent.exists(tenant=tenant, name=CLOSED_EVENT):
        await service.create_event(staff, CLOSED_EVENT, status=CheckInEventStatus.CLOSED)
    if not await MatcherinoLogin.exists(tenant=tenant):
        logins = MatcherinoLoginService(client_factory=lambda _: MockMatcherinoClient())
        await logins.set_login(staff, PLACEHOLDER_LOGIN)
        await logins.set_alert_user(staff, staff.id)
    print(f'    [{tenant.slug}] check-in ok (open event with synced badge sales, a draft, a closed one, '
          'a saved Matcherino login)')
