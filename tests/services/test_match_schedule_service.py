from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from application.services.match.match_schedule_service import MatchScheduleService
from application.services.notification_catalog import (
    FOLLOWERS,
    MATCH_CHECKED_IN,
    MATCH_CONFIRMED,
    MATCH_FINISHED,
    MATCH_SCHEDULED,
    MATCH_STARTED,
    PLAYERS,
    WATCHERS,
)
from application.utils.discord_messages import (
    _players_label,
    checked_in_dm,
    scheduled_dm,
    seed_dm,
    state_changed_dm,
    stream_candidate_dm,
)
from models import NotificationMode
from tests.factories import make_audit_double


class TestPlayersLabel:
    def test_two_players_uses_vs(self):
        assert _players_label(["Alice", "Bob"]) == "Alice vs Bob"

    def test_three_players_comma_joined(self):
        assert _players_label(["A", "B", "C"]) == "A, B, C"

    def test_empty_returns_blank(self):
        assert _players_label(None) == ""
        assert _players_label([]) == ""


class TestMatchInfoBlock:
    def test_stage_omitted_when_no_stage(self):
        msg = scheduled_dm(
            "Test", "2025-01-15 14:30 EST", player_names=["Alice", "Bob"]
        )
        assert "Stage:" not in msg
        assert "Players: Alice vs Bob" in msg

    def test_stage_included_when_assigned(self):
        msg = scheduled_dm(
            "Test", "2025-01-15 14:30 EST",
            player_names=["Alice", "Bob"], stage_name="Stage 1",
        )
        assert "Stage: Stage 1" in msg

    def test_state_changed_has_no_match_id(self):
        msg = state_changed_dm("Test", "Started", player_names=["Alice", "Bob"])
        assert "Match ID" not in msg
        assert "Alice vs Bob" in msg

    def test_checked_in_has_no_match_id(self):
        msg = checked_in_dm("Test", player_names=["Alice", "Bob"])
        assert "Match ID" not in msg
        assert "Alice vs Bob" in msg


class MockTournament:
    def __init__(self, name="Test Tournament", is_racetime_enabled=False):
        self.name = name
        self.is_racetime_enabled = is_racetime_enabled


class MockMatch:
    """Minimal stand-in for a Match ORM object."""

    def __init__(self, *, seated_at=None, started_at=None, finished_at=None,
                 confirmed_at=None, id=1, stage_id=None, tournament_id=1,
                 scheduled_at=None, players=None, stage=None,
                 is_racetime_enabled=False, needs_review=False):
        self.id = id
        self.seated_at = seated_at
        self.started_at = started_at
        self.finished_at = finished_at
        self.confirmed_at = confirmed_at
        self.stage_id = stage_id
        self.tournament_id = tournament_id
        self.scheduled_at = scheduled_at or datetime(2025, 1, 15, 19, 30)
        self.tournament = MockTournament(is_racetime_enabled=is_racetime_enabled)
        self.players = players if players is not None else []
        self.stage = stage
        # The dispute flag: confirm_match clears it (and audits the clear), so a
        # stand-in without this attribute would hide that half of confirming.
        self.needs_review = needs_review
        self.save = AsyncMock()
        self.fetch_related = AsyncMock()
        self.refresh_from_db = AsyncMock()


def mock_players(*names, ranks=None, stations=None):
    """MatchPlayers stand-ins.

    The DM builders read ``user.preferred_name`` and ``assigned_station``;
    ``confirm_match`` reads ``finish_rank`` to decide whether a result was ever
    recorded. ``ranks=None`` means no result recorded yet, ``stations=None`` no
    seats assigned. Discord ids run 101, 102, ...
    """
    names = names or ("Alice", "Bob")
    ranks = ranks if ranks is not None else [None] * len(names)
    stations = stations if stations is not None else [None] * len(names)
    return [
        SimpleNamespace(
            user=SimpleNamespace(preferred_name=n, discord_id=101 + i),
            finish_rank=r, assigned_station=st,
        )
        for i, (n, r, st) in enumerate(zip(names, ranks, stations, strict=False))
    ]


pytestmark = pytest.mark.usefixtures("bypass_auth")


