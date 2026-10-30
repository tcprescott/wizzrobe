"""A volunteer's own record of what they signed up to crew.

Measured before this existed: the Player tab ("Your Schedule") contained no
mention of commentator, tracker or crew; there was no My Commitments anywhere;
and after signing up, the volunteer's only handle on the commitment was a
Withdraw button in one row of a 17-row board — 5,874 px of scroll on a phone.

The service half is exercised against the real schema. The page itself is the
player run sheet narrowed to the viewer's crew slots, so its copy and buttons
are covered by ``tests/theme/test_match_player_run_sheet.py``.
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.services.crew_service import CrewService
from models import Commentator, Match, Tournament, Tracker, User


@pytest.fixture
async def person(db):
    return await User.create(discord_id=9100, username='caster')


@pytest.fixture
async def tournament(db):
    return await Tournament.create(name='Cup')


def _at(hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


class TestListMyCommitments:
    async def test_nothing_signed_up_is_an_empty_list(self, person, db):
        assert await CrewService().list_my_commitments(person) == []

    async def test_both_roles_appear_in_one_list(self, person, tournament, db):
        one = await Match.create(tournament=tournament, scheduled_at=_at(2))
        two = await Match.create(tournament=tournament, scheduled_at=_at(4))
        await Commentator.create(match=one, user=person, approved=True)
        await Tracker.create(match=two, user=person, approved=False)

        rows = await CrewService().list_my_commitments(person)
        assert [r['role'] for r in rows] == ['commentator', 'tracker']

    async def test_they_are_ordered_by_when_they_happen(self, person, tournament, db):
        """Two models merged into one chronological list — the whole point of
        doing the join in the service rather than the page."""
        later = await Match.create(tournament=tournament, scheduled_at=_at(6))
        sooner = await Match.create(tournament=tournament, scheduled_at=_at(1))
        await Commentator.create(match=later, user=person)
        await Tracker.create(match=sooner, user=person)

        rows = await CrewService().list_my_commitments(person)
        assert [r['match_id'] for r in rows] == [sooner.id, later.id]

    async def test_somebody_elses_signup_is_not_mine(self, person, tournament, db):
        other = await User.create(discord_id=9101, username='someone')
        match = await Match.create(tournament=tournament, scheduled_at=_at(2))
        await Commentator.create(match=match, user=other, approved=True)

        assert await CrewService().list_my_commitments(person) == []

    async def test_past_matches_are_hidden_by_default(self, person, tournament, db):
        past = await Match.create(tournament=tournament, scheduled_at=_at(-30))
        await Commentator.create(match=past, user=person, approved=True)

        assert await CrewService().list_my_commitments(person) == []

    async def test_a_match_running_late_stays_on_the_list(self, person, tournament, db):
        running = await Match.create(tournament=tournament, scheduled_at=_at(-1))
        await Commentator.create(match=running, user=person, approved=True)

        rows = await CrewService().list_my_commitments(person)
        assert [r['match_id'] for r in rows] == [running.id]

    async def test_a_match_that_finished_drops_off(self, person, tournament, db):
        done = await Match.create(
            tournament=tournament, scheduled_at=_at(-2), finished_at=_at(-1),
        )
        await Commentator.create(match=done, user=person, approved=True)

        assert await CrewService().list_my_commitments(person) == []

    async def test_past_matches_are_available_on_request(self, person, tournament, db):
        past = await Match.create(tournament=tournament, scheduled_at=_at(-30))
        await Commentator.create(match=past, user=person, approved=True)

        rows = await CrewService().list_my_commitments(person, upcoming_only=False)
        assert [r['match_id'] for r in rows] == [past.id]

    async def test_a_row_carries_what_the_card_has_to_show(self, person, tournament, db):
        match = await Match.create(tournament=tournament, scheduled_at=_at(3))
        player = await User.create(discord_id=9102, username='racer')
        await match.players.all()  # ensure the relation is materialised
        from models import MatchPlayers
        await MatchPlayers.create(match=match, user=player)
        await Commentator.create(match=match, user=person, approved=True)

        row = (await CrewService().list_my_commitments(person))[0]
        assert row['tournament'] == 'Cup'
        assert row['players'] == ['racer']
        assert row['approved'] is True
        assert row['acknowledged'] is False
        assert row['finished'] is False


class TestTheCrewBoard:
    """My Schedule's crew section is the run sheet narrowed by ``crew_discord_id``."""

    async def test_it_holds_every_match_you_crew_once_and_nothing_else(self, person, tournament, db):
        from application.services.match.match_display_service import MatchDisplayService

        other = await User.create(discord_id=9103, username='someone else')
        both = await Match.create(tournament=tournament, scheduled_at=_at(2))
        tracked = await Match.create(tournament=tournament, scheduled_at=_at(3))
        waiting = await Match.create(tournament=tournament, scheduled_at=_at(4))
        theirs = await Match.create(tournament=tournament, scheduled_at=_at(5))
        await Commentator.create(match=both, user=person, approved=True)
        await Tracker.create(match=both, user=person, approved=True)
        await Tracker.create(match=tracked, user=person, approved=True)
        await Commentator.create(match=waiting, user=person, approved=False)
        await Commentator.create(match=theirs, user=other, approved=True)

        rows = await MatchDisplayService().get_matches_for_display(crew_discord_id='9100')
        assert sorted(r['id'] for r in rows) == sorted([both.id, tracked.id, waiting.id])
