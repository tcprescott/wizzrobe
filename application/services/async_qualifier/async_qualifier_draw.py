"""Async qualifier — draw & scoring engine.

The imbalance-forcing permalink draw and the par/score recompute lifted out of
:class:`AsyncQualifierService`. Holds the repositories it needs, composed by the
service the same way :class:`MatchService` composes ``MatchScheduleService``. It
performs no audit writes and publishes no events — those stay with the service
methods that call it.
"""

import secrets
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from tortoise.transactions import in_transaction

from application.event_writes import ensure_writable
from application.repositories import (
    AsyncQualifierPermalinkRepository,
    AsyncQualifierPoolRepository,
    AsyncQualifierRepository,
    AsyncQualifierRunRepository,
    PlayedPermalinks,
)
from application.services.async_qualifier import async_qualifier_rules as rules
from application.services.async_qualifier.async_qualifier_scoring import compute_par, compute_score
from models import (
    AsyncQualifier,
    AsyncQualifierPermalink,
    AsyncQualifierPool,
    AsyncQualifierRun,
)


class AsyncQualifierDraw:
    """Imbalance-forcing permalink draw + par/score recompute for a qualifier."""

    def __init__(
        self,
        *,
        repository: AsyncQualifierRepository,
        pool_repository: AsyncQualifierPoolRepository,
        permalink_repository: AsyncQualifierPermalinkRepository,
        run_repository: AsyncQualifierRunRepository,
    ) -> None:
        self.repository = repository
        self.pool_repository = pool_repository
        self.permalink_repository = permalink_repository
        self.run_repository = run_repository

    @staticmethod
    def drawable(
        permalinks: Iterable[AsyncQualifierPermalink], played: PlayedPermalinks
    ) -> List[AsyncQualifierPermalink]:
        """The one definition of "this player may still draw this seed".

        A seed the player has never drawn comes first. A seed they played only in a
        voided run is offered again only when the pool has nothing fresh left, so a
        reattempt lands on a new permalink whenever one exists and a small pool
        still never dead-ends.

        Pure, so a caller holding the pool's permalinks already (the availability
        read prefetches them for every pool) does not have to re-read them to ask.
        """
        open_seeds = [p for p in permalinks if not p.live_race and p.id not in played.valid]
        fresh = [p for p in open_seeds if p.id not in played.voided]
        return fresh or open_seeds

    async def draw_candidates(
        self, pool: AsyncQualifierPool, user_id: int
    ) -> List[AsyncQualifierPermalink]:
        """Permalinks a player may still draw from a pool: not live-race and not
        already played by them (no-repeat; see :meth:`drawable`)."""
        permalinks = await self.permalink_repository.list_for_pool(pool.id)
        played = await self.run_repository.played_permalink_ids_for_user_in_pool(pool.id, user_id)
        return self.drawable(permalinks, played)

    @staticmethod
    def async_seeds(pool: AsyncQualifierPool) -> int:
        """How many of a pool's permalinks a self-paced runner could ever draw.

        Distinct from :meth:`drawable`, which also excludes the ones this player
        already played. A pool whose permalinks are all live-race has none — and
        must not be reported as "you have played every seed", which is what an
        exhausted-but-drawable pool means.

        Reads the pool's prefetched ``permalinks``; the repository's pool reads all
        prefetch them.
        """
        # A prefetched ReverseRelation iterates; mypy only sees the descriptor.
        links: Iterable[AsyncQualifierPermalink] = pool.permalinks  # type: ignore[assignment]
        return sum(1 for p in links if not p.live_race)

    async def async_seed_count(self, pool: AsyncQualifierPool) -> int:
        """:meth:`async_seeds` for a pool whose permalinks are not loaded."""
        permalinks = await self.permalink_repository.list_for_pool(pool.id)
        return sum(1 for p in permalinks if not p.live_race)

    async def pick_permalink(
        self, qualifier: AsyncQualifier, pool: AsyncQualifierPool, user_id: int
    ) -> Optional[AsyncQualifierPermalink]:
        """Imbalance-forcing draw: random among candidates unless the pool's
        play-count spread crosses the threshold, then force the least-played."""
        candidates = await self.draw_candidates(pool, user_id)
        if not candidates:
            return None
        counts = await self.run_repository.valid_run_counts_by_permalink_for_pool(pool.id)
        cand_counts = {c.id: counts.get(c.id, 0) for c in candidates}
        threshold = rules.imbalance_threshold(qualifier)
        spread = max(cand_counts.values()) - min(cand_counts.values())
        if spread >= threshold:
            fewest = min(cand_counts.values())
            candidates = [c for c in candidates if cand_counts[c.id] == fewest]
        return secrets.choice(candidates)

    async def recompute_par_and_scores(self, permalink_id: int) -> None:
        """Recompute a permalink's par from its approved finished runs and
        rescore every one of them (par shifts as runs are reviewed/voided).

        The par and the scores derived from it are written in **one transaction**.
        They were a bare sequence of updates before, so a failure part-way — a lost
        connection on the fortieth of fifty rows — left a fresh par standing beside
        stale scores, which is the one state nothing downstream can detect: every
        row looks individually plausible.
        """
        permalink = await self.permalink_repository.get_by_id(permalink_id)
        if permalink is None:
            return
        approved = await self.run_repository.list_approved_finished_for_permalink(permalink_id)
        elapsed = [r.elapsed_seconds for r in approved if r.elapsed_seconds]
        sample = rules.par_sample_size(await self._qualifier_for_permalink(permalink))
        par = compute_par(elapsed, sample)
        rescored = []
        for run in approved:
            score = compute_score(run.elapsed_seconds, par)
            if score != run.score:
                # None when the permalink has no par yet — a nullable column mypy
                # reads as non-optional.
                run.score = score  # type: ignore[assignment]
                rescored.append(run)
        async with in_transaction():
            await self.permalink_repository.update(
                permalink, par_time=par, par_updated_at=datetime.now(timezone.utc)
            )
            if rescored:
                # One statement rather than one per run: a permalink at real scale
                # holds hundreds of approved runs and every review re-scores all of
                # them, inside the request the reviewer is waiting on.
                await ensure_writable(AsyncQualifierRun.filter(id__in=[run.id for run in rescored]))
                await AsyncQualifierRun.bulk_update(rescored, fields=['score'])

    async def _qualifier_for_permalink(
        self, permalink: AsyncQualifierPermalink
    ) -> Optional[AsyncQualifier]:
        pool = await self.pool_repository.get_by_id(permalink.pool_id)
        if pool is None:
            return None
        return await self.repository.get_by_id(pool.qualifier_id)
