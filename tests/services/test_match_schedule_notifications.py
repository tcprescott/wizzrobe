"""Coverage for MatchScheduleService's notification fan-out.

Every notifier's success, opt-out, DM-failure and exception path against the
real in-memory ORM: match crew, the acknowledgment request, participants,
tournament subscribers, and the recipient list they all share.

Split from ``test_match_schedule_coverage.py`` when that file crossed the
800-line guideline. The sibling keeps seeding and the confirm/Challonge push,
and ``test_match_schedule_service.py`` keeps the lifecycle transitions and the
DM message builders; the service double both halves drive lives in
``_match_schedule_setup``.
"""

from unittest.mock import ANY, AsyncMock

import pytest

from application.services.notification_catalog import (
    CREW,
    FOLLOWERS,
    MATCH_CHECKED_IN,
    MATCH_SCHEDULED,
    WATCHERS,
)
from models import (
    Commentator,
    Match,
    MatchAcknowledgment,
    MatchNotificationLevel,
    MatchPlayers,
    MatchWatcher,
    Stage,
    Tournament,
    TournamentNotificationPreference,
    Tracker,
)
from tests.factories import utc
from tests.services._match_schedule_setup import build_service, make_dm_user


@pytest.fixture
def service():
    return build_service()


class TestNotifyMatchCrew:
    async def test_approved_commentator_gets_plain_dm(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, stage=await Stage.create(name="Kraid"))
        await Commentator.create(match=m, user=await make_dm_user(111, name="c"), approved=True)

        await service.notify_match_crew(m, "hello crew")

        service.discord_service.send_dm.assert_awaited_once_with(
            111, "hello crew\n\nYou're on this match as commentator.", embed=None, link=ANY, notice=MATCH_SCHEDULED.to(CREW),
        )
        link = service.discord_service.send_dm.await_args.kwargs['link']
        assert link.url.endswith(f'/home/my-crew?match={m.id}')
        service.discord_service.send_dm_with_unwatch_button.assert_not_awaited()

    async def test_approved_tracker_gets_plain_dm(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, stage=await Stage.create(name="Kraid"))
        await Tracker.create(match=m, user=await make_dm_user(112, name="tr"), approved=True)

        await service.notify_match_crew(m, "hi")

        service.discord_service.send_dm.assert_awaited_once_with(
            112, "hi\n\nYou're on this match as tracker.", embed=None, link=ANY, notice=MATCH_SCHEDULED.to(CREW),
        )

    async def test_crew_on_both_slots_reads_both_in_text_and_title(self, service, db):
        from application.utils.discord_embeds import COLOR_SCHEDULED, match_embed

        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, stage=await Stage.create(name="Kraid"))
        u = await make_dm_user(113, name="both")
        await Commentator.create(match=m, user=u, approved=True)
        await Tracker.create(match=m, user=u, approved=True)
        embed = match_embed(title='📣 Match scheduled', color=COLOR_SCHEDULED, tournament='T')

        await service.notify_match_crew(
            m, "hi", embed, crew_message=lambda roles: f"slot={'+'.join(roles)}",
        )

        service.discord_service.send_dm.assert_awaited_once()
        args, kwargs = service.discord_service.send_dm.await_args
        assert args == (113, 'slot=commentator+tracker')
        assert kwargs['embed'].title == '📣 Match scheduled · your commentator and tracker slot'
        assert embed.title == '📣 Match scheduled'

    async def test_watcher_gets_unwatch_button_dm(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        await MatchWatcher.create(match=m, user=await make_dm_user(222, name="w"))

        await service.notify_match_crew(m, "crew msg", watcher_message="watch msg")

        service.discord_service.send_dm.assert_not_awaited()
        service.discord_service.send_dm_with_unwatch_button.assert_awaited_once_with(222, "watch msg", m.id, embed=None, link=None, notice=MATCH_SCHEDULED.to(WATCHERS))

    async def test_crew_who_also_watches_reads_the_crew_copy(self, service, db):
        """Watching adds the Unwatch button; it does not demote them to a spectator."""
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, stage=await Stage.create(name="Kraid"))
        u = await make_dm_user(223, name="cw")
        await Commentator.create(match=m, user=u, approved=True)
        await MatchWatcher.create(match=m, user=u)

        await service.notify_match_crew(m, "crew msg", watcher_message="watch msg")

        service.discord_service.send_dm_with_unwatch_button.assert_awaited_once_with(
            223, "crew msg\n\nYou're on this match as commentator.", m.id, embed=None, link=ANY, notice=MATCH_SCHEDULED.to(CREW),
        )

    async def test_crew_on_an_unstaged_match_hear_nothing(self, service, db):
        """Crew hear about a match only while it's on a stage."""
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        await Commentator.create(match=m, user=await make_dm_user(231, name="c"), approved=True)
        await Tracker.create(match=m, user=await make_dm_user(232, name="tr"), approved=True)

        await service.notify_match_crew(m, "crew msg")
        await service.notify_match_participants(m, "hi", notice=MATCH_CHECKED_IN)

        service.discord_service.send_dm.assert_not_awaited()
        service.discord_service.send_dm_with_unwatch_button.assert_not_awaited()

    async def test_crew_who_watches_an_unstaged_match_reads_the_watcher_copy(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        u = await make_dm_user(233, name="cw")
        await Commentator.create(match=m, user=u, approved=True)
        await MatchWatcher.create(match=m, user=u)

        await service.notify_match_crew(m, "crew msg", watcher_message="watch msg")

        service.discord_service.send_dm_with_unwatch_button.assert_awaited_once_with(
            233, "watch msg", m.id, embed=None, link=None, notice=MATCH_SCHEDULED.to(WATCHERS),
        )

    async def test_player_who_is_crew_is_excluded(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        u = await make_dm_user(333, name="pc")
        await MatchPlayers.create(match=m, user=u)
        await Commentator.create(match=m, user=u, approved=True)

        await service.notify_match_crew(m, "hi")

        service.discord_service.send_dm.assert_not_awaited()
        service.discord_service.send_dm_with_unwatch_button.assert_not_awaited()

    async def test_unapproved_crew_skipped(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        await Commentator.create(match=m, user=await make_dm_user(444, name="pending"), approved=False)

        await service.notify_match_crew(m, "hi")

        service.discord_service.send_dm.assert_not_awaited()

    async def test_crew_with_discord_off_still_reaches_send_dm(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, stage=await Stage.create(name="Kraid"))
        await Commentator.create(match=m, user=await make_dm_user(555, name="mute", dm=False), approved=True)

        await service.notify_match_crew(m, "hi")

        # Discord off only drops the Discord copy, inside send_dm; devices still need the call.
        service.discord_service.send_dm.assert_awaited_once()

    async def test_dm_failure_is_swallowed(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, stage=await Stage.create(name="Kraid"))
        await Commentator.create(match=m, user=await make_dm_user(666, name="c"), approved=True)
        service.discord_service.send_dm = AsyncMock(return_value=(False, "blocked"))

        await service.notify_match_crew(m, "hi")

        service.discord_service.send_dm.assert_awaited_once()

    async def test_unexpected_exception_is_swallowed(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        await Commentator.create(match=m, user=await make_dm_user(777, name="c"), approved=True)
        service.discord_service.send_dm = AsyncMock(side_effect=RuntimeError("kaboom"))

        await service.notify_match_crew(m, "hi")


class TestNotifyAcknowledgmentRequest:
    async def _setup_match(self, *, stage=True):
        t = await Tournament.create(name="T")
        sr = await Stage.create(name="Stage 1") if stage else None
        return await Match.create(tournament=t, stage=sr, scheduled_at=utc(2025, 1, 15, 19, 30))

    async def test_sends_ack_button_to_pending_player(self, service, db):
        m = await self._setup_match()
        player = await make_dm_user(111, name="alice")
        await MatchPlayers.create(match=m, user=player)
        await MatchAcknowledgment.create(match=m, user=player, acknowledged_at=None)

        await service.notify_acknowledgment_request(m, rescheduled=False)

        call = service.discord_service.send_dm_with_acknowledgment_button.call_args
        assert call.args[0] == 111
        assert call.args[2] == m.id
        assert call.kwargs['link'].url.endswith(f'/home/player?match={m.id}')

    async def test_rescheduled_flag_still_sends(self, service, db):
        m = await self._setup_match(stage=False)
        player = await make_dm_user(112, name="bob")
        await MatchPlayers.create(match=m, user=player)
        await MatchAcknowledgment.create(match=m, user=player, acknowledged_at=None)

        await service.notify_acknowledgment_request(m, rescheduled=True)

        service.discord_service.send_dm_with_acknowledgment_button.assert_awaited_once()

    async def test_already_acknowledged_is_skipped(self, service, db):
        m = await self._setup_match()
        player = await make_dm_user(113, name="carol")
        await MatchPlayers.create(match=m, user=player)
        await MatchAcknowledgment.create(match=m, user=player, acknowledged_at=utc(2025, 1, 15, 20, 0))

        await service.notify_acknowledgment_request(m, rescheduled=False)

        service.discord_service.send_dm_with_acknowledgment_button.assert_not_awaited()

    async def test_player_with_discord_off_still_reaches_send_dm(self, service, db):
        m = await self._setup_match()
        player = await make_dm_user(114, name="dave", dm=False)
        await MatchPlayers.create(match=m, user=player)
        await MatchAcknowledgment.create(match=m, user=player, acknowledged_at=None)

        await service.notify_acknowledgment_request(m, rescheduled=False)

        # Discord off only drops the Discord copy, inside send_dm; devices still need the call.
        service.discord_service.send_dm_with_acknowledgment_button.assert_awaited_once()

    async def test_dm_failure_is_swallowed(self, service, db):
        m = await self._setup_match()
        player = await make_dm_user(115, name="erin")
        await MatchPlayers.create(match=m, user=player)
        await MatchAcknowledgment.create(match=m, user=player, acknowledged_at=None)
        service.discord_service.send_dm_with_acknowledgment_button = AsyncMock(return_value=(False, "blocked"))

        await service.notify_acknowledgment_request(m, rescheduled=False)

        service.discord_service.send_dm_with_acknowledgment_button.assert_awaited_once()

    async def test_unexpected_exception_is_swallowed(self, service, db):
        m = await self._setup_match()
        player = await make_dm_user(116, name="fred")
        await MatchPlayers.create(match=m, user=player)
        await MatchAcknowledgment.create(match=m, user=player, acknowledged_at=None)
        service.discord_service.send_dm_with_acknowledgment_button = AsyncMock(side_effect=RuntimeError("x"))

        await service.notify_acknowledgment_request(m, rescheduled=False)


class TestNotifyMatchParticipantsBranches:
    """Branches the sibling suite leaves uncovered: the approved-tracker path
    and the DM-failure / unexpected-exception handling."""

    async def test_approved_tracker_receives_dm(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, stage=await Stage.create(name="Kraid"))
        await Tracker.create(match=m, user=await make_dm_user(444, name="tr"), approved=True)

        await service.notify_match_participants(m, "hi", notice=MATCH_CHECKED_IN)

        service.discord_service.send_dm.assert_awaited_once_with(
            444, "hi\n\nYou're on this match as tracker.", embed=None, link=None, notice=MATCH_CHECKED_IN.to(CREW),
        )

    async def test_dm_failure_is_swallowed(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        await MatchPlayers.create(match=m, user=await make_dm_user(445, name="p"))
        service.discord_service.send_dm = AsyncMock(return_value=(False, "blocked"))

        await service.notify_match_participants(m, "hi", notice=MATCH_CHECKED_IN)

        service.discord_service.send_dm.assert_awaited_once()

    async def test_unexpected_exception_is_swallowed(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        await MatchPlayers.create(match=m, user=await make_dm_user(446, name="p"))
        service.discord_service.send_dm = AsyncMock(side_effect=RuntimeError("kaboom"))

        await service.notify_match_participants(m, "hi", notice=MATCH_CHECKED_IN)


class TestNotifySubscriberFailurePaths:
    async def test_tournament_subscriber_dm_failure_is_swallowed(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        sub = await make_dm_user(555, name="sub")
        await TournamentNotificationPreference.create(
            user=sub, tournament=t, match_notifications=MatchNotificationLevel.ALL,
        )
        service.discord_service.send_dm_with_crew_buttons = AsyncMock(return_value=(False, "blocked"))

        await service.notify_tournament_subscribers_scheduled(m, "msg", [])

        service.discord_service.send_dm_with_crew_buttons.assert_awaited_once_with(
            555, "msg", m.id, embed=None, link=ANY, notice=MATCH_SCHEDULED.to(FOLLOWERS), broadcaster=False,
        )

    async def test_stream_candidate_subscriber_dm_failure_is_swallowed(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19, 30))
        sub = await make_dm_user(556, name="sub")
        await TournamentNotificationPreference.create(
            user=sub, tournament=t, match_notifications=MatchNotificationLevel.STREAMED_AND_CANDIDATES,
        )
        service.discord_service.send_dm_with_crew_buttons = AsyncMock(return_value=(False, "blocked"))

        await service.notify_stream_candidate_subscribers(m, [])

        call = service.discord_service.send_dm_with_crew_buttons.call_args
        assert call.args[0] == 556


class TestNotifyMatchParticipantsCommentatorAndWatcher:
    async def test_approved_commentator_receives_dm(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, stage=await Stage.create(name="Kraid"))
        await Commentator.create(match=m, user=await make_dm_user(211, name="c"), approved=True)

        await service.notify_match_participants(m, "hi", notice=MATCH_CHECKED_IN)

        service.discord_service.send_dm.assert_awaited_once_with(
            211, "hi\n\nYou're on this match as commentator.", embed=None, link=None, notice=MATCH_CHECKED_IN.to(CREW),
        )

    async def test_watcher_receives_unwatch_button(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        await MatchWatcher.create(match=m, user=await make_dm_user(212, name="w"))

        await service.notify_match_participants(m, "hi", notice=MATCH_CHECKED_IN)

        service.discord_service.send_dm.assert_not_awaited()
        service.discord_service.send_dm_with_unwatch_button.assert_awaited_once_with(212, "hi", m.id, embed=None, link=None, notice=MATCH_CHECKED_IN.to(WATCHERS))


class TestNotifyStreamCandidateSubscribersExtra:
    async def test_returns_early_when_match_has_stage(self, service, db):
        t = await Tournament.create(name="T")
        sr = await Stage.create(name="Stage 1")
        m = await Match.create(tournament=t, stage=sr)
        sub = await make_dm_user(700, name="sub")
        await TournamentNotificationPreference.create(
            user=sub, tournament=t, match_notifications=MatchNotificationLevel.STREAMED_AND_CANDIDATES,
        )

        await service.notify_stream_candidate_subscribers(m, [])

        service.discord_service.send_dm_with_crew_buttons.assert_not_awaited()

    async def test_send_raising_is_swallowed(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19, 30))
        sub = await make_dm_user(701, name="sub")
        await TournamentNotificationPreference.create(
            user=sub, tournament=t, match_notifications=MatchNotificationLevel.STREAMED_AND_CANDIDATES,
        )
        service.discord_service.send_dm_with_crew_buttons = AsyncMock(side_effect=RuntimeError("boom"))

        await service.notify_stream_candidate_subscribers(m, [])


class TestNotifyTournamentSubscribersScheduledExtra:
    async def test_send_raising_is_swallowed(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        sub = await make_dm_user(710, name="sub")
        await TournamentNotificationPreference.create(
            user=sub, tournament=t, match_notifications=MatchNotificationLevel.ALL,
        )
        service.discord_service.send_dm_with_crew_buttons = AsyncMock(side_effect=RuntimeError("boom"))

        await service.notify_tournament_subscribers_scheduled(m, "msg", [])


class TestNotifyMatchScheduledFanOut:
    async def test_enqueues_ack_crew_and_subscribers(self, service, db):
        t = await Tournament.create(name="T")
        sr = await Stage.create(name="Stage 1")
        m = await Match.create(tournament=t, stage=sr, scheduled_at=utc(2025, 1, 15, 19, 30))
        await MatchPlayers.create(match=m, user=await make_dm_user(811, name="p"))

        captured = []
        import application.services.discord.discord_queue as dq
        original = dq.enqueue
        dq.enqueue = captured.append
        try:
            await service.notify_match_scheduled(m, rescheduled=False, is_stream_candidate=False)
        finally:
            dq.enqueue = original
        enqueued = {c.cr_code.co_qualname for c in captured}
        for coro in captured:
            coro.close()
        assert enqueued == {
            "MatchNotificationMixin.notify_acknowledgment_request",
            "MatchNotificationMixin.notify_match_crew",
            "MatchHardPresetService.send_offer",
            "MatchNotificationMixin.notify_tournament_subscribers_scheduled",
        }

    async def test_stream_candidate_adds_fourth_enqueue(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19, 30))
        await MatchPlayers.create(match=m, user=await make_dm_user(812, name="p"))

        captured = []
        import application.services.discord.discord_queue as dq
        original = dq.enqueue
        dq.enqueue = captured.append
        try:
            await service.notify_match_scheduled(m, rescheduled=True, is_stream_candidate=True)
        finally:
            dq.enqueue = original
        enqueued = {c.cr_code.co_qualname for c in captured}
        for coro in captured:
            coro.close()
        assert enqueued == {
            "MatchNotificationMixin.notify_acknowledgment_request",
            "MatchNotificationMixin.notify_match_crew",
            "MatchHardPresetService.send_offer",
            "MatchNotificationMixin.notify_tournament_subscribers_scheduled",
            "MatchNotificationMixin.notify_stream_candidate_subscribers",
        }

    async def test_notify_stream_candidate_enqueues_subscriber_fanout(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, 19, 30))
        await MatchPlayers.create(match=m, user=await make_dm_user(813, name="p"))

        captured = []
        import application.services.discord.discord_queue as dq
        original = dq.enqueue
        dq.enqueue = captured.append
        try:
            await service.notify_stream_candidate(m)
        finally:
            dq.enqueue = original
        for coro in captured:
            coro.close()
        assert len(captured) == 1


class TestCollectNotifiedDiscordIds:
    async def test_appends_unique_tracker_id(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        await MatchPlayers.create(match=m, user=await make_dm_user(111, name="p"))
        await Commentator.create(match=m, user=await make_dm_user(222, name="c"), approved=True)
        await Tracker.create(match=m, user=await make_dm_user(333, name="tr"), approved=True)

        ids = await service._collect_notified_discord_ids(m)

        assert ids == [111, 222, 333]

    async def test_unapproved_tracker_excluded(self, service, db):
        t = await Tournament.create(name="T")
        m = await Match.create(tournament=t)
        await MatchPlayers.create(match=m, user=await make_dm_user(111, name="p"))
        await Tracker.create(match=m, user=await make_dm_user(999, name="tr"), approved=False)

        ids = await service._collect_notified_discord_ids(m)

        assert ids == [111]
