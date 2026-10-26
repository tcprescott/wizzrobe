"""Cancellation tests — the notifying counterpart of ``delete_match``.

DB-backed (the ``db`` fixture: in-memory SQLite, default tenant id 1). The
load-bearing assertion here is that the cancellation DM still reaches people
*after* the match row and all its child rows are gone: ``collect_match_recipients``
re-queries, and ``discord_queue`` defers the send, so a naive implementation
silently DMs nobody.
"""

import pytest

from application.services.match.match_service import MatchService
from application.services.notification_catalog import CREW, MATCH_CANCELLED, PLAYERS
from application.services.race_room_service import RaceRoomService
from models import (
    Commentator,
    Match,
    MatchPlayers,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeRoom,
    Stage,
    Tournament,
    User,
)
from tests.factories import make_user, utc

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.usefixtures("bypass_auth")]


@pytest.fixture
def service() -> MatchService:
    return MatchService()


async def _match_with_people() -> tuple[Match, User, User]:
    tournament = await Tournament.create(name='Cup')
    match = await Match.create(
        tournament=tournament, scheduled_at=utc(2026, 8, 1, 18, 0),
        stage=await Stage.create(name='Kraid'),
    )
    player = await make_user(discord_id=101, username='player')
    commentator = await make_user(discord_id=202, username='commentator')
    await MatchPlayers.create(match=match, user=player)
    await Commentator.create(match=match, user=commentator, approved=True)
    return match, player, commentator


class TestCancelMatch:
    async def test_deletes_the_match_and_its_children(self, service):
        match, _, _ = await _match_with_people()

        await service.cancel_match(match.id, actor=await make_user(discord_id=1))

        assert await Match.filter(id=match.id).first() is None
        assert await MatchPlayers.filter(match_id=match.id).count() == 0
        assert await Commentator.filter(match_id=match.id).count() == 0

    async def test_notifies_players_and_crew_after_the_row_is_gone(
        self, service, stub_discord_queue,
    ):
        """The regression guard: recipients must be resolved before the delete.

        ``stub_discord_queue`` captures the coroutine without running it, so this
        awaits it explicitly — reproducing the real ordering, where the queue
        worker runs it only after ``cancel_match`` has already returned.
        """
        match, player, commentator = await _match_with_people()
        sent: list[tuple[int, str]] = []
        titles: dict[int, str] = {}
        notices: dict[int, object] = {}

        class _Recorder:
            async def send_dm(self, discord_id, message, embed=None, notice=None):
                sent.append((discord_id, message))
                titles[discord_id] = embed.title
                notices[discord_id] = notice
                return True, None

        await service.cancel_match(
            match.id, actor=await make_user(discord_id=1), reason='Series clinched 2-0.',
        )
        assert await Match.filter(id=match.id).first() is None  # gone first

        queued = list(stub_discord_queue)
        assert queued, 'cancellation enqueued no notification'
        service.match_schedule_service.discord_service = _Recorder()
        for coro in queued:
            await coro

        assert {discord_id for discord_id, _ in sent} == {
            player.discord_id, commentator.discord_id,
        }
        body = sent[0][1]
        assert 'cancelled' in body
        assert 'Series clinched 2-0.' in body
        # The commentator is told which slot they held; the player is not.
        assert titles[commentator.discord_id] == 'Match cancelled · your commentator slot'
        assert titles[player.discord_id] == 'Match cancelled'
        assert notices[player.discord_id] == MATCH_CANCELLED.to(PLAYERS)
        assert notices[commentator.discord_id] == MATCH_CANCELLED.to(CREW)
        by_id = dict(sent)
        assert "You're on this match as commentator." in by_id[commentator.discord_id]

    async def test_cancels_a_live_racetime_room(self, service):
        match, _, _ = await _match_with_people()
        bot = await RacetimeBot.create(
            category='alttpr', client_id='c', client_secret='s', name='bot',
        )
        room = await RacetimeRoom.create(
            bot=bot, slug='alttpr/match-1', category='alttpr',
            status=RaceRoomStatus.OPEN, match=match,
        )

        await service.cancel_match(match.id, actor=await make_user(discord_id=1))

        await room.refresh_from_db()
        assert room.status == RaceRoomStatus.CANCELLED
        assert room.match_id is None  # SET_NULL, but cancelled rather than orphaned OPEN

    async def test_a_finished_room_is_left_alone(self, service):
        match, _, _ = await _match_with_people()
        bot = await RacetimeBot.create(
            category='alttpr', client_id='c', client_secret='s', name='bot',
        )
        room = await RacetimeRoom.create(
            bot=bot, slug='alttpr/match-2', category='alttpr',
            status=RaceRoomStatus.FINISHED, match=match,
        )

        await service.cancel_match(match.id, actor=await make_user(discord_id=1))

        await room.refresh_from_db()
        assert room.status == RaceRoomStatus.FINISHED

    async def test_missing_match_raises(self, service):
        from application.errors import NotFoundError

        with pytest.raises(NotFoundError):
            await service.cancel_match(9999, actor=await make_user(discord_id=1))


class TestDeleteMatchUnchanged:
    async def test_delete_still_notifies_nobody(self, service, stub_discord_queue):
        """``delete_match`` keeps its existing silent behaviour."""
        match, _, _ = await _match_with_people()
        before = len(stub_discord_queue)

        await service.delete_match(match.id, actor=await make_user(discord_id=1))

        assert await Match.filter(id=match.id).first() is None
        assert len(stub_discord_queue) == before


class TestRaceRoomServiceCancel:
    async def test_cancel_room_records_the_reason(self):
        match, _, _ = await _match_with_people()
        bot = await RacetimeBot.create(
            category='alttpr', client_id='c', client_secret='s', name='bot',
        )
        room = await RacetimeRoom.create(
            bot=bot, slug='alttpr/match-3', category='alttpr',
            status=RaceRoomStatus.OPEN, match=match,
        )

        await RaceRoomService().cancel_room(
            room, actor=await make_user(discord_id=1), reason='series clinched',
        )

        await room.refresh_from_db()
        assert room.status == RaceRoomStatus.CANCELLED
