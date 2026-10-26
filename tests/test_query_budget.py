"""Queries-per-render guard for the hottest read paths.

[scaling-roadmap.md](../docs/scaling-roadmap.md) names this the primary
regression metric and notes that no such test existed. Its reasoning: wall time
on the load rig varies ±15% run to run so nothing under a ~20% change can be
validated there, while a query count is exact and machine-independent. The wins
it protects are large — the tenant home went from 377 queries per render to 26
once a triple-load of the match table was found.

Two assertions per path, and the first is the one that matters:

* **Shape.** The same call against a small fixture and a larger one must issue
  the *same* number of queries. A dropped ``prefetch_related`` turns that into
  "grows with the row count", which is the N+1 this catches — with no magic
  constant to re-tune whenever the fixture changes.
* **Ceiling.** An absolute upper bound, which catches the other regression the
  roadmap records: the same data being loaded more than once per render.

These exercise the service methods the pages call, not a browser render. The
Vue/Quasar element tree issues no queries, so the data path is where the count
comes from; rendering itself is covered by the `/ui-validation` browser loop.

Both budgets go through ``MatchDisplayService.get_matches_for_display`` because
that is what ``MatchTableView.refresh`` actually calls — and the formatter it
feeds touches every prefetched relation, which is what makes a dropped prefetch
show up here as a per-row query. A loader whose result nothing walks would count
the same with or without its prefetches, so guarding one would prove nothing.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from tortoise import connections

from application.services.event_service import EventService
from application.services.match.match_display_service import MatchDisplayService
from application.tenant_context import require_tenant_id
from models import Match, MatchPlayers, Stage, Tournament, User

pytestmark = pytest.mark.usefixtures("db")

COUNTED_METHODS = ("execute_query", "execute_query_dict", "execute_insert", "execute_many")


@contextmanager
def count_queries():
    """Count statements the default connection executes inside the block."""
    client = connections.get("default")
    tally = {"n": 0}

    # The originals are bound methods off the class, so restoring means removing
    # the instance attribute — re-assigning a captured bound method would leave
    # the client permanently shadowed for the rest of the session.
    for name in COUNTED_METHODS:
        def wrap(original):
            async def counted(*args, **kwargs):
                tally["n"] += 1
                return await original(*args, **kwargs)
            return counted

        client.__dict__[name] = wrap(getattr(client, name))
    try:
        yield tally
    finally:
        for name in COUNTED_METHODS:
            client.__dict__.pop(name, None)


async def warm_event_cache() -> None:
    """Load the community's events once, as any earlier request in a running process has."""
    await EventService.events_for_tenant(require_tenant_id())


async def seed_matches(count: int, *, offset: int = 0) -> None:
    """``count`` matches, each with its own tournament, stage and two players.

    Deliberately *not* shared between the two fixture sizes: every relation a
    match hangs off is distinct per row, so a missing prefetch shows up as a
    per-row query rather than being masked by Tortoise reusing one cached
    parent object.
    """
    base = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)
    for i in range(offset, offset + count):
        tournament = await Tournament.create(name=f"Tournament {i}")
        stage = await Stage.create(name=f"Stage {i}")
        match = await Match.create(
            tournament=tournament,
            stage=stage,
            scheduled_at=base + timedelta(hours=i),
        )
        for p in range(2):
            user = await User.create(discord_id=100_000 + i * 10 + p, username=f"p{i}_{p}")
            await MatchPlayers.create(match=match, user=user)


class TestMatchTableQueryBudget:
    """``MatchTableView.refresh`` — the schedule tab, and the busiest read here."""

    SMALL, LARGE = 3, 12
    # Measured 9. The regression this bounds is the same data loaded twice, which
    # would be 18 — the headroom is for a legitimate extra lookup, not a re-load.
    # Raised to 12 for the broadcaster prefetch, the third crew role.
    CEILING = 12

    async def _count_for(self, rows: int, *, offset: int = 0) -> int:
        await seed_matches(rows, offset=offset)
        service = MatchDisplayService()
        await warm_event_cache()
        with count_queries() as tally:
            result = await service.get_matches_for_display()
        assert len(result) == rows, "fixture did not produce the expected rows"
        return tally["n"]

    async def test_query_count_does_not_grow_with_row_count(self):
        small = await self._count_for(self.SMALL)
        await Match.all().delete()
        large = await self._count_for(self.LARGE, offset=100)
        assert small == large, (
            f"get_matches_for_display issued {small} queries for {self.SMALL} matches "
            f"and {large} for {self.LARGE} — the count scales with rows, so a relation "
            f"is being loaded per row. Add it to the repository's prefetch_related."
        )

    async def test_query_count_stays_under_the_ceiling(self):
        count = await self._count_for(self.LARGE)
        assert count <= self.CEILING, (
            f"get_matches_for_display issued {count} queries (budget {self.CEILING}). "
            f"If the extra queries are genuinely needed, raise CEILING deliberately — "
            f"see docs/scaling-roadmap.md on why this metric is the one that is guarded."
        )