@pytest.fixture
def service():
    svc = object.__new__(MatchScheduleService)
    svc.match_repository = MagicMock()
    svc.match_repository.stamp_if_unset = AsyncMock(return_value=True)
    svc.discord_service = MagicMock()
    svc.seedgen_service = MagicMock()
    svc._seed_locks = {}
    svc.audit_service = make_audit_double()
    svc.notify_match_participants = AsyncMock()
    return svc


class TestSeatMatch:
    async def test_dm_carries_seats_and_each_players_own(self, service):
        match = MockMatch(players=mock_players(stations=["A1", "A2"]))
        with patch(
            'application.services.match.match_schedule_service.bracket_line_for',
            AsyncMock(return_value=''),
        ), patch(
            'application.services.match.match_schedule_service._community_name',
            AsyncMock(return_value='SGL'),
        ):
            await service.seat_match(match)

        args = service.notify_match_participants.call_args
        text, embed = args.args[1], args.args[2]
        assert "Seats: Alice: A1 · Bob: A2" in text
        assert "Your seat" not in text
        assert [f.name for f in embed.fields][-1] == 'Seats'

        copies = args.kwargs['player_copies']
        alice_text, alice_embed = copies[101]
        assert "Your seat: **A1**" in alice_text
        assert (alice_embed.fields[0].name, alice_embed.fields[0].value) == ('Your seat', '**A1**')
        assert "**A2**" in copies[102][0]

    async def test_no_seats_no_seat_lines(self, service):
        match = MockMatch(players=mock_players())
        with patch(
            'application.services.match.match_schedule_service.bracket_line_for',
            AsyncMock(return_value=''),
        ), patch(
            'application.services.match.match_schedule_service._community_name',
            AsyncMock(return_value='SGL'),
        ):
            await service.seat_match(match)

        args = service.notify_match_participants.call_args
        assert "Seat" not in args.args[1]
        assert args.kwargs['player_copies'] == {}

    async def test_sets_seated_at(self, service):
        match = MockMatch(players=mock_players())
        await service.seat_match(match)
        assert match.seated_at is not None

    async def test_persists_the_change(self, service):
        match = MockMatch(players=mock_players())
        await service.seat_match(match)
        service.match_repository.stamp_if_unset.assert_awaited_once()

    async def test_seated_at_is_recent(self, service):
        match = MockMatch(players=mock_players())
        before = datetime.now(timezone.utc)
        await service.seat_match(match)
        after = datetime.now(timezone.utc)
        assert before <= match.seated_at <= after

    async def test_raises_if_the_match_has_no_players(self, service):
        """Bracket-scheduled matches whose entrants are unresolved must not seat."""
        match = MockMatch(players=[])
        with pytest.raises(ValueError, match="no players"):
            await service.seat_match(match)
        assert match.seated_at is None
        service.match_repository.stamp_if_unset.assert_not_awaited()

    async def test_raises_if_already_seated(self, service):
        match = MockMatch(seated_at=datetime.now())
        with pytest.raises(ValueError, match="already checked in"):
            await service.seat_match(match)

    async def test_does_not_save_on_error(self, service):
        match = MockMatch(seated_at=datetime.now())
        with pytest.raises(ValueError):
            await service.seat_match(match)
        service.match_repository.stamp_if_unset.assert_not_awaited()

    async def test_racetime_tournament_rejects_check_in(self, service):
        match = MockMatch(is_racetime_enabled=True)
        with pytest.raises(ValueError, match=r"racetime\.gg"):
            await service.seat_match(match)

    async def test_racetime_tournament_does_not_seat(self, service):
        match = MockMatch(is_racetime_enabled=True)
        with pytest.raises(ValueError):
            await service.seat_match(match)
        assert match.seated_at is None
        service.match_repository.stamp_if_unset.assert_not_awaited()


