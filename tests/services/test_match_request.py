"""DB-backed tests for player-initiated match creation.

Covers ``MatchRequestMixin.submit_match_request``
(``application/services/match/match_request.py``) against the in-memory SQLite
``db`` fixture with a *real* ``MatchService``, matching the conventions in
``test_match_service_coverage.py`` — real roles rather than monkeypatched gates,
Discord fan-out captured by the autouse ``stub_discord_queue`` fixture.

Split out of that module when it outgrew the file-length guideline, alongside the
service split that produced ``match_request.py``.
"""

import itertools

import pytest

from application.events import EventType, event_bus
from application.services.match.match_service import MatchService
from models import (
    MatchAcknowledgment,
    MatchPlayers,
    Tournament,
    TournamentPlayers,
    User,
    UserRole,
)
from tests.factories import join_community

_discord_ids = itertools.count(31000)


async def make_user(username="player", display_name=None, roles=None):
    user = await User.create(
        discord_id=next(_discord_ids),
        username=username,
        display_name=display_name if display_name is not None else username,
    )
    await join_community(user)
    for role in roles or []:
        await UserRole.create(user=user, role=role)
    return user


async def make_tournament(**overrides):
    fields = dict(name="Test Tournament", players_per_match=2, seed_generator=None)
    fields.update(overrides)
    return await Tournament.create(**fields)


@pytest.fixture
async def service():
    return MatchService()


@pytest.fixture
def captured_events():
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    yield seen
    event_bus.unsubscribe(token)


