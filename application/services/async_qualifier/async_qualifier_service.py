"""Async Qualifier Service — Business Logic Layer (PR 9).

The self-paced permalink-pool qualifier: a peer aggregate of ``Tournament`` with
its own state machine (window opens → draw → run → review → scored leaderboard →
close). This service owns every rule the repositories deliberately don't:

- **Management** (create/edit qualifier, pools, permalinks, admins) gated by
  :meth:`AuthService.can_admin_qualifier`; ``admins`` doubles as the reviewer set.
- **Draw** — an atomic, row-locked transaction: one active run per player, a
  permalink revealed only at start, no-repeat, ``runs_per_pool`` cap, and
  imbalance-forcing fairness so sampling stays even.
- **Run lifecycle** — submit (→ review), forfeit (irreversible, scores zero), and
  reattempt (voids the prior run, frees the slot, requires a reason, is limited).
- **Review** — reviewers = the qualifier's ``admins``; **self-review blocked**;
  claim-locking; approve/reject recomputes the permalink's par and rescores.
- **Scoring / leaderboard** — par + score math in
  :mod:`application.services.async_qualifier.async_qualifier_scoring`; the board obeys the
  **active-window information lockdown** (pool/par/other entrants' runs are
  staff-only until the qualifier closes).

Raises :class:`ValueError` for user errors and :class:`PermissionError` for authz
(both surfaced by the UI); audits every state change and mirrors run
submitted/reviewed onto the event bus. Discord DMs (window-open, run-reviewed) are
best-effort and never block a state change.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from tortoise.transactions import in_transaction

from application.errors import NotFoundError
from application.events import EventType
from application.feature_flags import requires_feature
from application.repositories import (
    AsyncQualifierPermalinkRepository,
    AsyncQualifierPoolRepository,
    AsyncQualifierRepository,
    AsyncQualifierReviewNoteRepository,
    AsyncQualifierRunRepository,
    PresetRepository,
)
from application.services.async_qualifier import async_qualifier_access as access
from application.services.async_qualifier import async_qualifier_notifications as notifications
from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.async_qualifier.async_qualifier_config import validate_async_qualifier_config
from application.services.async_qualifier.async_qualifier_draw import AsyncQualifierDraw
from application.services.async_qualifier.async_qualifier_expiry import RunExpiryMixin
from application.services.async_qualifier.async_qualifier_pools import PoolManagementMixin
from application.services.async_qualifier.async_qualifier_reads import PlayerReadsMixin
from application.services.async_qualifier.async_qualifier_review import RunReviewMixin
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.tenant_membership_service import TenantMembershipService
from application.utils.safe_url import normalize_http_url
from models import (
    AsyncQualifier,
    AsyncQualifierReviewStatus,
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    FeatureFlag,
    User,
)

logger = logging.getLogger(__name__)

# Upper bound on a submitted finish time (7 days). The finish time is free text
# the runner types, so without a ceiling a typo ("100:00:00" → 360,000s is fine,
# but "1000000:00:00" is not) reaches the column as an out-of-range integer. Any
# real qualifier run is orders of magnitude under this.
MAX_RUN_SECONDS = 7 * 24 * 60 * 60

# Terminal run states a finished/forfeit/DQ run can be in (used for slot counting).
_TERMINAL = {
    AsyncQualifierRunStatus.FINISHED,
    AsyncQualifierRunStatus.FORFEIT,
    AsyncQualifierRunStatus.DISQUALIFIED,
}


class AsyncQualifierService(
    PoolManagementMixin, PlayerReadsMixin, RunExpiryMixin, RunReviewMixin,
):
    """CRUD + run execution + review + scoring for async qualifiers.

    Pool and permalink management comes from
    :class:`~application.services.async_qualifier.async_qualifier_pools.PoolManagementMixin`;
    the competitor-facing reads (open qualifiers, run availability, own runs,
    reattempt allowance, the leaderboard) from
    :class:`~application.services.async_qualifier.async_qualifier_reads.PlayerReadsMixin`;
    the automatic forfeit of an abandoned run, and its warning, from
    :class:`~application.services.async_qualifier.async_qualifier_expiry.RunExpiryMixin`.
    What stays here is the qualifier itself, the run lifecycle, and review.
    """

    def __init__(self) -> None:
        self.repository = AsyncQualifierRepository()
        self.pool_repository = AsyncQualifierPoolRepository()
        self.permalink_repository = AsyncQualifierPermalinkRepository()
        self.run_repository = AsyncQualifierRunRepository()
        self.note_repository = AsyncQualifierReviewNoteRepository()
        self.preset_repository = PresetRepository()
        self.audit_service = AuditService()
        self.draw = AsyncQualifierDraw(
            repository=self.repository,
            pool_repository=self.pool_repository,
            permalink_repository=self.permalink_repository,
            run_repository=self.run_repository,
        )

    # ============================================================ management

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def list_qualifiers(self, actor: Optional[User]) -> List[AsyncQualifier]:
        """The qualifiers ``actor`` may administer and review.

        Every one for STAFF and ``QUALIFIER_ADMIN``; for a per-qualifier reviewer,
        only the ones that list them. Anyone else is refused.
        """
        if await AuthService.can_admin_qualifier(actor):
            return await self.repository.list_all()
        assigned = await self.repository.list_administered_by(actor.id) if actor else []
        await AuthService.ensure(bool(assigned), "Cannot administer qualifiers")
        return assigned

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def get_qualifier(self, actor: Optional[User], qualifier_id: int) -> AsyncQualifier:
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier)
        return qualifier

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def create_qualifier(
        self,
        actor: Optional[User],
        *,
        name: str,
        description: Optional[str] = None,
        event_name: Optional[str] = None,
        opens_at: Optional[datetime] = None,
        closes_at: Optional[datetime] = None,
        runs_per_pool: int = 1,
        allowed_reattempts: int = 0,
        is_active: bool = True,
        config: Optional[dict] = None,
    ) -> AsyncQualifier:
        await access.ensure_qualifier_admin(actor, message="Cannot administer qualifiers")
        name = (name or '').strip()
        if not name:
            raise ValueError("Qualifier name is required")
        runs_per_pool, allowed_reattempts = rules.validate_counts(runs_per_pool, allowed_reattempts)
        rules.validate_window(opens_at, closes_at)
        config = validate_async_qualifier_config(config)
        qualifier = await self.repository.create(
            name=name,
            description=(description or '').strip() or None,
            event_name=(event_name or '').strip() or None,
            opens_at=opens_at,
            closes_at=closes_at,
            runs_per_pool=runs_per_pool,
            allowed_reattempts=allowed_reattempts,
            is_active=is_active,
            config=config,
        )
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_CREATED,
            {'qualifier_id': qualifier.id, 'name': name},
        )
        # A qualifier created inside its own window is open now, and a subscriber
        # should not have to wait for the worker's next tick to hear it.
        await self.sync_window_state(qualifier)
        return qualifier

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def update_qualifier(
        self,
        actor: Optional[User],
        qualifier_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        event_name: Optional[str] = None,
        opens_at: Optional[datetime] = None,
        closes_at: Optional[datetime] = None,
        runs_per_pool: Optional[int] = None,
        allowed_reattempts: Optional[int] = None,
        is_active: Optional[bool] = None,
        config: Optional[dict] = None,
        clear_window: bool = False,
    ) -> AsyncQualifier:
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier)
        changes: dict = {}
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Qualifier name is required")
            changes['name'] = name
        if description is not None:
            changes['description'] = description.strip() or None
        if event_name is not None:
            changes['event_name'] = event_name.strip() or None
        new_opens = opens_at if opens_at is not None or clear_window else qualifier.opens_at
        new_closes = closes_at if closes_at is not None or clear_window else qualifier.closes_at
        if opens_at is not None or closes_at is not None or clear_window:
            rules.validate_window(new_opens, new_closes)
            changes['opens_at'] = new_opens
            changes['closes_at'] = new_closes
        new_rpp = qualifier.runs_per_pool if runs_per_pool is None else runs_per_pool
        new_ar = qualifier.allowed_reattempts if allowed_reattempts is None else allowed_reattempts
        if runs_per_pool is not None or allowed_reattempts is not None:
            new_rpp, new_ar = rules.validate_counts(new_rpp, new_ar)
            changes['runs_per_pool'] = new_rpp
            changes['allowed_reattempts'] = new_ar
        if is_active is not None:
            changes['is_active'] = is_active
        if config is not None:
            changes['config'] = validate_async_qualifier_config(config)
        qualifier = await self.repository.update(qualifier, **changes)
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_UPDATED,
            {'qualifier_id': qualifier.id, 'fields': sorted(changes.keys())},
        )
        # An edit that moves the window, or flips ``is_active``, can cross the
        # boundary immediately — and switching a qualifier off takes it out of the
        # worker's active scan, so this is the only place that crossing is seen.
        await self.sync_window_state(qualifier)
        return qualifier

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def delete_qualifier(self, actor: Optional[User], qualifier_id: int) -> None:
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier)
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_DELETED,
            {'qualifier_id': qualifier.id, 'name': qualifier.name},
        )
        await self.repository.delete(qualifier)

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def add_admin(self, actor: Optional[User], qualifier_id: int, target: User) -> None:
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier)
        await TenantMembershipService.require_community_users([target.id], actor)
        await qualifier.admins.add(target)
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_ADMIN_GRANTED,
            {'qualifier_id': qualifier.id, 'target_user_id': target.id},
        )

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def remove_admin(self, actor: Optional[User], qualifier_id: int, target: User) -> None:
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier)
        await qualifier.admins.remove(target)
        await self.audit_service.write_log(
            actor, AuditActions.ASYNC_QUALIFIER_ADMIN_REVOKED,
            {'qualifier_id': qualifier.id, 'target_user_id': target.id},
        )

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def list_admins(self, actor: Optional[User], qualifier_id: int) -> List[User]:
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier)
        return await qualifier.admins.all()

    # =============================================================== player

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def start_run(self, user: User, qualifier_id: int, pool_id: int) -> AsyncQualifierRun:
        """Atomically draw a permalink and open a run (reveal == start).

        Row-locks the player so concurrent clicks serialize, enforces one active
        run + the per-pool cap + no-repeat inside the transaction, then picks a
        permalink by imbalance-forcing fairness.
        """
        if user is None:
            raise ValueError("You must be logged in to start a run")
        qualifier = await self._require_qualifier(qualifier_id)
        rules.ensure_window_open(qualifier)
        pool = await self._require_pool(pool_id)
        if pool.qualifier_id != qualifier_id:
            raise ValueError("Pool does not belong to this qualifier")

        async with in_transaction():
            await self.run_repository.lock_user_for_draw(user.id)
            active = await self.run_repository.get_active_for_user(qualifier_id, user.id)
            if active is not None:
                raise ValueError("You already have a run in progress")
            used = await self.run_repository.count_valid_runs_for_user_in_pool(pool_id, user.id)
            if used >= qualifier.runs_per_pool:
                raise ValueError("You have used all your runs for this pool")
            permalink = await self.draw.pick_permalink(qualifier, pool, user.id)
            if permalink is None:
                raise ValueError("No permalinks left to draw in this pool")
            run = await self.run_repository.create(
                qualifier_id=qualifier_id,
                user_id=user.id,
                permalink_id=permalink.id,
                status=AsyncQualifierRunStatus.IN_PROGRESS,
                started_at=datetime.now(timezone.utc),
            )
        run.permalink = permalink
        await self.audit_service.write_log(
            user, AuditActions.ASYNC_QUALIFIER_RUN_STARTED,
            {'qualifier_id': qualifier_id, 'run_id': run.id, 'pool_id': pool_id},
        )
        return run

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def submit_run(
        self, user: User, run_id: int, *, elapsed_seconds: int, runner_vod_url: Optional[str] = None
    ) -> AsyncQualifierRun:
        run = await self._require_own_active_run(user, run_id)
        vod_url = normalize_http_url(runner_vod_url, 'The VoD link')
        if elapsed_seconds is None or elapsed_seconds <= 0:
            raise ValueError("Finish time must be a positive number of seconds")
        if elapsed_seconds > MAX_RUN_SECONDS:
            raise ValueError("Finish time is longer than a week — check the value you entered.")
        # Both bounds above come before the clock is read: a nonsense value is
        # refused as nonsense, not as a discrepancy.
        measured = rules.measure_elapsed(run.started_at)
        if rules.classify_claim(elapsed_seconds, measured) is rules.ClaimVerdict.IMPOSSIBLE:
            raise ValueError(rules.describe_claim(elapsed_seconds, measured))
        run = await self.run_repository.update(
            run,
            status=AsyncQualifierRunStatus.FINISHED,
            finished_at=datetime.now(timezone.utc),
            elapsed_seconds=elapsed_seconds,
            measured_seconds=measured,
            runner_vod_url=vod_url,
            review_status=AsyncQualifierReviewStatus.PENDING,
        )
        await self.audit_service.write_and_publish(
            user, AuditActions.ASYNC_QUALIFIER_RUN_SUBMITTED,
            {'run_id': run.id, 'qualifier_id': run.qualifier_id, 'elapsed_seconds': elapsed_seconds,
             'measured_seconds': measured},
            EventType.ASYNC_QUALIFIER_RUN_SUBMITTED,
            event_details={
                'run_id': run.id, 'qualifier_id': run.qualifier_id, 'user_id': user.id,
            },
        )
        return run

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def forfeit_run(self, user: User, run_id: int) -> AsyncQualifierRun:
        """Forfeit is irreversible, scores zero, and blocks replay unless a
        reattempt is spent."""
        run = await self._require_own_active_run(user, run_id)
        run = await self.run_repository.update(
            run,
            status=AsyncQualifierRunStatus.FORFEIT,
            finished_at=datetime.now(timezone.utc),
            score=0.0,
            review_status=AsyncQualifierReviewStatus.APPROVED,
        )
        await self.audit_service.write_and_publish(
            user, AuditActions.ASYNC_QUALIFIER_RUN_FORFEITED,
            {'run_id': run.id, 'qualifier_id': run.qualifier_id},
            EventType.ASYNC_QUALIFIER_RUN_FORFEITED,
            event_details={
                'run_id': run.id, 'qualifier_id': run.qualifier_id, 'user_id': user.id,
            },
        )
        return run

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def reattempt_run(self, user: User, run_id: int, *, reason: str) -> AsyncQualifierRun:
        """Void a prior terminal run so its pool slot frees up for a fresh draw.

        Requires a reason, is limited by ``allowed_reattempts``, and never touches
        an in-progress run (finish or forfeit it first).
        """
        run = await self.run_repository.get_by_id(run_id)
        if run is None or run.user_id != user.id:
            raise NotFoundError("Run not found")
        reason = (reason or '').strip()
        if not reason:
            raise ValueError("A reattempt reason is required")
        if run.reattempted:
            raise ValueError("This run was already reattempted")
        if run.status not in _TERMINAL:
            raise ValueError("Only a finished or forfeited run can be reattempted")
        qualifier = await self._require_qualifier(run.qualifier_id)
        allowance = rules.ReattemptAllowance(
            spent=await self._count_reattempts(user.id, run.qualifier_id),
            allowed=qualifier.allowed_reattempts,
        )
        if allowance.remaining < 1:
            raise ValueError("No reattempts remaining")
        run = await self._void_run(run, reason=reason)
        await self.audit_service.write_and_publish(
            user, AuditActions.ASYNC_QUALIFIER_RUN_REATTEMPTED,
            {'run_id': run.id, 'qualifier_id': run.qualifier_id},
            EventType.ASYNC_QUALIFIER_RUN_REATTEMPTED,
            event_details={
                'run_id': run.id, 'qualifier_id': run.qualifier_id, 'user_id': user.id,
                'granted': False,
            },
        )
        return run

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def grant_reattempt(
        self, actor: Optional[User], run_id: int, *, reason: str
    ) -> AsyncQualifierRun:
        """Void a runner's terminal run on their behalf, ignoring their allowance.

        The reviewer's override for a mis-clicked forfeit or a bad seed. Requires
        qualifier-admin and a reason; unlike :meth:`reattempt_run` it does not
        consume ``allowed_reattempts``, because the run being voided is not the
        runner's mistake. Deliberately does not check the window either — a
        reviewer may need to void a run after the qualifier closes.
        """
        run, _qualifier, _reviewer = await self._require_reviewable(
            actor, run_id, message="Cannot grant a reattempt in this qualifier",
        )
        reason = (reason or '').strip()
        if not reason:
            raise ValueError("A reattempt reason is required — the runner is told what you write here.")
        if run.reattempted:
            raise ValueError("This run was already reattempted")
        if run.status not in _TERMINAL:
            raise ValueError("Only a finished or forfeited run can be reattempted")
        run = await self._void_run(run, reason=reason, granted_by=actor)
        # Published as a reattempt rather than under its own name: to a subscriber
        # this is the same external fact — a run was voided and its slot reopened —
        # and ``granted`` says who spent it. The audit keeps the two apart because
        # only one of them costs the runner an allowance.
        await self.audit_service.write_and_publish(
            actor, AuditActions.ASYNC_QUALIFIER_REATTEMPT_GRANTED,
            {'run_id': run.id, 'qualifier_id': run.qualifier_id,
             'target_user_id': run.user_id, 'reason': reason},
            EventType.ASYNC_QUALIFIER_RUN_REATTEMPTED,
            # ``<fk>_id`` is generated by Tortoise at runtime, invisible to mypy.
            event_details={
                'run_id': run.id, 'qualifier_id': run.qualifier_id,  # type: ignore[attr-defined]
                'user_id': run.user_id, 'granted': True,  # type: ignore[attr-defined]
            },
        )
        await notifications.notify_reattempt_granted(run, reason)
        return run



    # ============================================================= internals

    async def _require_qualifier(self, qualifier_id: int) -> AsyncQualifier:
        return await access.require_qualifier(self.repository, qualifier_id)

    async def _require_own_active_run(self, user: User, run_id: int) -> AsyncQualifierRun:
        run = await self.run_repository.get_by_id(run_id)
        if run is None or run.user_id != user.id:
            raise NotFoundError("Run not found")
        if run.status != AsyncQualifierRunStatus.IN_PROGRESS:
            raise ValueError("This run is no longer in progress")
        return run

    async def _count_reattempts(self, user_id: int, qualifier_id: int) -> int:
        """Reattempts this player spent themselves — a reviewer's grant is not theirs."""
        return await self.run_repository.count_self_spent_reattempts(qualifier_id, user_id)

    # feature-gate: exempt — a sibling service's continuation, already past the gate
    # its own entry method enforced; refusing here would abandon runs it just wrote
    # with a stale par.
    async def recompute_par_and_scores(self, permalink_id: int) -> None:
        """Public entry for sibling services (the live-race capture path) that add
        approved runs on a permalink and need its par + scores refreshed."""
        await self.draw.recompute_par_and_scores(permalink_id)

    async def _void_run(
        self, run: AsyncQualifierRun, *, reason: str, granted_by: Optional[User] = None
    ) -> AsyncQualifierRun:
        """Mark a terminal run reattempted, freeing its pool slot, and refresh par.

        Shared by the runner's own reattempt and a reviewer's grant so neither can
        forget the par refresh — a voided run left in the scoring inputs silently
        skews every score on its permalink.
        """
        run = await self.run_repository.update(
            run, reattempted=True, reattempt_reason=reason,
            reattempt_granted_by_id=granted_by.id if granted_by is not None else None,
            # A voided run counts for nothing, so it must not keep the score it had.
            # The recompute below rescores the permalink's approved set, which this
            # run has just left, so it would never be reached there.
            score=None,
        )
        if run.permalink_id is not None:
            await self.draw.recompute_par_and_scores(run.permalink_id)
        return run
