"""A match's players keep their order after one of them is updated.

Postgres hands back rows in heap order when a query has no ORDER BY, and an
UPDATE writes a new row version at the end of the heap. Seating a match writes
each player's station, so "Racer Five vs Racer Six" came back as "Racer Six vs
Racer Five" on every board the moment it was checked in. SQLite keeps rowid
order, so only the real backend shows it. Which order Postgres picks depends on
the plan (heap scan, or the (match, user) index), so the test covers both.

Run with:

    WIZZROBE_TEST_DB_URL=postgres://user:pass@localhost:5432/db \\
        poetry run pytest tests/postgres -q
"""

import pytest

from models import Match, MatchPlayers, Tournament, User
from tests.conftest import ON_POSTGRES

pytestmark = pytest.mark.skipif(
    not ON_POSTGRES,
    reason="heap order only exists on PostgreSQL; set WIZZROBE_TEST_DB_URL",
)


async def test_seating_the_first_player_does_not_swap_the_names(db):
    tournament = await Tournament.create(name='Dev Tourney', tenant_id=1)
    match = await Match.create(tournament=tournament, tenant_id=1)
    # The second account is older, so an index walk gets the order wrong too.
    second = await User.create(discord_id=900_202, username='racer_six')
    first = await User.create(discord_id=900_201, username='racer_five')
    seat_first = await MatchPlayers.create(match=match, user=first, tenant_id=1)
    await MatchPlayers.create(match=match, user=second, tenant_id=1)

    seat_first.assigned_station = '12'
    await seat_first.save(update_fields=['assigned_station'])

    fetched = await Match.get(id=match.id).prefetch_related('players__user')
    assert [p.user.username for p in fetched.players] == ['racer_five', 'racer_six']