class TestSubmitMatchRequest:
    async def test_requires_login(self, service, db):
        with pytest.raises(PermissionError, match="Login required"):
            await service.submit_match_request(
                tournament_id=1,
                scheduled_date="2099-01-15",
                scheduled_time="14:30",
                player_ids=[1],
                actor=None,
            )

    async def test_actor_must_be_a_player(self, service, db):
        t = await make_tournament()
        actor = await make_user("selfless")
        other = await make_user("other")
        with pytest.raises(PermissionError, match="only submit match requests where you are a player"):
            await service.submit_match_request(
                tournament_id=t.id,
                scheduled_date="2099-01-15",
                scheduled_time="14:30",
                player_ids=[other.id],
                actor=actor,
            )

    async def test_invalid_datetime_raises(self, service, db):
        t = await make_tournament()
        actor = await make_user("p")
        with pytest.raises(ValueError, match="Invalid date/time format"):
            await service.submit_match_request(
                tournament_id=t.id,
                scheduled_date="nope",
                scheduled_time="14:30",
                player_ids=[actor.id],
                actor=actor,
            )

    async def test_missing_opponent_user_raises(self, service, db):
        t = await make_tournament()
        actor = await make_user("p")
        with pytest.raises(ValueError, match="not found"):
            await service.submit_match_request(
                tournament_id=t.id,
                scheduled_date="2099-01-15",
                scheduled_time="14:30",
                player_ids=[actor.id, 424242],
                actor=actor,
            )

    async def test_bracket_run_tournament_refuses_requests(self, service, db):
        """The enforcement behind the dialog's filtered dropdown."""
        t = await make_tournament()
        t.allow_player_match_requests = False
        await t.save()
        actor = await make_user("p1", "P1")
        opponent = await make_user("p2", "P2")
        with pytest.raises(PermissionError, match="scheduled from its bracket"):
            await service.submit_match_request(
                tournament_id=t.id,
                scheduled_date="2099-01-15",
                scheduled_time="14:30",
                player_ids=[actor.id, opponent.id],
                actor=actor,
            )

    async def test_from_bracket_bypasses_the_toggle(self, service, db):
        """Scheduling *through* the bracket is the path the toggle forces."""
        t = await make_tournament()
        t.allow_player_match_requests = False
        await t.save()
        actor = await make_user("p1", "P1")
        opponent = await make_user("p2", "P2")
        match = await service.submit_match_request(
            tournament_id=t.id,
            scheduled_date="2099-01-15",
            scheduled_time="14:30",
            player_ids=[actor.id, opponent.id],
            actor=actor,
            from_bracket=True,
        )
        assert match.tournament_id == t.id

    async def test_title_is_stored(self, service, db):
        t = await make_tournament()
        actor = await make_user("p1", "P1")
        opponent = await make_user("p2", "P2")
        match = await service.submit_match_request(
            tournament_id=t.id,
            scheduled_date="2099-01-15",
            scheduled_time="14:30",
            player_ids=[actor.id, opponent.id],
            actor=actor,
            title="Cup: P1 vs P2 — Game 2 of 3",
            from_bracket=True,
        )
        assert match.title == "Cup: P1 vs P2 — Game 2 of 3"

    async def test_happy_path_creates_and_enrolls(self, service, db, captured_events):
        t = await make_tournament()
        actor = await make_user("p1", "P1")
        opponent = await make_user("p2", "P2")
        await TournamentPlayers.create(tournament=t, user=opponent, tenant_id=1)
        match = await service.submit_match_request(
            tournament_id=t.id,
            scheduled_date="2099-01-15",
            scheduled_time="14:30",
            player_ids=[actor.id, opponent.id],
            actor=actor,
            comment="req",
        )
        assert match.comment == "req"
        assert {p.user_id for p in await MatchPlayers.filter(match=match)} == {actor.id, opponent.id}
        assert await TournamentPlayers.filter(tournament=t, user=actor).exists()
        assert (await MatchAcknowledgment.get(match=match, user=actor)).acknowledged_at is not None
        assert (await MatchAcknowledgment.get(match=match, user=opponent)).acknowledged_at is None
        assert EventType.MATCH_CREATED in [e.event_type for e in captured_events]

    async def test_a_request_cannot_sign_other_people_up(self, service, db):
        """Request Match enrolled every named player, so a member could put anyone
        in the community into a closed tournament, past its signup window."""
        t = await make_tournament()
        actor = await make_user("p1", "P1")
        stranger = await make_user("p2", "P2")
        with pytest.raises(ValueError, match="signed up for"):
            await service.submit_match_request(
                tournament_id=t.id, scheduled_date="2099-01-15", scheduled_time="14:30",
                player_ids=[actor.id, stranger.id], actor=actor,
            )
        assert not await TournamentPlayers.filter(tournament=t, user=stranger).exists()

    async def test_a_request_cannot_seat_more_players_than_a_match_has(self, service, db):
        t = await make_tournament()
        actor = await make_user("p1", "P1")
        others = [await make_user(f"p{i}", f"P{i}") for i in (2, 3, 4)]
        for other in others:
            await TournamentPlayers.create(tournament=t, user=other, tenant_id=1)
        with pytest.raises(ValueError, match="2 players"):
            await service.submit_match_request(
                tournament_id=t.id, scheduled_date="2099-01-15", scheduled_time="14:30",
                player_ids=[actor.id, *[o.id for o in others]], actor=actor,
            )

    async def test_a_time_in_the_past_is_refused(self, service, db):
        """The booking dialog used to default to now, so pressing Schedule
        untouched booked a match that started a minute ago."""
        t = await make_tournament()
        actor = await make_user("p1", "P1")
        opponent = await make_user("p2", "P2")
        await TournamentPlayers.create(tournament=t, user=opponent, tenant_id=1)
        for from_bracket in (False, True):
            with pytest.raises(ValueError, match="already passed"):
                await service.submit_match_request(
                    tournament_id=t.id,
                    scheduled_date="2020-01-15",
                    scheduled_time="14:30",
                    player_ids=[actor.id, opponent.id],
                    actor=actor,
                    from_bracket=from_bracket,
                )
        assert not await MatchPlayers.filter(user=actor).exists()

    async def test_an_archived_tournament_is_refused(self, service, db):
        """Request Match offered last season's tournament, and nothing stopped
        the request going through."""
        t = await make_tournament(name="Last Season", is_active=False)
        actor = await make_user("p1", "P1")
        opponent = await make_user("p2", "P2")
        for from_bracket in (False, True):
            with pytest.raises(ValueError, match="Last Season has finished"):
                await service.submit_match_request(
                    tournament_id=t.id,
                    scheduled_date="2099-01-15",
                    scheduled_time="14:30",
                    player_ids=[actor.id, opponent.id],
                    actor=actor,
                    from_bracket=from_bracket,
                )


class TestRequestableTournaments:
    async def test_archived_tournaments_are_not_offered(self, db):
        from application.services import TournamentService

        live = await make_tournament(name="Live")
        await make_tournament(name="Archived", is_active=False)
        await make_tournament(name="Bracket run", allow_player_match_requests=False)
        offered = await TournamentService().list_player_requestable()
        assert [t.id for t in offered] == [live.id]
