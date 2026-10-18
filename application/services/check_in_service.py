"""
Check-in Service - Business Logic Layer

Runs the check-in desk for in-person events. Registration happens on
Matcherino; this service mirrors a ticketed venue's badge sales into
:class:`~models.CheckInPass` rows and one :class:`~models.CheckInEntrant` per
buyer, matches each buyer to a Wizzrobe account where it can, and records who
has checked in. Walk-ups are added by staff at the desk.

Matching to an account is a nice-to-have, never a precondition: an entrant with
no ``user`` checks in like anyone else.

The Matcherino endpoints are unofficial (see ``matcherino_client``), so a sync
that fails, or that comes back empty while the roster has rows, records the
error and changes nothing. Withdrawing everyone because one response was bad is
the failure this guards against.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from tortoise.exceptions import IntegrityError

from application.errors import require_found
from application.events import Event, EventType, check_in_live, event_bus
from application.feature_flags import requires_feature
from application.repositories import (
    CheckInEntrantRepository,
    CheckInEventRepository,
    CheckInPassRepository,
    CheckInTierRepository,
    CheckInUserLookupRepository,
    UserRepository,
    VolunteerAssignmentRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.check_in_accounts import (
    build_lookups,
    desk_entrant,
    ensure_desk_sees,
    forget_matcherino_account,
    linkable_user,
    remember_matcherino_account,
)
from application.services.check_in_comps import absorb_comp_row, apply_comps, comped_users, has_comp_rules
from application.services.check_in_dm import start_dm_check
from application.services.check_in_rules import (
    CheckInOutcome,
    SyncResult,
    count_suggestions,
    entrant_details,
    is_active_badge,
    norm_name,
    refuse_comp_row,
    resolve_link,
    suggested_users,
    suggestion_candidates,
    summarize,
)
from application.services.check_in_sales import apply_badges, apply_tiers, buyer_fields, buyers_of
from application.services.feature_flag_service import FeatureFlagService
from application.services.matcherino_login_service import MatcherinoLoginService
from application.utils.clients.matcherino_client import (
    MatcherinoAPIError,
    MatcherinoClient,
    MatcherinoTier,
    MatcherinoVenue,
    VenueSales,
    get_matcherino_client,
)
from models import (
    CheckInEntrant,
    CheckInEntrantSource,
    CheckInEvent,
    CheckInEventStatus,
    CheckInLinkMethod,
    CheckInPass,
    CheckInTier,
    CheckInTierLanyard,
    FeatureFlag,
    Role,
    User,
)

MIN_SYNC_INTERVAL_MINUTES = 1
MAX_SYNC_INTERVAL_MINUTES = 120
SUGGESTION_LIMIT = 3

_DESK_DENIED = 'Only staff and check-in desk volunteers can do that.'
_STAFF_DENIED = 'Only staff can do that.'
_WITHDRAWN_DENIED = 'Only staff can check them in anyway.'


@dataclass(frozen=True)
class VenuePreview:
    """What the admin dialog's Look up shows before staff save a venue id."""

    venue: MatcherinoVenue
    tiers: List[MatcherinoTier]


# One sync per event at a time. The app runs as a single process
# (docs/scaling-roadmap.md), so an in-process lock serialises the worker's poll,
# a staff Sync and every desk phone's Sync for the same event.
_sync_locks: Dict[int, asyncio.Lock] = {}


def _sync_lock(event_id: int) -> asyncio.Lock:
    return _sync_locks.setdefault(event_id, asyncio.Lock())