class TestPlayerBoardQueryBudget:
    """The same table filtered to one player — the home Player tab's board.

    A different query plan (a join through ``players__user``) reaching the same
    formatter, so it can regress independently of the unfiltered board above.
    """

    SMALL, LARGE = 3, 12
    # Measured 9, same as the unfiltered board: the filter narrows rows, it does
    # not change how many statements the load takes. 12 with broadcasters.
    CEILING = 12

    async def _count_for(self, rows: int, *, offset: int = 0) -> int:
        await seed_matches(rows, offset=offset)
        # Every seeded match has a player numbered 0; filter to the last one so
        # exactly one row comes back however many were seeded.
        last = offset + rows - 1
        service = MatchDisplayService()
        await warm_event_cache()
        with count_queries() as tally:
            result = await service.get_matches_for_display(
                user_discord_id=str(100_000 + last * 10),
                only_upcoming=True,
            )
        assert len(result) == 1, "fixture did not produce the expected row"
        return tally["n"]

    async def test_query_count_does_not_grow_with_row_count(self):
        small = await self._count_for(self.SMALL)
        await Match.all().delete()
        large = await self._count_for(self.LARGE, offset=100)
        assert small == large, (
            f"the player board issued {small} queries over {self.SMALL} matches and "
            f"{large} over {self.LARGE} — the count tracks the table, not the result."
        )

    async def test_query_count_stays_under_the_ceiling(self):
        count = await self._count_for(self.LARGE)
        assert count <= self.CEILING, (
            f"the player board issued {count} queries (budget {self.CEILING})."
        )


class TestQualifierAvailabilityQueryBudget:
    """``get_run_availability`` — what the player's qualifier page opens on.

    Its shape is per *pool* rather than per row, which is why it belongs here as its
    own class: the audit measured about five and a half queries a pool (22 for four
    pools), because the candidate list, the seed count and the spent count each went
    to the database separately and the first two re-read permalinks the pool read had
    already prefetched.
    """

    SMALL, LARGE = 1, 4
    # Measured 5 flat — the qualifier, its pools with permalinks prefetched, this
    # player's played permalinks, their spent counts, and the flag resolution behind
    # the service's own gate. Before the batch it was 7 / 19 / 51 for 1 / 4 / 12
    # pools, which is the shape this class exists to keep from coming back.
    CEILING = 7

    async def _count_for(self, pools: int, *, discord_id: int) -> int:
        from application.services.async_qualifier.async_qualifier_service import (
            AsyncQualifierService,
        )
        from models import Role, UserRole

        service = AsyncQualifierService()
        staff = await User.create(discord_id=discord_id, username=f"qbudget{discord_id}")
        await UserRole.create(user=staff, role=Role.STAFF, tenant_id=1)
        now = datetime.now(timezone.utc)
        qualifier = await service.create_qualifier(
            staff, name=f"Budget Q {pools}", opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=1), runs_per_pool=1,
        )
        for i in range(pools):
            pool = await service.create_pool(staff, qualifier.id, name=f"Pool {i}")
            await service.add_permalink(
                staff, pool.id, url=f"https://seed.test/{discord_id}-{i}")
        player = await User.create(discord_id=discord_id + 1, username=f"qplayer{discord_id}")

        with count_queries() as tally:
            result = await service.get_run_availability(player, qualifier.id)
        assert len(result.pools) == pools, "fixture did not produce the expected pools"
        return tally["n"]

    async def test_query_count_does_not_grow_with_pool_count(self):
        small = await self._count_for(self.SMALL, discord_id=940_100)
        large = await self._count_for(self.LARGE, discord_id=940_200)
        assert small == large, (
            f"get_run_availability issued {small} queries for {self.SMALL} pool(s) and "
            f"{large} for {self.LARGE} — the count scales with pools, so the "
            f"per-pool loop is back. Batch the reads in _pool_usage instead."
        )

    async def test_query_count_stays_under_the_ceiling(self):
        count = await self._count_for(self.LARGE, discord_id=940_300)
        assert count <= self.CEILING, (
            f"get_run_availability issued {count} queries (budget {self.CEILING})."
        )


class TestTheGuardItself:
    """The counter has to actually see queries, or every budget above is vacuous."""

    async def test_counter_sees_queries(self):
        await seed_matches(2)
        with count_queries() as tally:
            await Match.all()
        assert tally["n"] >= 1

    async def test_counter_restores_the_client(self):
        client = connections.get("default")
        with count_queries():
            assert all(name in client.__dict__ for name in COUNTED_METHODS)
        # Back to the class methods: no instance attribute is left shadowing them.
        assert not any(name in client.__dict__ for name in COUNTED_METHODS)

    async def test_an_unprefetched_relation_is_visibly_worse(self):
        """A deliberate N+1 must produce a per-row count, or the shape test is blind."""
        await seed_matches(4)
        with count_queries() as tally:
            for match in await Match.all():
                await match.tournament
        assert tally["n"] >= 5, "per-row relation access did not register as extra queries"
