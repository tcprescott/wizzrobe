"""Racetime Room Repository — data access for :class:`~models.RacetimeRoom`.

Rooms are tenant-scoped data, but the **by-slug lookup is deliberately
unscoped**: inbound racetime events carry only the room slug (no tenant), so the
routing layer must resolve slug → room → tenant with no ambient scope, exactly
like the token-hash lookup that routes an ``ApiToken`` back to its tenant. That
method never calls the ``_tenant`` helpers and says so; the scoped reads/writes
(created by the PR 4/6 lifecycle) use them.
"""

from datetime import datetime, timezone
from typing import Any, List, Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import scoped
from models import Match, RaceRoomStatus, RacetimeRoom


class RacetimeRoomRepository(TenantScopedRepository[RacetimeRoom]):
    """Data access for racetime race-room records."""

    model = RacetimeRoom

    async def get_by_slug(self, slug: str) -> Optional[RacetimeRoom]:
        """Resolve a room by its globally-unique slug — **unscoped** on purpose.

        Inbound racetime events carry no tenant, so slug → room is the entry
        point the routing layer uses before any tenant context exists (mirrors
        ``ApiTokenRepository.get_by_hash``).
        """
        return await RacetimeRoom.get_or_none(slug=slug).prefetch_related('tenant')

    async def list_open_all(self) -> List[RacetimeRoom]:
        """Every not-yet-terminal room across all tenants — **unscoped**.

        Used by the runtime on boot to re-adopt live rooms so a redeploy does
        not orphan them. Cross-tenant on purpose (like :meth:`get_by_slug`); the
        caller re-establishes each room's tenant scope from ``room.tenant_id``.
        """
        return await RacetimeRoom.filter(
            status__in=[RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS],
        ).prefetch_related('tenant').order_by('id')

    async def list_bot_run_open(self, category: str) -> List[RacetimeRoom]:
        """Open match rooms in ``category`` whose tournament has the bot run them — **unscoped**.

        The bot's connection asks for these every few seconds to know which rooms
        to sit in, across every community that shares the category's bot.
        """
        return await RacetimeRoom.filter(
            category=category,
            status__in=[RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS],
            match__tournament__racetime_bot_runs_room=True,
        ).order_by('id')

    async def claim_hold(self, room: RacetimeRoom) -> bool:
        """Clear the room's held result; ``False`` when someone else already did.

        Conditional on the hold still being there, so two staff confirming the
        same race at once record it once.
        """
        cleared = await scoped(
            RacetimeRoom.filter(id=room.id, held_reason__isnull=False)
        ).update(held_reason=None, held_detail=None)
        if cleared:
            room.held_reason = None  # type: ignore[assignment]
            room.held_detail = None  # type: ignore[assignment]
        return bool(cleared)

    async def claim_seed_post(self, room: RacetimeRoom, at: datetime) -> bool:
        """Stamp the seed as posted; ``False`` when it already was (post once)."""
        claimed = await scoped(
            RacetimeRoom.filter(id=room.id, seed_posted_at__isnull=True)
        ).update(seed_posted_at=at)
        if claimed:
            room.seed_posted_at = at
        return bool(claimed)

    async def claim_greeting(self, room: RacetimeRoom, at: datetime) -> bool:
        """Stamp the room as greeted; ``False`` when it already was."""
        claimed = await scoped(
            RacetimeRoom.filter(id=room.id, greeted_at__isnull=True)
        ).update(greeted_at=at)
        if claimed:
            room.greeted_at = at
        return bool(claimed)

    async def bot_run_awaiting_seed(self) -> List[RacetimeRoom]:
        """Open bot-run match rooms whose seed isn't posted yet — **unscoped** cross-tenant.

        The worker's scan for seeds that have come due; each room is handled
        inside its own community's scope.
        """
        return await RacetimeRoom.filter(
            status__in=[RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS],
            seed_posted_at__isnull=True,
            match__tournament__racetime_bot_runs_room=True,
        ).prefetch_related('match', 'match__tournament').order_by('id')

    async def matches_due_for_auto_open(self, window_start: Any, window_end: Any) -> List[Match]:
        """Candidate matches for the auto-open worker — **unscoped** cross-tenant.

        Returns not-yet-finished matches on auto-create tournaments scheduled in
        the ``[window_start, window_end]`` band, with players/users/tournament
        prefetched so the worker can re-check each tournament's lead time and the
        linked-entrant eligibility without more queries. Intentionally
        tenant-agnostic (like the volunteer-reminder scan); the worker binds each
        match's tenant before acting.
        """
        return await Match.filter(
            scheduled_at__gte=window_start,
            scheduled_at__lte=window_end,
            finished_at__isnull=True,
            tournament__racetime_auto_create_rooms=True,
        ).prefetch_related(
            'tournament', 'players', 'players__user',
        ).order_by('scheduled_at')

    async def get_by_match(self, match: Match) -> Optional[RacetimeRoom]:
        return await scoped(RacetimeRoom.filter(match_id=match.id)).first()

    async def for_matches(self, match_ids: List[int]) -> dict[int, RacetimeRoom]:
        """``{match_id: room}`` for a whole match set — **one scoped query**.

        The batched peer of :meth:`get_by_match`, for the bracket view: it resolves
        the live state of every card at once, so a 64-match field costs one query
        rather than 64. ``RacetimeRoom.match`` is a OneToOne, so the mapping is
        single-valued.
        """
        if not match_ids:
            return {}
        rooms = await scoped(RacetimeRoom.filter(match_id__in=list(match_ids)))
        return {r.match_id: r for r in rooms if r.match_id is not None}

    async def open_for_player(self, user_id: int) -> List[RacetimeRoom]:
        """Rooms not yet finished or cancelled on matches ``user_id`` plays in.

        Soonest match first. Sorted here rather than by ``order_by`` because the
        roster join needs ``DISTINCT``, and Postgres refuses to order a distinct
        query by a column of another table.
        """
        rooms = await scoped(
            RacetimeRoom.filter(
                status__in=[RaceRoomStatus.OPEN, RaceRoomStatus.IN_PROGRESS],
                match__players__user_id=user_id,
            )
        ).distinct().prefetch_related('match', 'match__tournament', 'match__players__user')
        far = datetime.max.replace(tzinfo=timezone.utc)
        return sorted(rooms, key=lambda r: ((r.match and r.match.scheduled_at) or far, r.id))

    async def list_all(self) -> List[RacetimeRoom]:
        return await scoped(RacetimeRoom.all()).order_by('-created_at')