class TestStartMatch:
    async def test_sets_started_at(self, service):
        match = MockMatch(seated_at=datetime.now())
        await service.start_match(match)
        assert match.started_at is not None

    async def test_persists_the_change(self, service):
        match = MockMatch(seated_at=datetime.now())
        await service.start_match(match)
        service.match_repository.stamp_if_unset.assert_awaited_once()

    async def test_raises_if_not_seated(self, service):
        match = MockMatch()
        with pytest.raises(ValueError, match="checked in before starting"):
            await service.start_match(match)

    async def test_raises_if_already_started(self, service):
        now = datetime.now()
        match = MockMatch(seated_at=now, started_at=now)
        with pytest.raises(ValueError, match="already started"):
            await service.start_match(match)

    async def test_does_not_modify_seated_at(self, service):
        seated = datetime(2025, 1, 15, 10, 0)
        match = MockMatch(seated_at=seated)
        await service.start_match(match)
        assert match.seated_at == seated

    async def test_dm_is_the_match_started_notice(self, service):
        await service.start_match(MockMatch(seated_at=datetime.now()))
        kwargs = service.notify_match_participants.call_args.kwargs
        assert kwargs == {'notice': MATCH_STARTED, 'player_copies': None}


class TestFinishMatch:
    async def test_sets_finished_at(self, service):
        now = datetime.now()
        match = MockMatch(seated_at=now, started_at=now)
        await service.finish_match(match)
        assert match.finished_at is not None

    async def test_persists_the_change(self, service):
        now = datetime.now()
        match = MockMatch(seated_at=now, started_at=now)
        await service.finish_match(match)
        service.match_repository.stamp_if_unset.assert_awaited_once()

    async def test_dm_is_the_match_finished_notice(self, service):
        now = datetime.now()
        await service.finish_match(MockMatch(seated_at=now, started_at=now))
        kwargs = service.notify_match_participants.call_args.kwargs
        assert kwargs == {'notice': MATCH_FINISHED, 'player_copies': None}

    async def test_raises_if_not_started(self, service):
        match = MockMatch()
        with pytest.raises(ValueError, match="started before finishing"):
            await service.finish_match(match)

    async def test_raises_if_already_finished(self, service):
        now = datetime.now()
        match = MockMatch(seated_at=now, started_at=now, finished_at=now)
        with pytest.raises(ValueError, match="already finished"):
            await service.finish_match(match)

    async def test_does_not_require_confirmed_at_to_be_none(self, service):
        # confirmed_at is irrelevant to finish check
        now = datetime.now()
        match = MockMatch(seated_at=now, started_at=now)
        await service.finish_match(match)
        assert match.finished_at is not None


class TestConfirmMatch:
    @staticmethod
    def _played_out(**stamps):
        """A finished match whose winner has been recorded."""
        now = datetime.now()
        return MockMatch(
            seated_at=now, started_at=now, finished_at=now,
            players=mock_players(ranks=[1, 2]), **stamps,
        )

    async def test_sets_confirmed_at(self, service):
        match = self._played_out()
        await service.confirm_match(match)
        assert match.confirmed_at is not None

    async def test_persists_the_change(self, service):
        match = self._played_out()
        await service.confirm_match(match)
        service.match_repository.stamp_if_unset.assert_awaited_once()

    async def test_dm_is_the_result_confirmed_notice(self, service):
        await service.confirm_match(self._played_out())
        kwargs = service.notify_match_participants.call_args.kwargs
        assert kwargs == {'notice': MATCH_CONFIRMED, 'player_copies': None}

    async def test_raises_if_not_finished(self, service):
        match = MockMatch()
        with pytest.raises(ValueError, match="finished before confirming"):
            await service.confirm_match(match)

    async def test_raises_if_already_confirmed(self, service):
        match = self._played_out(confirmed_at=datetime.now())
        with pytest.raises(ValueError, match="already confirmed"):
            await service.confirm_match(match)

    async def test_raises_if_no_result_recorded(self, service):
        """Confirming advances the bracket, so an empty result must not pass."""
        now = datetime.now()
        match = MockMatch(
            seated_at=now, started_at=now, finished_at=now, players=mock_players(),
        )
        with pytest.raises(ValueError, match="No result has been recorded"):
            await service.confirm_match(match)
        assert match.confirmed_at is None
        service.match_repository.stamp_if_unset.assert_not_awaited()

    async def test_raises_if_the_match_has_no_players(self, service):
        now = datetime.now()
        match = MockMatch(seated_at=now, started_at=now, finished_at=now, players=[])
        with pytest.raises(ValueError, match="No result has been recorded"):
            await service.confirm_match(match)
        assert match.confirmed_at is None

    async def test_already_confirmed_wins_over_the_missing_result(self, service):
        """The clearer message for an operator who double-clicked Confirm."""
        now = datetime.now()
        match = MockMatch(
            seated_at=now, started_at=now, finished_at=now, confirmed_at=now,
            players=mock_players(),
        )
        with pytest.raises(ValueError, match="already confirmed"):
            await service.confirm_match(match)