class CheckInService:
    """Check-in events, the Matcherino sync, and the desk's actions."""

    def __init__(self, client: Optional[MatcherinoClient] = None) -> None:
        self.events = CheckInEventRepository()
        self.entrants = CheckInEntrantRepository()
        self.tiers = CheckInTierRepository()
        self.passes = CheckInPassRepository()
        self.lookup = CheckInUserLookupRepository()
        self.audit_service = AuditService()
        self.login_service = MatcherinoLoginService()
        self._client = client

    async def _matcherino(self) -> MatcherinoClient:
        # Never cached on the service: the worker shares one across tenants, each with its own login.
        if self._client is not None:
            return self._client
        return get_matcherino_client(await self.login_service.resolve())

    # --- Events (staff) ---

    @staticmethod
    def _clean_event_fields(
        name: str, venue_id: Optional[int], sync_interval_minutes: int,
    ) -> Tuple[str, Optional[int], int]:
        name = (name or '').strip()
        if not name:
            raise ValueError('Give the event a name.')
        if venue_id is not None and venue_id <= 0:
            raise ValueError('A Matcherino venue ID is a positive number.')
        if not MIN_SYNC_INTERVAL_MINUTES <= sync_interval_minutes <= MAX_SYNC_INTERVAL_MINUTES:
            raise ValueError(
                f'Sync every {MIN_SYNC_INTERVAL_MINUTES} to {MAX_SYNC_INTERVAL_MINUTES} minutes.'
            )
        return name, venue_id, sync_interval_minutes

    @staticmethod
    def _clean_comp_roles(comp_roles: Optional[Sequence[object]]) -> List[str]:
        """Role values a community may comp, in ``Role`` order, deduplicated."""
        wanted = {getattr(role, 'value', role) for role in comp_roles or []}
        grantable = [role.value for role in Role.tenant_grantable()]
        unknown = wanted - set(grantable)
        if unknown:
            raise ValueError(f"Those roles can't be comped: {', '.join(sorted(map(str, unknown)))}.")
        return [value for value in grantable if value in wanted]

    async def _ensure_venue_free(self, venue_id: Optional[int], event_id: Optional[int] = None) -> None:
        if venue_id is None:
            return
        existing = await self.events.get_by_venue(venue_id)
        if existing is not None and existing.id != event_id:
            raise ValueError(f'Venue {venue_id} is already used by "{existing.name}".')

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def create_event(
        self,
        actor: User,
        name: str,
        venue_id: Optional[int] = None,
        status: CheckInEventStatus = CheckInEventStatus.DRAFT,
        sync_interval_minutes: int = 5,
        *,
        comp_roles: Optional[Sequence[object]] = None,
        comp_volunteers: bool = False,
    ) -> CheckInEvent:
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        name, venue_id, sync_interval_minutes = self._clean_event_fields(
            name, venue_id, sync_interval_minutes,
        )
        roles = self._clean_comp_roles(comp_roles)
        await self._ensure_venue_free(venue_id)
        try:
            event = await self.events.create(
                name=name, matcherino_venue_id=venue_id, status=status,
                sync_interval_minutes=sync_interval_minutes,
                comp_roles=roles, comp_volunteers=comp_volunteers,
            )
        except IntegrityError as e:
            raise ValueError(f'Venue {venue_id} is already used by another event.') from e
        await self.audit_service.write_log(
            actor, AuditActions.CHECK_IN_EVENT_CREATED,
            {'event_id': event.id, 'name': name, 'venue_id': venue_id, 'status': status.value,
             'comp_roles': roles, 'comp_volunteers': comp_volunteers},
        )
        return event

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def update_event(
        self,
        actor: User,
        event_id: int,
        name: str,
        venue_id: Optional[int],
        status: CheckInEventStatus,
        sync_interval_minutes: int,
        *,
        comp_roles: Optional[Sequence[object]] = None,
        comp_volunteers: Optional[bool] = None,
    ) -> CheckInEvent:
        """Edit an event. ``comp_roles`` / ``comp_volunteers`` left ``None`` keep their values."""
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        event = require_found(await self.events.get_by_id(event_id), 'Check-in event')
        name, venue_id, sync_interval_minutes = self._clean_event_fields(
            name, venue_id, sync_interval_minutes,
        )
        roles = event.comp_roles if comp_roles is None else self._clean_comp_roles(comp_roles)
        volunteers = event.comp_volunteers if comp_volunteers is None else comp_volunteers
        await self._ensure_venue_free(venue_id, event.id)
        if venue_id != event.matcherino_venue_id and await self.entrants.has_matcherino_rows(event):
            raise ValueError(
                "This event already has a Matcherino roster, so its venue can't change: "
                'the next sync would mark everyone on it as withdrawn. Create a new event instead.'
            )
        changed = {
            key: value
            for key, value in (
                ('name', name), ('matcherino_venue_id', venue_id), ('status', status),
                ('sync_interval_minutes', sync_interval_minutes),
                ('comp_roles', roles), ('comp_volunteers', volunteers),
            )
            if getattr(event, key) != value
        }
        if not changed:
            return event
        await self.events.update(event, **changed)
        await self.audit_service.write_log(
            actor, AuditActions.CHECK_IN_EVENT_UPDATED,
            {'event_id': event.id, 'changed': {
                key: (value.value if isinstance(value, CheckInEventStatus) else value)
                for key, value in changed.items()
            }},
        )
        check_in_live.publish(event.id, None, check_in_live.ROSTER)
        return event

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def delete_event(self, actor: User, event_id: int) -> None:
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        event = require_found(await self.events.get_by_id(event_id), 'Check-in event')
        entrants = await self.entrants.list_for_event(event)
        counts = summarize(entrants)
        await self.events.delete(event)
        await self.audit_service.write_log(
            actor, AuditActions.CHECK_IN_EVENT_DELETED,
            {'event_id': event_id, 'name': event.name,
             'entrants': len(entrants), 'checked_in': counts.checked_in},
        )
        check_in_live.publish(event_id, None, check_in_live.DELETED)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def preview_venue(self, actor: User, venue_id: int) -> VenuePreview:
        """Look a venue up on Matcherino so staff can confirm it before saving.

        Reads the badge types too, which needs the stored Matcherino login: a
        venue that previews here is one the sync can read.
        """
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        client = await self._matcherino()
        try:
            venue = await client.fetch_venue(venue_id)
            tiers = await client.fetch_tiers(venue_id)
        except MatcherinoAPIError as e:
            raise ValueError(f"Couldn't read that venue on Matcherino: {e}") from e
        return VenuePreview(venue=venue, tiers=sorted(tiers, key=lambda t: -t.amount_cents))

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def list_events(self) -> List[CheckInEvent]:
        return await self.events.list_all()

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def list_open_events(self) -> List[CheckInEvent]:
        return await self.events.list_by_status(CheckInEventStatus.OPEN)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def get_event(self, event_id: int) -> Optional[CheckInEvent]:
        return await self.events.get_by_id(event_id)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def has_open_event(self) -> bool:
        return await self.events.exists_with_status(CheckInEventStatus.OPEN)

    # --- Matcherino sync ---

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def sync_event(self, actor: User, event_id: int, *, audit: bool = True) -> SyncResult:
        """Mirror the venue's badge sales and the event's comps into the roster.

        Comps are worked out first, so a comped buyer is never withdrawn for
        having no badge; then the badge sales (when there is a venue); then the
        comp rows (:mod:`check_in_comps`). A Matcherino failure changes nothing,
        comps included. ``audit=False`` is the worker's poll: it still publishes
        the domain event, but writes no audit row every few minutes.
        """
        await AuthService.ensure(await AuthService.can_run_check_in_desk(actor), _DESK_DENIED)
        async with _sync_lock(event_id):
            event = await ensure_desk_sees(actor, require_found(await self.events.get_by_id(event_id), 'Check-in event'))
            if event.matcherino_venue_id is None and not has_comp_rules(event):
                raise ValueError(
                    "This event has no Matcherino venue and comps nobody, so there's nothing to sync."
                )
            comped = await comped_users(event)
            if event.matcherino_venue_id is not None:
                result = await self._sync_sales(actor, event, comped)
            else:
                result = SyncResult()
            now = datetime.now(timezone.utc)
            try:
                comps = await apply_comps(self.entrants, event, comped, now)
            except IntegrityError as e:
                raise ValueError(
                    'The roster changed while syncing. Try Sync again in a moment.'
                ) from e
            result.added += comps.added
            result.withdrawn += comps.withdrawn
            result.rejoined += comps.rejoined
            result.comped = len(comped)
            await self.events.record_sync(
                event, last_synced_at=now, last_sync_error=None, last_sync_count=result.total,
            )

        details = {'event_id': event.id, 'venue_id': event.matcherino_venue_id, **result.as_dict()}
        if audit:
            await self.audit_service.write_and_publish(
                actor, AuditActions.CHECK_IN_EVENT_SYNCED, details, EventType.CHECK_IN_EVENT_SYNCED,
            )
        else:
            event_bus.publish(Event.create(EventType.CHECK_IN_EVENT_SYNCED, details, actor))
        check_in_live.publish(event.id, None, check_in_live.ROSTER)
        return result

    async def _sync_sales(self, actor: User, event: CheckInEvent, comped: Dict[int, List[str]]) -> SyncResult:
        """Fetch the venue's sales and apply them; on a Matcherino failure, record it and raise."""
        assert event.matcherino_venue_id is not None
        client = await self._matcherino()
        try:
            sales = await client.fetch_sales(event.matcherino_venue_id)
            # Read after the fetch, so desk actions made while it ran are seen.
            existing = await self.entrants.matcherino_rows_by_user_id(event)
            stored = await self.passes.by_purchase_id(event)
            if not sales.purchases and any(is_active_badge(p) for p in stored.values()):
                raise MatcherinoAPIError('Matcherino returned no badges for a venue that had some')
        except MatcherinoAPIError as e:
            await self.login_service.note_sync_result(e)
            # updated_at marks when it failed: the desk shows it and the
            # worker backs off from it.
            await self.events.record_sync(
                event, last_sync_error=str(e)[:1000], updated_at=datetime.now(timezone.utc),
            )
            check_in_live.publish(event.id, None, check_in_live.ROSTER)
            raise ValueError(f"Couldn't sync with Matcherino, so the roster wasn't changed. {e}") from e
        await self.login_service.note_sync_result()
        try:
            return await self._apply_sales(actor, event, sales, existing, stored, comped)
        except IntegrityError as e:
            raise ValueError(
                'The roster changed while syncing. Try Sync again in a moment.'
            ) from e

    async def _apply_sales(
        self,
        actor: User,
        event: CheckInEvent,
        sales: VenueSales,
        existing: Dict[str, CheckInEntrant],
        stored: Dict[int, CheckInPass],
        comped: Dict[int, List[str]],
    ) -> SyncResult:
        """One roster row per buyer, one badge row per purchase.

        A buyer with no unrefunded badge is withdrawn, and so is a row whose
        buyer has dropped out of the sales altogether, unless their account is
        comped. A buyer matched to someone already on the roster as a comp
        takes over that comp row (:func:`absorb_comp_row`).
        """
        now = datetime.now(timezone.utc)
        buyers = buyers_of(sales)
        holding = {user_id for user_id, buyer in buyers.items() if buyer.holds_badge}
        result = SyncResult(total=len(holding), badges=sum(1 for p in sales.purchases if p.active))
        created: List[CheckInEntrant] = []
        touched: Dict[int, Tuple[CheckInEntrant, set]] = {}

        for user_id, buyer in buyers.items():
            fields = buyer_fields(buyer)
            row = existing.get(user_id)
            if row is None:
                created.append(CheckInEntrant(
                    event=event, source=CheckInEntrantSource.MATCHERINO, matcherino_user_id=user_id,
                    withdrawn_at=None if user_id in holding else now, **fields,
                ))
                continue
            changes = {key for key, value in fields.items() if getattr(row, key) != value}
            for key in changes:
                setattr(row, key, fields[key])
            if changes:
                touched[row.id] = (row, changes)
                result.updated += 1

        for user_id, row in existing.items():
            holds = user_id in holding or row.user_id in comped
            if holds and row.withdrawn_at is not None:
                row.withdrawn_at = None  # type: ignore[assignment]
                result.rejoined += 1
            elif not holds and row.withdrawn_at is None:
                row.withdrawn_at = now
                result.withdrawn += 1
            else:
                continue
            touched[row.id] = (row, touched.get(row.id, (row, set()))[1] | {'withdrawn_at'})

        unlinked = [
            row for row in [*created, *existing.values()]
            if row.user_id is None and row.link_method != CheckInLinkMethod.MANUAL
        ]
        comp_rows = await self.entrants.comp_rows_by_user_id(event)
        matches = await self._auto_link_matches(event, unlinked, set(comp_rows)) if unlinked else []
        new_rows = {id(row) for row in created}
        linked: List[Tuple[CheckInEntrant, User, CheckInLinkMethod]] = []
        for row, user, method in matches:
            comp_row = comp_rows.pop(user.id, None)
            if comp_row is not None:
                absorbed = absorb_comp_row(comp_row, row)
                await self.entrants.delete(comp_row)
                if id(row) not in new_rows and absorbed:
                    touched[row.id] = (row, touched.get(row.id, (row, set()))[1] | set(absorbed))
            if id(row) in new_rows:
                row.user = user
                row.link_method = method
                if user.id in comped:
                    row.withdrawn_at = None  # type: ignore[assignment]
                linked.append((row, user, method))

        result.added = len(created)
        if created:
            await self.entrants.bulk_create(created)
        for row, changes in touched.values():
            await self.entrants.save(row, [*changes, 'updated_at'])
        for row, user, method in matches:
            if id(row) in new_rows:
                continue
            # Conditional: a desk may have linked or unlinked this row meanwhile.
            if await self.entrants.claim_auto_link(row, user, method, now):
                row.user = user
                row.link_method = method
                linked.append((row, user, method))
        for row, user, method in linked:
            await remember_matcherino_account(self.audit_service, actor, user, row, method)
        result.auto_linked = len(linked)

        tiers = await apply_tiers(self.tiers, event, sales)
        # bulk_create doesn't hand back primary keys on every backend, so the
        # badges look their buyer's row up again.
        rows = await self.entrants.matcherino_rows_by_user_id(event)
        await apply_badges(self.passes, event, sales.purchases, stored, tiers, rows, now)
        return result

    async def _auto_link_matches(
        self, event: CheckInEvent, rows: Sequence[CheckInEntrant], absorbable: frozenset | set = frozenset(),
    ) -> List[Tuple[CheckInEntrant, User, CheckInLinkMethod]]:
        """The account each row's identifiers name exactly, one row per account.

        Exact identifiers only, matched across all of Wizzrobe: a buyer's
        Discord or Twitch id comes from the account they signed in to Matcherino
        with, so finding the Wizzrobe account it belongs to reveals nothing new
        to staff who can already see the sale. Fuzzy name matches are only
        ever *suggested* (:meth:`suggest_users`), never applied. Accounts in
        ``absorbable`` (held only by a comp row) are still free to match.
        """
        lookups = await build_lookups(rows)
        taken = await self.entrants.linked_user_ids(event) - set(absorbable)
        matches: List[Tuple[CheckInEntrant, User, CheckInLinkMethod]] = []
        for row in rows:
            match = resolve_link(row, lookups)
            if match is None or match[0].id in taken:
                continue
            taken.add(match[0].id)
            matches.append((row, *match))
        return matches

    # --- Roster reads ---

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def roster(self, event: CheckInEvent) -> List[CheckInEntrant]:
        return await self.entrants.list_for_event(event)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def tiers_for(self, event: CheckInEvent) -> List[CheckInTier]:
        """The event's badge types, most expensive first."""
        return await self.tiers.list_for_event(event)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def set_tier_lanyard(self, actor: User, tier_id: int, lanyard: object) -> CheckInTier:
        """Set which lanyard a badge type's holders get. Staff only; the sync keeps it."""
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        tier = require_found(await self.tiers.get_by_id(tier_id), 'Badge type')
        try:
            value = CheckInTierLanyard(getattr(lanyard, 'value', lanyard))
        except ValueError as e:
            raise ValueError("That isn't a lanyard a badge type can give.") from e
        if tier.lanyard == value:
            return tier
        previous = tier.lanyard
        tier.lanyard = value
        await self.tiers.save(tier, ['lanyard', 'updated_at'])
        await self.audit_service.write_log(
            actor, AuditActions.CHECK_IN_TIER_UPDATED,
            {'event_id': tier.event_id, 'tier_id': tier.id, 'title': tier.title,
             'lanyard': {'from': previous.value, 'to': value.value}},
        )
        check_in_live.publish(tier.event_id, None, check_in_live.ROSTER)
        return tier

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def volunteer_user_ids(self) -> set[int]:
        """Accounts holding a published volunteer assignment in this community.

        The desk marks these so volunteers can be waved through. Unpublished
        auto-scheduler drafts don't count. With volunteering off this answers
        an empty set rather than raising: it's a soft read from another
        subsystem, not a volunteering surface.
        """
        if not await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS):
            return set()
        return await VolunteerAssignmentRepository.published_user_ids()

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def get_entrant(self, entrant_id: int) -> Optional[CheckInEntrant]:
        return await self.entrants.get_by_id(entrant_id)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def suggest_users(self, entrant: CheckInEntrant, limit: int = SUGGESTION_LIMIT) -> List[User]:
        """Community members whose names look like the registrant's.

        A suggestion for staff to confirm, never applied on its own. Members
        already linked to someone else on this roster are left out, except a
        comp, whose row a link folds into this one (:meth:`link`).
        """
        target = norm_name(entrant.display_name)
        if not target:
            return []
        taken = await self._taken_user_ids(entrant.event, for_link=True)
        candidates = suggestion_candidates(await UserRepository.get_community_people_summaries(), taken)
        picks = await asyncio.to_thread(suggested_users, target, candidates, limit)
        return await UserRepository.get_in_order([pick.id for pick in picks])

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def suggestion_counts(
        self, event: CheckInEvent, entrants: Sequence[CheckInEntrant], limit: int = SUGGESTION_LIMIT,
    ) -> Dict[int, int]:
        """How many suggestions :meth:`suggest_users` would offer each unlinked row: one
        community read for the whole roster, scored off the event loop every desk shares."""
        if all(e.user_id is not None for e in entrants):
            return {}
        taken = await self._taken_user_ids(event, for_link=True)
        candidates = suggestion_candidates(await UserRepository.get_community_people_summaries(), taken)
        return await asyncio.to_thread(count_suggestions, entrants, candidates, limit)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def search_members(
        self, event: CheckInEvent, query: str, limit: int = 20, *, for_link: bool = False,
    ) -> List[User]:
        """Community members not yet on this roster, matching ``query``.

        Backs the member search in the link and walk-up dialogs. With
        ``for_link`` it also offers people on the roster only as a comp: a
        comped volunteer who also bought a badge must be findable so their
        purchase can be linked and the comp folded into it.
        """
        needle = norm_name(query)
        if len(needle) < 2:
            return []
        taken = await self._taken_user_ids(event, for_link=for_link)
        matches = [
            person.id for person in await UserRepository.get_community_people_summaries()
            if person.id not in taken and any(
                needle in norm_name(name)
                for name in (person.username, person.display_name, person.twitch_username,
                             person.matcherino_username)
            )
        ]
        return await UserRepository.get_in_order(matches[:limit])

    async def _taken_user_ids(self, event: CheckInEvent, *, for_link: bool) -> set[int]:
        taken = await self.entrants.linked_user_ids(event)
        if for_link:
            taken -= set(await self.entrants.comp_rows_by_user_id(event))
        return taken

    # --- Desk actions ---

    async def _entrant_for_desk(self, actor: User, entrant_id: int) -> CheckInEntrant:
        await AuthService.ensure(await AuthService.can_run_check_in_desk(actor), _DESK_DENIED)
        return await desk_entrant(actor, require_found(await self.entrants.get_by_id(entrant_id), 'Roster entry'))

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def check_in(self, actor: User, entrant_id: int, *, despite_withdrawn: bool = False) -> CheckInOutcome:
        """Check someone in. Doing it twice is not an error: it reports who did it first.

        The write is conditional, so two phones tapping the same person at once
        record one check-in, one audit row and one event. Someone withdrawn
        (refunded, no badge left, no longer comped) has nothing to check in
        with: only staff can let them in anyway, with ``despite_withdrawn``.
        """
        entrant = await self._entrant_for_desk(actor, entrant_id)
        if despite_withdrawn:
            await AuthService.ensure(await AuthService.can_manage_check_in(actor), _WITHDRAWN_DENIED)
        if entrant.checked_in_at is None and entrant.withdrawn_at is not None and not despite_withdrawn:
            raise ValueError(f"{entrant.display_name} doesn't hold a badge any more. {_WITHDRAWN_DENIED}")
        now = datetime.now(timezone.utc)
        if entrant.checked_in_at is not None or not await self.entrants.claim_check_in(
            entrant, now, actor, active_only=not despite_withdrawn,
        ):
            current = await self.entrants.get_by_id(entrant_id) or entrant
            if current.checked_in_at is None and current.withdrawn_at is not None:
                # Withdrawn by a sync between the read above and the claim.
                raise ValueError(f"{current.display_name} doesn't hold a badge any more. {_WITHDRAWN_DENIED}")
            return CheckInOutcome(current, already=True)
        entrant.checked_in_at = now
        entrant.checked_in_by = actor
        details = entrant_details(entrant)
        if entrant.withdrawn_at is not None:
            details['despite_withdrawn'] = True
        await self.audit_service.write_and_publish(
            actor, AuditActions.CHECK_IN_ENTRANT_CHECKED_IN, details,
            EventType.CHECK_IN_ENTRANT_CHECKED_IN,
        )
        await start_dm_check(entrant, entrant.event.name)
        check_in_live.publish(entrant.event_id, entrant.id, check_in_live.CHANGED)
        return CheckInOutcome(entrant)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def undo_check_in(self, actor: User, entrant_id: int) -> CheckInEntrant:
        entrant = await self._entrant_for_desk(actor, entrant_id)
        if entrant.checked_in_at is None or not await self.entrants.release_check_in(
            entrant, datetime.now(timezone.utc),
        ):
            return entrant
        entrant.checked_in_at = None  # type: ignore[assignment]
        entrant.checked_in_by = None
        await self.audit_service.write_and_publish(
            actor, AuditActions.CHECK_IN_ENTRANT_CHECK_IN_UNDONE, entrant_details(entrant),
            EventType.CHECK_IN_ENTRANT_CHECK_IN_UNDONE,
        )
        check_in_live.publish(entrant.event_id, entrant.id, check_in_live.CHANGED)
        return entrant

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def link(self, actor: User, entrant_id: int, user_id: int) -> CheckInEntrant:
        entrant = await self._entrant_for_desk(actor, entrant_id)
        refuse_comp_row(entrant)
        user = await linkable_user(user_id, actor)
        if entrant.user_id == user.id:
            return entrant
        other = await self.entrants.get_for_user(entrant.event, user)
        absorbed: List[str] = []
        if other is not None and other.source == CheckInEntrantSource.COMP:
            # They were comped before anyone matched this purchase to them.
            absorbed = absorb_comp_row(other, entrant)
            await self.entrants.delete(other)
        elif other is not None:
            raise ValueError(f'{user.preferred_name} is already on this roster as {other.display_name}.')
        if entrant.matcherino_user_id:
            holder = await self.lookup.user_with_matcherino_id(entrant.matcherino_user_id)
            if holder is not None and holder.id != user.id:
                raise ValueError(
                    f'That Matcherino account is already linked to {holder.preferred_name}.'
                )
        entrant.user = user
        entrant.link_method = CheckInLinkMethod.MANUAL
        entrant.linked_by = actor
        try:
            await self.entrants.save(
                entrant, ['user_id', 'link_method', 'linked_by_id', *absorbed, 'updated_at'],
            )
        except IntegrityError as e:
            raise ValueError(f'{user.preferred_name} was just added to this roster by someone else.') from e
        await remember_matcherino_account(self.audit_service, actor, user, entrant, CheckInLinkMethod.MANUAL)
        await self.audit_service.write_and_publish(
            actor, AuditActions.CHECK_IN_ENTRANT_LINKED, entrant_details(entrant),
            EventType.CHECK_IN_ENTRANT_LINKED,
        )
        check_in_live.publish(entrant.event_id, entrant.id, check_in_live.CHANGED)
        return entrant

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def unlink(self, actor: User, entrant_id: int) -> CheckInEntrant:
        """Detach the account from this roster row.

        The row is marked ``MANUAL`` with no user, which tells the sync not to
        auto-link it again: staff unlink precisely when a match was wrong. For
        the same reason the Matcherino id the link recorded on the account is
        cleared (:func:`forget_matcherino_account`).
        """
        entrant = await self._entrant_for_desk(actor, entrant_id)
        refuse_comp_row(entrant)
        if entrant.user_id is None:
            return entrant
        previous_user = entrant.user
        previous_user_id = entrant.user_id
        entrant.user = None
        entrant.link_method = CheckInLinkMethod.MANUAL
        entrant.linked_by = actor
        await self.entrants.save(entrant, ['user_id', 'link_method', 'linked_by_id', 'updated_at'])
        if previous_user is not None:
            await forget_matcherino_account(self.audit_service, actor, previous_user, entrant)
        await self.audit_service.write_and_publish(
            actor, AuditActions.CHECK_IN_ENTRANT_UNLINKED,
            {**entrant_details(entrant), 'user_id': previous_user_id},
            EventType.CHECK_IN_ENTRANT_UNLINKED,
        )
        check_in_live.publish(entrant.event_id, entrant.id, check_in_live.CHANGED)
        return entrant

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def add_walk_up(
        self,
        actor: User,
        event_id: int,
        *,
        user_id: Optional[int] = None,
        name: Optional[str] = None,
        check_in: bool = True,
    ) -> CheckInEntrant:
        """Add someone who didn't buy a badge on Matcherino. Staff only."""
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        event = require_found(await self.events.get_by_id(event_id), 'Check-in event')
        user = await linkable_user(user_id, actor) if user_id else None
        if user is not None:
            other = await self.entrants.get_for_user(event, user)
            if other is not None:
                raise ValueError(f'{user.preferred_name} is already on this roster as {other.display_name}.')
            display_name = user.preferred_name
        else:
            display_name = (name or '').strip()
            if not display_name:
                raise ValueError('Pick a member or type their name.')
        now = datetime.now(timezone.utc)
        try:
            entrant = await self.entrants.create(
                event=event,
                source=CheckInEntrantSource.WALK_UP,
                display_name=display_name[:255],
                user=user,
                link_method=CheckInLinkMethod.WALK_UP if user else None,
                linked_by=actor if user else None,
                checked_in_at=now if check_in else None,
                checked_in_by=actor if check_in else None,
            )
        except IntegrityError as e:
            raise ValueError('That member was just added to this roster by someone else.') from e
        await self.audit_service.write_and_publish(
            actor, AuditActions.CHECK_IN_ENTRANT_WALK_UP_ADDED,
            {**entrant_details(entrant), 'checked_in': check_in},
            EventType.CHECK_IN_ENTRANT_WALK_UP_ADDED,
        )
        if check_in:
            await start_dm_check(entrant, event.name)
        check_in_live.publish(event.id, entrant.id, check_in_live.CREATED)
        return entrant

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def remove_entrant(self, actor: User, entrant_id: int) -> None:
        """Delete a walk-up added by mistake. Staff only.

        Matcherino rows can't be removed here: the next sync would bring them
        straight back. Someone who isn't coming gets their badge refunded on
        Matcherino, and the sync marks them withdrawn.
        """
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        entrant = require_found(await self.entrants.get_by_id(entrant_id), 'Roster entry')
        if entrant.source == CheckInEntrantSource.COMP:
            raise ValueError(
                "Comps come from the event's comp rules and come back on the next sync. "
                'Change the rules to remove someone.'
            )
        if entrant.source != CheckInEntrantSource.WALK_UP:
            raise ValueError(
                "Matcherino badge holders come back on the next sync, so they can't be "
                'removed here. Refund their badge on Matcherino instead.'
            )
        details = entrant_details(entrant)
        event_id = entrant.event_id
        await self.entrants.delete(entrant)
        await self.audit_service.write_and_publish(
            actor, AuditActions.CHECK_IN_ENTRANT_REMOVED, details, EventType.CHECK_IN_ENTRANT_REMOVED,
        )
        check_in_live.publish(event_id, entrant_id, check_in_live.DELETED)
