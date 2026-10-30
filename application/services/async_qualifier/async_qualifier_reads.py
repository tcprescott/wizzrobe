"""Async qualifier — the player's read surface and the leaderboard.

Everything a competitor's own pages ask for: which qualifiers are open, what this
one looks like from outside the admin gate, whether they can start a run (and if
not, why not), their own runs, their reattempt allowance, and the board.

Mixed into :class:`AsyncQualifierService` — the same composition
``application/services/_bracket/`` uses — so the orchestration module stays inside
the file-length guideline. Reads only: nothing here writes or audits.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional

from application.feature_flags import requires_feature
from application.repositories import PlayedPermalinks
from application.services.async_qualifier import async_qualifier_access as access
from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.async_qualifier.async_qualifier_scoring import (
    LeaderboardEntry,
    ScoreBand,
    ScoredRun,
    build_leaderboard,
    score_band,
)
from application.utils.timezone import format_local_display
from models import (
    AsyncQualifier,
    AsyncQualifierPool,
    AsyncQualifierReviewStatus,
    AsyncQualifierRun,
    AsyncQualifierRunStatus,
    FeatureFlag,
    User,
)

if TYPE_CHECKING:  # typing only — importing these at runtime would cycle
    from application.repositories import (
        AsyncQualifierPoolRepository,
        AsyncQualifierRepository,
        AsyncQualifierRunRepository,
    )
    from application.services.async_qualifier.async_qualifier_draw import AsyncQualifierDraw


@dataclass(frozen=True)
class OwnRun:
    """One of the caller's own runs, as the caller is allowed to see it.

    A projection rather than the model, for two reasons the audit found the hard
    way. **The exact score is exactly solvable for par** — the runner knows their
    own elapsed, so ``par = elapsed / (2 - score/100)`` — which defeats the
    active-window lockdown; here it is ``None`` until results are public and only
    ``score_band`` is offered. And the model carries **less** than the runner
    needs: the seed they played, why a reviewer voided a run, and whether a
    forfeit was theirs or the clock's were all on the record and on no screen.

    Returning a frozen projection rather than masking the model is deliberate: a
    model with its score nulled for display is one `save()` away from writing that
    null back.
    """

    id: int
    pool_name: str
    permalink_url: Optional[str]
    status: AsyncQualifierRunStatus
    review_status: AsyncQualifierReviewStatus
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    deadline: Optional[datetime]
    elapsed_seconds: Optional[int]
    measured_seconds: Optional[int]
    runner_vod_url: Optional[str]
    reattempted: bool
    reattempt_reason: Optional[str]
    reattempt_was_granted: bool
    expired_at: Optional[datetime]
    score: Optional[float]
    score_band: Optional[ScoreBand]
    notes: List[str]

    @property
    def is_reattemptable(self) -> bool:
        """Whether spending a reattempt on this run would do anything."""
        return not self.reattempted and self.status in _REATTEMPTABLE

    @property
    def was_expired(self) -> bool:
        return self.expired_at is not None

    @property
    def latest_note(self) -> str:
        """The most recent reviewer note — the reason behind the current verdict."""
        return self.notes[-1] if self.notes else ''

    @property
    def awaits_review(self) -> bool:
        """Whether a review verdict is a thing this run is still waiting for.

        An in-progress run is ``PENDING`` in the column too, which read as "a
        reviewer is looking at this" when nobody had anything to look at yet.
        """
        return (self.status == AsyncQualifierRunStatus.FINISHED
                and self.review_status == AsyncQualifierReviewStatus.PENDING)


_REATTEMPTABLE = frozenset({
    AsyncQualifierRunStatus.FINISHED,
    AsyncQualifierRunStatus.FORFEIT,
    AsyncQualifierRunStatus.DISQUALIFIED,
})


@dataclass(frozen=True)
class RunAvailability:
    """Whether this player can start a run, and if not, why not.

    ``pools`` is what they may draw from; ``usage`` is every pool with the
    player's spent/remaining counts, so the surface can show what is left without a
    second read.
    """

    pools: List[AsyncQualifierPool]
    usage: List[rules.PoolUsage]
    reason: Optional[rules.RunUnavailableReason]
    message: str


class PlayerReadsMixin:
    """The competitor-facing reads.

    The members below are supplied by :class:`AsyncQualifierService`, which composes
    this mixin. Declared (annotation only, so nothing shadows the real ones at
    runtime) to state the contract a composer must satisfy — the same shape
    :class:`RunExpiryMixin` uses.
    """

    repository: 'AsyncQualifierRepository'
    pool_repository: 'AsyncQualifierPoolRepository'
    run_repository: 'AsyncQualifierRunRepository'
    draw: 'AsyncQualifierDraw'
    _require_qualifier: Callable[[int], Awaitable[AsyncQualifier]]

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def list_open_qualifiers(self) -> List[AsyncQualifier]:
        """Active qualifiers, newest first (the player-facing list)."""
        return await self.repository.list_active()

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def get_qualifier_for_player(self, qualifier_id: int) -> AsyncQualifier:
        """A qualifier's public shell (name/window) for the player pages — no
        admin gate. Pools/pars/other entrants' runs stay behind the lockdown in
        the methods that return them."""
        return await self._require_qualifier(qualifier_id)

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def get_player_pools(
        self, user: Optional[User], qualifier_id: int
    ) -> List[AsyncQualifierPool]:
        """Pools a player may still draw from: within window, slots remaining,
        and at least one undrawn non-live-race permalink left.

        Raises for a shut window; kept for the REST client that relies on that.
        The web surface calls :meth:`get_run_availability`, which answers instead.
        """
        qualifier = await self._require_qualifier(qualifier_id)
        rules.ensure_window_open(qualifier)
        if user is None:
            return []
        pools, usage = await self._pool_usage(qualifier, user)
        return [p for p in pools if usage[p.id].has_candidates and usage[p.id].slots_left > 0]

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def get_run_availability(
        self, user: Optional[User], qualifier_id: int
    ) -> RunAvailability:
        """Whether this player can start a run, and if not, exactly why not.

        Deliberately does not raise on a shut window: "the window is closed" is
        the answer the runner needs, not an error the page has to translate. Every
        no-run case names what to do about it — wait, ask an organiser, or nothing.
        """
        qualifier = await self._require_qualifier(qualifier_id)
        window = rules.window_reason(qualifier)
        if user is None:
            reason = window or rules.RunUnavailableReason.ANONYMOUS
            return RunAvailability(pools=[], usage=[], reason=reason,
                                   message=self._describe(qualifier, reason))
        if window is not None:
            return RunAvailability(pools=[], usage=[], reason=window,
                                   message=self._describe(qualifier, window))
        pools, usage = await self._pool_usage(qualifier, user)
        usages = [usage[p.id] for p in pools if usage[p.id].seeded]
        reason = rules.classify_availability(usages)
        eligible = [p for p in pools if usage[p.id].has_candidates and usage[p.id].slots_left > 0]
        return RunAvailability(
            pools=eligible, usage=usages, reason=reason,
            message='' if reason is None else self._describe(qualifier, reason),
        )

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def list_user_runs(self, user: User, qualifier_id: int) -> List[OwnRun]:
        """The caller's own runs, projected to what they may see and need.

        Returns :class:`OwnRun` rather than the model so the score lockdown cannot
        be forgotten by a caller — the web page and ``GET /{id}/me/runs`` share
        this one decision instead of each reading ``run.score`` and hoping.
        """
        qualifier = await self._require_qualifier(qualifier_id)
        exact = self.is_results_public(qualifier)
        runs = await self.run_repository.list_for_user(qualifier_id, user.id)
        return [self._own_run(run, qualifier, exact_scores=exact) for run in runs]

    def _own_run(
        self, run: AsyncQualifierRun, qualifier: AsyncQualifier, *, exact_scores: bool
    ) -> OwnRun:
        pool = run.permalink.pool if run.permalink and run.permalink.pool else None
        return OwnRun(
            id=run.id,
            pool_name=pool.name if pool else '—',
            permalink_url=run.permalink.url if run.permalink else None,
            status=run.status,
            review_status=run.review_status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            deadline=(rules.run_deadline(qualifier, run.started_at)
                      if run.status == AsyncQualifierRunStatus.IN_PROGRESS else None),
            elapsed_seconds=run.elapsed_seconds,
            measured_seconds=run.measured_seconds,
            runner_vod_url=run.runner_vod_url,
            reattempted=run.reattempted,
            reattempt_reason=run.reattempt_reason,
            reattempt_was_granted=run.reattempt_granted_by_id is not None,  # type: ignore[attr-defined]
            expired_at=run.expired_at,
            score=run.score if exact_scores else None,
            # Only a finished run gets a band: a forfeit's 0 would read as "above par".
            score_band=(score_band(run.score)
                        if run.status == AsyncQualifierRunStatus.FINISHED else None),
            notes=[
                n.note for n in sorted(
                    (n for n in getattr(run, 'review_notes', []) or [] if n.note),
                    key=lambda n: (n.created_at, n.id),
                )
            ],
        )

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def get_active_run(self, user: User, qualifier_id: int) -> Optional[AsyncQualifierRun]:
        run = await self.run_repository.get_active_for_user(qualifier_id, user.id)
        if run is not None:
            await run.fetch_related('permalink__pool')
        return run

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def get_reattempt_allowance(
        self, user: User, qualifier_id: int
    ) -> rules.ReattemptAllowance:
        """How many reattempts this player has spent and may still spend."""
        qualifier = await self._require_qualifier(qualifier_id)
        return rules.ReattemptAllowance(
            spent=await self._count_reattempts(user.id, qualifier_id),
            allowed=qualifier.allowed_reattempts,
        )

    async def _pool_usage(self, qualifier: AsyncQualifier, user: User):
        """Every pool plus this player's state in it — the one eligibility loop.

        Both the pool list and the availability read derive from this, so "may I
        draw here?" is answered in one place.

        Three queries, whatever the pool count. It was about five and a half *per
        pool* — 22 for a four-pool qualifier — because the candidate list, the seed
        count and the spent count each went to the database on their own, and the
        first two re-read the same permalinks the pool read had already prefetched.
        """
        pools = await self.pool_repository.list_for_qualifier(qualifier.id)
        pool_ids = [p.id for p in pools]
        played = await self.run_repository.played_permalink_ids_for_user_by_pool(
            pool_ids, user.id)
        spent = await self.run_repository.valid_run_counts_for_user_by_pool(pool_ids, user.id)
        usage = {}
        for pool in pools:
            candidates = self.draw.drawable(
                pool.permalinks, played.get(pool.id) or PlayedPermalinks())
            usage[pool.id] = rules.PoolUsage(
                pool_id=pool.id, name=pool.name, used=spent.get(pool.id, 0),
                allowed=qualifier.runs_per_pool, has_candidates=bool(candidates),
                seeded=self.draw.async_seeds(pool) > 0,
            )
        return pools, usage

    @staticmethod
    def _describe(qualifier: AsyncQualifier, reason) -> str:
        return rules.describe_unavailability(
            reason,
            runs_per_pool=qualifier.runs_per_pool,
            opens_display=format_local_display(qualifier.opens_at) if qualifier.opens_at else '',
            closes_display=format_local_display(qualifier.closes_at) if qualifier.closes_at else '',
        )

    def is_results_public(self, qualifier: AsyncQualifier, now: Optional[datetime] = None) -> bool:
        return rules.is_results_public(qualifier, now)

    @requires_feature(FeatureFlag.ASYNC_QUALIFIERS)
    async def get_leaderboard(
        self, actor: Optional[User], qualifier_id: int
    ) -> List[LeaderboardEntry]:
        qualifier = await self._require_qualifier(qualifier_id)
        if not self.is_results_public(qualifier):
            await access.ensure_qualifier_admin(
                actor, qualifier,
                message="The leaderboard is hidden while this qualifier is open",
            )
        pools = await self.pool_repository.list_for_qualifier(qualifier_id)
        pool_ids = [p.id for p in pools]
        open_pool_ids = [
            p.id for p in pools
            if any(not link.live_race for link in p.permalinks)
        ]
        rows = await self.run_repository.list_scored_for_leaderboard(qualifier_id)
        scored = [
            ScoredRun(
                user_id=row['user_id'],
                username=rules.display_name_of(
                    row['user__display_name'], row['user__username'], row['user_id']),
                pool_id=row['permalink__pool_id'],
                score=row['score'] if rules.run_scores(row) else 0.0,
            )
            for row in rows
        ]
        # Deterministic input order → stable ties (scoring keeps insertion order).
        scored.sort(key=lambda s: (s.username.lower(), s.user_id))
        return build_leaderboard(
            pool_ids=pool_ids, runs_per_pool=qualifier.runs_per_pool, scored_runs=scored,
            open_pool_ids=open_pool_ids,
        )