class TestFullLifecycle:
    async def test_seat_start_finish_confirm_in_order(self, service):
        match = MockMatch(players=mock_players())
        await service.seat_match(match)
        await service.start_match(match)
        await service.finish_match(match)
        # Recording the winner sits between finishing and confirming; without it
        # confirm_match refuses (see TestConfirmMatch).
        match.players[0].finish_rank = 1
        match.players[1].finish_rank = 2
        await service.confirm_match(match)
        assert match.confirmed_at is not None
        assert service.match_repository.stamp_if_unset.await_count == 4

    async def test_cannot_skip_seat_to_start(self, service):
        match = MockMatch()
        with pytest.raises(ValueError):
            await service.start_match(match)

    async def test_cannot_skip_start_to_finish(self, service):
        match = MockMatch(seated_at=datetime.now())
        with pytest.raises(ValueError):
            await service.finish_match(match)

    async def test_cannot_skip_finish_to_confirm(self, service):
        now = datetime.now()
        match = MockMatch(seated_at=now, started_at=now)
        with pytest.raises(ValueError):
            await service.confirm_match(match)


class TestStreamCandidateDm:
    def test_contains_player_names(self):
        msg = stream_candidate_dm(
            "ALttPR Open", "2025-01-15 14:30 EST", player_names=["Alice", "Bob"]
        )
        assert "Alice vs Bob" in msg

    def test_omits_match_id(self):
        msg = stream_candidate_dm(
            "ALttPR Open", "2025-01-15 14:30 EST", player_names=["Alice", "Bob"]
        )
        assert "Match ID" not in msg

    def test_contains_tournament_name(self):
        msg = stream_candidate_dm("ALttPR Open", "2025-01-15 14:30 EST")
        assert "ALttPR Open" in msg

    def test_contains_scheduled_at(self):
        msg = stream_candidate_dm("Test", "2025-01-15 14:30 EST")
        assert "2025-01-15 14:30 EST" in msg

    def test_is_non_empty_string(self):
        msg = stream_candidate_dm("Test", "")
        assert isinstance(msg, str)
        assert len(msg) > 0


class TestNotifyTournamentSubscribersScheduled:
    async def test_sends_dm_with_crew_buttons_to_qualifying_subscriber(self, service):
        match = MockMatch()
        subscriber = SimpleNamespace(discord_id=999)
        mock_repo = MagicMock()
        mock_repo.get_match_notification_subscribers = AsyncMock(return_value=[subscriber])
        service.discord_service.send_dm_with_crew_buttons = AsyncMock(return_value=(True, "ok"))

        with patch('application.repositories.TournamentNotificationRepository', return_value=mock_repo):
            await service.notify_tournament_subscribers_scheduled(match, "msg", [])

        # link=None here only because no tenant resolves in this unit fixture;
        # the kwarg must still be passed, since it is what carries the DM's way
        # back into the app.
        service.discord_service.send_dm_with_crew_buttons.assert_awaited_once_with(
            999, "msg", match.id, embed=None, link=None,
            notice=MATCH_SCHEDULED.to(FOLLOWERS),
        )
        assert MATCH_SCHEDULED.to(FOLLOWERS).default is NotificationMode.SILENT

    async def test_excludes_already_notified_discord_ids(self, service):
        match = MockMatch()
        subscriber = SimpleNamespace(discord_id=999)
        mock_repo = MagicMock()
        mock_repo.get_match_notification_subscribers = AsyncMock(return_value=[subscriber])
        service.discord_service.send_dm_with_crew_buttons = AsyncMock(return_value=(True, "ok"))

        with patch('application.repositories.TournamentNotificationRepository', return_value=mock_repo):
            await service.notify_tournament_subscribers_scheduled(match, "msg", [999])

        service.discord_service.send_dm_with_crew_buttons.assert_not_awaited()

    async def test_passes_has_stage_true_when_set(self, service):
        match = MockMatch(stage_id=5)
        mock_repo = MagicMock()
        mock_repo.get_match_notification_subscribers = AsyncMock(return_value=[])
        service.discord_service.send_dm_with_crew_buttons = AsyncMock()

        with patch('application.repositories.TournamentNotificationRepository', return_value=mock_repo):
            await service.notify_tournament_subscribers_scheduled(match, "msg", [])

        mock_repo.get_match_notification_subscribers.assert_awaited_once_with(
            match.tournament_id, has_stage=True
        )

    async def test_passes_has_stage_false_when_not_set(self, service):
        match = MockMatch(stage_id=None)
        mock_repo = MagicMock()
        mock_repo.get_match_notification_subscribers = AsyncMock(return_value=[])
        service.discord_service.send_dm_with_crew_buttons = AsyncMock()

        with patch('application.repositories.TournamentNotificationRepository', return_value=mock_repo):
            await service.notify_tournament_subscribers_scheduled(match, "msg", [])

        mock_repo.get_match_notification_subscribers.assert_awaited_once_with(
            match.tournament_id, has_stage=False
        )

    async def test_swallows_exception_without_raising(self, service):
        match = MockMatch()
        with patch('application.repositories.TournamentNotificationRepository', side_effect=Exception("db error")):
            # Should not raise
            await service.notify_tournament_subscribers_scheduled(match, "msg", [])


