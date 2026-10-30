"""The review queue: claiming a run, deciding it, and the notes behind it.

Split out of ``async_qualifier_service`` the way :class:`PoolManagementMixin`
and :class:`RunExpiryMixin` were, to keep that module inside the file-length
guideline: :class:`RunReviewMixin` is composed into
:class:`AsyncQualifierService` and reaches ``run_repository``,
``note_repository``, ``draw``, ``audit_service`` and ``_require_qualifier``
through that composed class.

Two rules give the queue its shape. A **claim** is advisory but named: another
reviewer holding a live claim is refused by name, because "someone has this"
leaves the reader nothing to do about it. And a run that has already been
settled is not re-settled silently — deciding it again is an **override**, which
says who settled it first.

Every public method carries ``@requires_feature(ASYNC_QUALIFIERS)``: the hook
that pairs the UI gate with the service gate reads this whole package as the
flag's owning modules.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional, Sequence, Tuple

from tortoise.transactions import in_transaction

from application.events import EventType
from application.feature_flags import requires_feature
from application.services.async_qualifier import async_qualifier_access as access
from application.services.async_qualifier import async_qualifier_notifications as notifications
from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from models import (
    AsyncQualifier,
    AsyncQualifierReviewStatus,
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    FeatureFlag,
    User,
)

if TYPE_CHECKING:  # typing only — importing these at runtime would cycle
    from application.repositories import (
        AsyncQualifierReviewNoteRepository,
        AsyncQualifierRunRepository,
    )
    from application.services.async_qualifier.async_qualifier_draw import AsyncQualifierDraw
    from application.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class RunReviewMixin:
    """List, claim, release and decide the runs waiting on a reviewer.

    The five members below are supplied by :class:`AsyncQualifierService`, which
    composes this mixin. They are *declared* (annotation only, no assignment, so
    nothing shadows the real ones at runtime) the way :class:`RunExpiryMixin`
    declares its own, stating the contract a composer must satisfy rather than
    leaving it implicit in the attribute accesses.
    """

    run_repository: 'AsyncQualifierRunRepository'
    note_repository: 'AsyncQualifierReviewNoteRepository'
    audit_service: 'AuditService'
    draw: 'AsyncQualifierDraw'
    _require_qualifier: Callable[[int], Awaitable[AsyncQualifier]]

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def list_review_queue(self, actor: Optional[User], qualifier_id: int) -> List[AsyncQualifierRun]:
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier, message="Cannot review this qualifier")
        return await self.run_repository.list_pending_review(qualifier_id)

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def count_pending_review(self, actor: Optional[User], qualifier_id: int) -> int:
        """How many runs await review — the number on the tab, not the queue itself.

        The drill-down loads one tab at a time, so a count taken from the loaded
        queue reads 0 until someone opens that tab, which is the moment it stops
        being worth telling them.
        """
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier, message="Cannot review this qualifier")
        waiting, _ = await self.run_repository.pending_review_backlog(qualifier_id)
        return waiting

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def review_queue_context(
        self, actor: Optional[User], qualifier_id: int, runs: Sequence[AsyncQualifierRun]
    ) -> dict:
        """Per-runner outcome counts for the runs in a review queue.

        Re-reviewing without seeing a runner's other attempts is how two reviewers
        reach opposite conclusions about the same person, so the queue card carries
        that context. It used to come from ``list_runs`` — every run in the
        qualifier, hydrated with two levels of prefetch, then scanned once per card
        — which is quadratic in entrants. This reads counts for the queue's own
        runners instead, so the cost tracks the queue and not the tournament.
        """
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier, message="Cannot review this qualifier")
        user_ids = {run.user_id for run in runs}  # type: ignore[attr-defined]
        return await self.run_repository.outcome_tally_for_users(qualifier_id, user_ids)

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def list_runs(
        self,
        actor: Optional[User],
        qualifier_id: int,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[AsyncQualifierRun]:
        """Every run in the qualifier, for the reviewer's runs list.

        The review queue only returns finished+pending runs, so a forfeit — which
        is written straight to approved/score 0 — is unreachable from it. This is
        the read that lets a reviewer find one and grant a reattempt on it.

        ``limit``/``offset`` page it for the API, where the whole list of a
        real qualifier's runs is megabytes. The web page passes neither: its table
        paginates in the browser and the reviewer searches across the lot.
        """
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier, message="Cannot review this qualifier")
        return await self.run_repository.list_for_qualifier(
            qualifier_id, limit=limit, offset=offset)

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def count_runs(self, actor: Optional[User], qualifier_id: int) -> int:
        """How many runs the qualifier holds — the total beside a page of them."""
        qualifier = await self._require_qualifier(qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier, message="Cannot review this qualifier")
        return await self.run_repository.count_for_qualifier(qualifier_id)

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def claim_run(self, actor: Optional[User], run_id: int) -> AsyncQualifierRun:
        """Take the review lock on a run, so two reviewers don't both work it."""
        run, _qualifier, reviewer = await self._require_reviewable(actor, run_id)
        await self._ensure_claim_free(run, reviewer)
        run = await self.run_repository.update(
            run, review_claimed_by_id=reviewer.id, review_claimed_at=datetime.now(timezone.utc)
        )
        return run

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def release_claim(self, actor: Optional[User], run_id: int) -> AsyncQualifierRun:
        """Give the lock back. Any reviewer may release any claim.

        Deliberately not restricted to the holder: the reason a claim needs
        releasing is usually that the holder has gone, and a lock only they can
        undo is a lock nobody can undo. The worker sweep is the backstop, not the
        only way out.
        """
        run, _qualifier, _reviewer = await self._require_reviewable(actor, run_id)
        run = await self.run_repository.update(
            run, review_claimed_by_id=None, review_claimed_at=None
        )
        return run

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def review_run(
        self,
        actor: Optional[User],
        run_id: int,
        *,
        approved: bool,
        note: Optional[str] = None,
        override: bool = False,
    ) -> AsyncQualifierRun:
        """Approve or reject a submitted run.

        Three ways two reviewers can collide on one run, and what each gets:

        - **One holds the claim.** The other is refused by name. The claim is a
          lock or it is decoration, and it was decoration.
        - **The run is already settled.** Refused, unless the caller passes
          ``override`` — reversing a verdict is legitimate, doing it
          indistinguishably from a first verdict is not. An override needs a
          reason, audits under its own action, and tells the runner their earlier
          result changed rather than arriving in the shape of a first verdict.
        - **Both commit at once.** The verdict is a compare-and-set on the review
          status, so the second writer loses on its stale read. Its note is
          written inside the same transaction, so a losing verdict leaves nothing
          behind — no note, no DM, no half-applied outcome.
        """
        run, _qualifier, reviewer = await self._require_reviewable(actor, run_id)
        if run.user_id == reviewer.id:
            raise ValueError("You cannot review your own run")
        if run.status != AsyncQualifierRunStatus.FINISHED:
            raise ValueError("Only a finished run can be reviewed")
        await self._ensure_claim_free(run, reviewer)
        was = run.review_status
        settled = was != AsyncQualifierReviewStatus.PENDING
        if settled and not override:
            raise ValueError(
                f"This run was already {rules.review_status_label(was)} by "
                f"{await self._reviewer_name(run)}. Reopen it as an override if you "
                "mean to change that verdict."
            )
        note = (note or '').strip()
        # Above every write, so a reasonless rejection or override changes nothing at all.
        if not approved and not note:
            raise ValueError("A rejection needs a reason — the runner is told what you write here.")
        if settled and not note:
            raise ValueError(
                "Overturning a verdict needs a reason — it goes to the runner, who "
                "was already told the old one."
            )
        new_status = (
            AsyncQualifierReviewStatus.APPROVED if approved else AsyncQualifierReviewStatus.REJECTED
        )
        changes: dict = {
            'review_status': new_status,
            'reviewed_by_id': reviewer.id,
            'reviewed_at': datetime.now(timezone.utc),
            'review_claimed_by_id': None,
            'review_claimed_at': None,
        }
        if not approved:
            # Clear here: the recompute below rescores only the approved set, which a rejection just left.
            changes['score'] = None
        async with in_transaction():
            written = await self.run_repository.settle_review(run.id, expect=was, **changes)
            if not written:
                raise ValueError(
                    "Another reviewer settled this run while you were deciding. "
                    "Reload the queue to see their verdict."
                )
            if note:
                await self.note_repository.create(run_id=run.id, author_id=reviewer.id, note=note)
        run = await self.run_repository.get_by_id(run.id) or run
        if run.permalink_id is not None:
            await self.draw.recompute_par_and_scores(run.permalink_id)
            run = await self.run_repository.get_by_id(run.id) or run
        await self.audit_service.write_and_publish(
            actor,
            (AuditActions.ASYNC_QUALIFIER_RUN_REVIEW_OVERRIDDEN if settled
             else AuditActions.ASYNC_QUALIFIER_RUN_REVIEWED),
            {'run_id': run.id, 'qualifier_id': run.qualifier_id, 'approved': approved,
             **({'previous_status': was.value} if settled else {})},
            EventType.ASYNC_QUALIFIER_RUN_REVIEWED,
            event_extra={'user_id': run.user_id, 'override': settled},
        )
        await notifications.notify_run_reviewed(run, approved, reason=note, overridden=settled)
        return run

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def get_run_notes(self, actor: Optional[User], run_id: int):
        run = await access.require_run(self.run_repository, run_id)
        qualifier = await self._require_qualifier(run.qualifier_id)
        if not (await AuthService.can_admin_qualifier(actor, qualifier)
                or (actor is not None and run.user_id == actor.id)):
            raise PermissionError("Cannot view these review notes")
        return await self.note_repository.list_for_run(run_id)

    async def _require_reviewable(
        self, actor: Optional[User], run_id: int, *, message: str = "Cannot review this qualifier"
    ) -> Tuple[AsyncQualifierRun, AsyncQualifier, User]:
        """The run, its qualifier, and the reviewer — narrowed to a real ``User``.

        Handing the actor back is what lets every caller read ``reviewer.id``
        without a redundant None check: the refusal above already guarantees it,
        but only a return type says so.
        """
        if actor is None:
            raise PermissionError("Cannot review this run")
        run = await access.require_run(self.run_repository, run_id)
        qualifier = await self._require_qualifier(run.qualifier_id)
        await access.ensure_qualifier_admin(actor, qualifier, message=message)
        return run, qualifier, actor

    async def _ensure_claim_free(self, run: AsyncQualifierRun, actor: User) -> None:
        """Refuse when another reviewer holds a live claim on this run.

        Names the holder, because "someone has this" leaves the caller with nothing
        to do about it — knowing who lets them go and ask.
        """
        holder_id = run.review_claimed_by_id
        if not holder_id or holder_id == actor.id:
            return
        if not rules.claim_is_live(run.review_claimed_at):
            return
        holder = await run.review_claimed_by
        raise ValueError(
            f"{rules.display_name(holder)} has claimed this run for review. Ask them "
            "to release it if they are done with it."
        )

    @staticmethod
    async def _reviewer_name(run: AsyncQualifierRun) -> str:
        """Who settled a run, for the message that refuses to re-settle it silently."""
        reviewer = await run.reviewed_by
        return rules.display_name(reviewer) if reviewer is not None else 'another reviewer'
