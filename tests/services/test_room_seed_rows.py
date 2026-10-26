"""The tournament-room board's selection rule.

The room screen exists to answer one question — "which seeds does someone need
in the next few minutes?" — so a row belongs on it only while the seed is rolled
and the match is still unplayed. Both halves matter: an unseeded match is
nothing to show, and a started one has already had its seed opened.
"""

from application.services.match.match_display_service import (
    MatchDisplayService,
    is_room_seed_row,
)
from application.tenant_context import tenant_scope
from models import GeneratedSeeds, Match, MatchPlayers, Tournament
from tests.conftest import DEFAULT_TEST_TENANT_ID
from tests.factories import make_user, utc


async def _match(tournament: Tournament, *, seeded: bool, **kwargs) -> Match:
    seed = None
    if seeded:
        seed = await GeneratedSeeds.create(
            seed_url='https://alttpr.com/h/abcd1234', generator='alttpr',
        )
    match = await Match.create(
        tournament=tournament, generated_seed=seed,
        scheduled_at=utc(2025, 1, 15, 19), **kwargs,
    )
    player = await make_user(discord_id=6000 + match.id, username=f'p{match.id}')
    await MatchPlayers.create(match=match, user=player)
    return match


class TestPredicate:
    def test_needs_both_a_seed_and_an_unfinished_state(self):
        assert is_room_seed_row({'generated_seed': 'https://x', 'state': 'Scheduled'})
        assert is_room_seed_row({'generated_seed': 'https://x', 'state': 'Checked In'})
        assert not is_room_seed_row({'generated_seed': '', 'state': 'Scheduled'})
        assert is_room_seed_row({'generated_seed': 'https://x', 'state': 'Started'})
        assert not is_room_seed_row({'generated_seed': 'https://x', 'state': 'Finished'})
        assert not is_room_seed_row({})


class TestQuery:
    async def test_rolled_matches_stay_until_they_finish(self, db):
        with tenant_scope(DEFAULT_TEST_TENANT_ID):
            tournament = await Tournament.create(name='Cup')
            wanted = await _match(tournament, seeded=True)
            checked_in = await _match(
                tournament, seeded=True, seated_at=utc(2025, 1, 15, 18, 50),
            )
            await _match(tournament, seeded=False)
            started = await _match(tournament, seeded=True, started_at=utc(2025, 1, 15, 19, 1))
            await _match(
                tournament, seeded=True,
                started_at=utc(2025, 1, 15, 17), finished_at=utc(2025, 1, 15, 18),
            )

            rows = await MatchDisplayService().get_room_seed_rows()

        assert {row['id'] for row in rows} == {wanted.id, checked_in.id, started.id}
        assert all(row['generated_seed'] for row in rows)