class TestNotifyStreamCandidateSubscribers:
    async def test_skips_entirely_when_match_has_stage(self, service):
        match = MockMatch(stage_id=3)
        mock_repo = MagicMock()
        mock_repo.get_stream_candidate_subscribers = AsyncMock(return_value=[])
        service.discord_service.send_dm_with_crew_buttons = AsyncMock()

        with patch('application.repositories.TournamentNotificationRepository', return_value=mock_repo):
            await service.notify_stream_candidate_subscribers(match, [])

        mock_repo.get_stream_candidate_subscribers.assert_not_awaited()
        service.discord_service.send_dm_with_crew_buttons.assert_not_awaited()

    async def test_sends_dm_to_subscriber_when_no_stage(self, service):
        match = MockMatch(stage_id=None)
        subscriber = SimpleNamespace(discord_id=777)
        mock_repo = MagicMock()
        mock_repo.get_stream_candidate_subscribers = AsyncMock(return_value=[subscriber])
        service.discord_service.send_dm_with_crew_buttons = AsyncMock(return_value=(True, "ok"))

        with patch('application.repositories.TournamentNotificationRepository', return_value=mock_repo):
            await service.notify_stream_candidate_subscribers(match, [])

        service.discord_service.send_dm_with_crew_buttons.assert_awaited_once()
        call_args = service.discord_service.send_dm_with_crew_buttons.call_args
        assert call_args.args[0] == 777

    async def test_excludes_already_notified_discord_ids(self, service):
        match = MockMatch(stage_id=None)
        subscriber = SimpleNamespace(discord_id=777)
        mock_repo = MagicMock()
        mock_repo.get_stream_candidate_subscribers = AsyncMock(return_value=[subscriber])
        service.discord_service.send_dm_with_crew_buttons = AsyncMock(return_value=(True, "ok"))

        with patch('application.repositories.TournamentNotificationRepository', return_value=mock_repo):
            await service.notify_stream_candidate_subscribers(match, [777])

        service.discord_service.send_dm_with_crew_buttons.assert_not_awaited()

    async def test_swallows_exception_without_raising(self, service):
        match = MockMatch(stage_id=None)
        with patch('application.repositories.TournamentNotificationRepository', side_effect=Exception("db error")):
            await service.notify_stream_candidate_subscribers(match, [])


class TestNotifyMatchScheduled:
    """The collapsed scheduled/rescheduled fan-out shared by create/update/request."""

    @staticmethod
    def _wire(service):
        # Sub-notifications are passed to the queue (never awaited here), so plain
        # MagicMocks avoid 'coroutine never awaited' noise; _collect is awaited.
        service.notify_acknowledgment_request = MagicMock()
        service.notify_match_crew = MagicMock()
        service.notify_tournament_subscribers_scheduled = MagicMock()
        service.notify_stream_candidate_subscribers = MagicMock()
        service._collect_notified_discord_ids = AsyncMock(return_value=[111])

    async def test_enqueues_ack_crew_and_subscribers(self, service):
        self._wire(service)
        match = MockMatch()
        with patch('application.services.match.match_schedule_service.discord_queue.enqueue') as enqueue:
            await service.notify_match_scheduled(match, rescheduled=False, is_stream_candidate=False)

        _, ack_kwargs = service.notify_acknowledgment_request.call_args
        assert ack_kwargs == {
            'rescheduled': False, 'community': '', 'bracket_line': '',
        }
        subs_args = service.notify_tournament_subscribers_scheduled.call_args.args
        assert subs_args[0] is match
        assert subs_args[2] == [111]
        service.notify_stream_candidate_subscribers.assert_not_called()
        # Ack, crew, subscribers, plus the harder-settings offer.
        assert enqueue.call_count == 4

    async def test_stream_candidate_enqueued_when_flagged(self, service):
        self._wire(service)
        match = MockMatch()
        with patch('application.services.match.match_schedule_service.discord_queue.enqueue') as enqueue:
            await service.notify_match_scheduled(match, rescheduled=False, is_stream_candidate=True)

        candidate_args = service.notify_stream_candidate_subscribers.call_args.args
        assert candidate_args[0] is match
        assert candidate_args[1] == [111]
        assert enqueue.call_count == 5

    async def test_rescheduled_flag_forwarded_to_ack_request(self, service):
        self._wire(service)
        match = MockMatch()
        with patch('application.services.match.match_schedule_service.discord_queue.enqueue'):
            await service.notify_match_scheduled(match, rescheduled=True)

        _, ack_kwargs = service.notify_acknowledgment_request.call_args
        assert ack_kwargs == {
            'rescheduled': True, 'community': '', 'bracket_line': '',
        }


class TestNotifyStreamCandidate:
    async def test_enqueues_stream_candidate_subscribers(self, service):
        service.notify_stream_candidate_subscribers = MagicMock()
        service._collect_notified_discord_ids = AsyncMock(return_value=[111, 222])
        match = MockMatch()
        with patch('application.services.match.match_schedule_service.discord_queue.enqueue') as enqueue:
            await service.notify_stream_candidate(match)

        args = service.notify_stream_candidate_subscribers.call_args.args
        assert args[0] is match
        assert args[1] == [111, 222]
        assert enqueue.call_count == 1


class TestCollectNotifiedDiscordIds:
    @staticmethod
    def _patch_query(target, rows):
        mock_qs = MagicMock()
        mock_qs.prefetch_related = MagicMock(return_value=AsyncMock(return_value=rows)())
        return patch(target, return_value=mock_qs)

    async def test_dedupes_players_and_approved_crew(self, service):
        match = MockMatch()
        player = SimpleNamespace(user=SimpleNamespace(discord_id=111))
        commentator = SimpleNamespace(user=SimpleNamespace(discord_id=222))
        # Tracker shares the commentator's id — should be deduped away.
        tracker = SimpleNamespace(user=SimpleNamespace(discord_id=222))

        with self._patch_query('application.services.match._match_recipients.MatchPlayers.filter', [player]), \
             self._patch_query('application.services.match._match_recipients.Commentator.filter', [commentator]), \
             self._patch_query('application.services.match._match_recipients.Tracker.filter', [tracker]):
            ids = await service._collect_notified_discord_ids(match)

        assert ids == [111, 222]


class TestSeedDm:
    def test_contains_player_name(self):
        msg = seed_dm("Alice", "ALttPR Open", "https://alttpr.com/h/abc")
        assert "Alice" in msg

    def test_omits_match_id(self):
        msg = seed_dm(
            "Alice", "ALttPR Open", "https://alttpr.com/h/abc",
            player_names=["Alice", "Bob"],
        )
        assert "Match ID" not in msg
        assert "ID:" not in msg

    def test_contains_player_names_block(self):
        msg = seed_dm(
            "Alice", "ALttPR Open", "https://alttpr.com/h/abc",
            player_names=["Alice", "Bob"],
        )
        assert "Alice vs Bob" in msg

    def test_contains_tournament_name(self):
        msg = seed_dm("Alice", "ALttPR Open", "https://alttpr.com/h/abc")
        assert "ALttPR Open" in msg

    def test_contains_seed_url(self):
        url = "https://alttpr.com/h/abc123"
        msg = seed_dm("Alice", "ALttPR Open", url)
        assert url in msg

    def test_is_non_empty_string(self):
        msg = seed_dm("Bob", "OoTR", "https://ootrandomizer.com/seed/1")
        assert isinstance(msg, str)
        assert len(msg) > 0


class TestNotifyMatchParticipants:
    @pytest.fixture
    def real_notify_service(self, service):
        del service.notify_match_participants
        service.discord_service.send_dm = AsyncMock(return_value=(True, "ok"))
        service.discord_service.send_dm_with_unwatch_button = AsyncMock(return_value=(True, "ok"))
        return service

    @staticmethod
    def _patch_query(target, rows):
        mock_qs = MagicMock()
        mock_qs.prefetch_related = MagicMock(return_value=AsyncMock(return_value=rows)())
        return patch(target, return_value=mock_qs)

    async def test_player_only_gets_plain_dm(self, real_notify_service):
        match = MockMatch()
        player = SimpleNamespace(user=SimpleNamespace(discord_id=111, dm_notifications=True))

        with self._patch_query('application.services.match._match_recipients.MatchPlayers.filter', [player]), \
             self._patch_query('application.services.match._match_recipients.Commentator.filter', []), \
             self._patch_query('application.services.match._match_recipients.Tracker.filter', []), \
             self._patch_query('application.services.match._match_recipients.MatchWatcher.filter', []):
            await real_notify_service.notify_match_participants(match, "hello", notice=MATCH_CHECKED_IN)

        real_notify_service.discord_service.send_dm.assert_awaited_once_with(111, "hello", embed=None, link=None, notice=MATCH_CHECKED_IN.to(PLAYERS))
        real_notify_service.discord_service.send_dm_with_unwatch_button.assert_not_awaited()

    async def test_watcher_only_gets_unwatch_button_dm(self, real_notify_service):
        match = MockMatch()
        watcher = SimpleNamespace(user=SimpleNamespace(discord_id=222, dm_notifications=True))

        with self._patch_query('application.services.match._match_recipients.MatchPlayers.filter', []), \
             self._patch_query('application.services.match._match_recipients.Commentator.filter', []), \
             self._patch_query('application.services.match._match_recipients.Tracker.filter', []), \
             self._patch_query('application.services.match._match_recipients.MatchWatcher.filter', [watcher]):
            await real_notify_service.notify_match_participants(match, "hello", notice=MATCH_CHECKED_IN)

        real_notify_service.discord_service.send_dm.assert_not_awaited()
        real_notify_service.discord_service.send_dm_with_unwatch_button.assert_awaited_once_with(222, "hello", match.id, embed=None, link=None, notice=MATCH_CHECKED_IN.to(WATCHERS))

    async def test_player_who_is_also_watcher_gets_unwatch_button(self, real_notify_service):
        match = MockMatch()
        u = SimpleNamespace(discord_id=333, dm_notifications=True)
        player = SimpleNamespace(user=u)
        watcher = SimpleNamespace(user=u)

        with self._patch_query('application.services.match._match_recipients.MatchPlayers.filter', [player]), \
             self._patch_query('application.services.match._match_recipients.Commentator.filter', []), \
             self._patch_query('application.services.match._match_recipients.Tracker.filter', []), \
             self._patch_query('application.services.match._match_recipients.MatchWatcher.filter', [watcher]):
            await real_notify_service.notify_match_participants(match, "hello", notice=MATCH_CHECKED_IN)

        real_notify_service.discord_service.send_dm.assert_not_awaited()
        real_notify_service.discord_service.send_dm_with_unwatch_button.assert_awaited_once_with(333, "hello", match.id, embed=None, link=None, notice=MATCH_CHECKED_IN.to(PLAYERS))

    async def test_each_audience_gets_its_own_target_of_the_notice(self, real_notify_service):
        match = MockMatch()
        player = SimpleNamespace(user=SimpleNamespace(discord_id=111, dm_notifications=True))
        watcher = SimpleNamespace(user=SimpleNamespace(discord_id=222, dm_notifications=True))

        with self._patch_query('application.services.match._match_recipients.MatchPlayers.filter', [player]), \
             self._patch_query('application.services.match._match_recipients.Commentator.filter', []), \
             self._patch_query('application.services.match._match_recipients.Tracker.filter', []), \
             self._patch_query('application.services.match._match_recipients.MatchWatcher.filter', [watcher]):
            await real_notify_service.notify_match_participants(match, "hello", notice=MATCH_STARTED)

        # send_dm drops the players' copy and quiets the watcher's under the
        # catalogue defaults; the fan-out only has to name the right target.
        real_notify_service.discord_service.send_dm.assert_awaited_once_with(
            111, "hello", embed=None, link=None, notice=MATCH_STARTED.to(PLAYERS),
        )
        real_notify_service.discord_service.send_dm_with_unwatch_button.assert_awaited_once_with(
            222, "hello", match.id, embed=None, link=None, notice=MATCH_STARTED.to(WATCHERS),
        )
        assert MATCH_STARTED.to(PLAYERS).default is NotificationMode.OFF
        assert MATCH_STARTED.to(WATCHERS).default is NotificationMode.SILENT

    async def test_a_player_copy_replaces_the_shared_dm_for_that_player(self, real_notify_service):
        match = MockMatch()
        player = SimpleNamespace(user=SimpleNamespace(discord_id=111, dm_notifications=True))
        watcher = SimpleNamespace(user=SimpleNamespace(discord_id=222, dm_notifications=True))

        with self._patch_query('application.services.match._match_recipients.MatchPlayers.filter', [player]), \
             self._patch_query('application.services.match._match_recipients.Commentator.filter', []), \
             self._patch_query('application.services.match._match_recipients.Tracker.filter', []), \
             self._patch_query('application.services.match._match_recipients.MatchWatcher.filter', [watcher]):
            await real_notify_service.notify_match_participants(
                match, "hello", notice=MATCH_CHECKED_IN, player_copies={111: ("your seat A1", None)},
            )

        real_notify_service.discord_service.send_dm.assert_awaited_once_with(111, "your seat A1", embed=None, link=None, notice=MATCH_CHECKED_IN.to(PLAYERS))
        real_notify_service.discord_service.send_dm_with_unwatch_button.assert_awaited_once_with(222, "hello", match.id, embed=None, link=None, notice=MATCH_CHECKED_IN.to(WATCHERS))

    async def test_discord_off_still_reaches_send_dm(self, real_notify_service):
        match = MockMatch()
        opted_out = SimpleNamespace(user=SimpleNamespace(discord_id=444, dm_notifications=False))

        with self._patch_query('application.services.match._match_recipients.MatchPlayers.filter', [opted_out]), \
             self._patch_query('application.services.match._match_recipients.Commentator.filter', []), \
             self._patch_query('application.services.match._match_recipients.Tracker.filter', []), \
             self._patch_query('application.services.match._match_recipients.MatchWatcher.filter', []):
            await real_notify_service.notify_match_participants(match, "hello", notice=MATCH_CHECKED_IN)

        # Discord off only drops the Discord copy, inside send_dm; devices still need the call.
        real_notify_service.discord_service.send_dm.assert_awaited_once()

    async def test_each_recipient_gets_exactly_one_dm(self, real_notify_service):
        match = MockMatch()
        player_user = SimpleNamespace(discord_id=111, dm_notifications=True)
        crew_user = SimpleNamespace(discord_id=222, dm_notifications=True)
        watcher_user = SimpleNamespace(discord_id=333, dm_notifications=True)

        with self._patch_query('application.services.match._match_recipients.MatchPlayers.filter',
                               [SimpleNamespace(user=player_user)]), \
             self._patch_query('application.services.match._match_recipients.Commentator.filter',
                               [SimpleNamespace(user=crew_user)]), \
             self._patch_query('application.services.match._match_recipients.Tracker.filter', []), \
             self._patch_query('application.services.match._match_recipients.MatchWatcher.filter',
                               [SimpleNamespace(user=watcher_user)]):
            await real_notify_service.notify_match_participants(match, "msg", notice=MATCH_CHECKED_IN)

        assert real_notify_service.discord_service.send_dm.await_count == 2
        assert real_notify_service.discord_service.send_dm_with_unwatch_button.await_count == 1
