"""DB-backed coverage for the two MatchService writes: create_match, update_match.

Split from ``test_match_service_coverage.py`` when that module approached the
800-line guideline — these two methods were half of it, and they are one
subject: the scheduling write, its validation ladder, the roster and crew sync
it performs, and the acknowledgment reseeding and notification fan-out that
follow a changed time or player set.

Real ``MatchService`` against the in-memory SQLite ``db`` fixture: real
repositories, real ``MatchScheduleService``, permission gates satisfied by
granting real roles rather than monkeypatching. Discord sends are captured
(never awaited) by the autouse ``stub_discord_queue`` fixture. Row builders come
from ``_match_service_setup``.
"""

import json

import pytest

from application.events import EventType, event_bus
from application.services.audit_service import AuditActions
from application.services.match.match_service import MatchService
from application.services.system_config_service import KEY_TOURNAMENT_HOURS
from models import (
    AuditLog,
    Commentator,
    Match,
    MatchAcknowledgment,
    MatchPlayers,
    Stage,
    SystemConfiguration,
    TournamentPlayers,
    Tracker,
)
from tests.factories import utc
from tests.services._match_service_setup import make_player, make_staff, make_tournament

pytestmark = pytest.mark.usefixtures("stub_discord_queue")


@pytest.fixture
async def service():
    # Constructed inside the running loop: MatchService -> MatchScheduleService ->
    # DiscordService lazily builds the singleton commands.Bot.
    return MatchService()


@pytest.fixture
def captured_events():
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    yield seen
    event_bus.unsubscribe(token)


# ---------------------------------------------------------------------------
# create_match
# ---------------------------------------------------------------------------


class TestCreateMatch:
    async def test_full_create_with_crew_and_stream_candidate(self, service, db, captured_events):
        actor = await make_staff()
        t = await make_tournament()
        stage = await Stage.create(name="Stage A")
        alice = await make_player("alice", "Alice")
        bob = await make_player("bob", "Bob")
        commentator = await make_player("carl", "Carl")
        tracker = await make_player("dana", "Dana")

        match = await service.create_match(
            tournament_id=t.id,
            scheduled_date="2025-01-15",
            scheduled_time="14:30",
            player_ids=[alice.id, bob.id],
            comment="gogo",
            stage_id=stage.id,
            commentator_ids=[commentator.id],
            tracker_ids=[tracker.id],
            is_stream_candidate=True,
            actor=actor,
        )

        assert match.is_stream_candidate is True
        assert match.comment == "gogo"
        assert match.stage_id == stage.id

        player_ids = {p.user_id for p in await MatchPlayers.filter(match=match)}
        assert player_ids == {alice.id, bob.id}
        # fresh players are auto-enrolled in the tournament (covers enrollment branch)
        assert await TournamentPlayers.filter(tournament=t, user=alice).exists()

        comms = await Commentator.filter(match=match)
        assert len(comms) == 1 and comms[0].user_id == commentator.id and comms[0].approved is True
        trks = await Tracker.filter(match=match)
        assert len(trks) == 1 and trks[0].user_id == tracker.id and trks[0].approved is True

        # actor is not a player here, so nobody is auto-acked
        acks = await MatchAcknowledgment.filter(match=match)
        assert {a.user_id for a in acks} == {alice.id, bob.id}
        assert all(a.acknowledged_at is None for a in acks)

        assert EventType.MATCH_CREATED in [e.event_type for e in captured_events]

    async def test_actor_who_is_player_is_auto_acked(self, service, db):
        actor = await make_staff("boss")
        t = await make_tournament()
        opponent = await make_player("opp", "Opp")
        match = await service.create_match(
            tournament_id=t.id,
            scheduled_date="2025-01-15",
            scheduled_time="14:30",
            player_ids=[actor.id, opponent.id],
            actor=actor,
        )
        actor_ack = await MatchAcknowledgment.get(match=match, user=actor)
        opp_ack = await MatchAcknowledgment.get(match=match, user=opponent)
        assert actor_ack.acknowledged_at is not None and actor_ack.auto_acknowledged is True
        assert opp_ack.acknowledged_at is None

    async def test_tournament_admin_may_create_without_staff_role(self, service, db):
        # non-staff actor that is a tournament admin exercises the else/ensure branch
        actor = await make_player("ta")
        t = await make_tournament()
        await t.admins.add(actor)
        player = await make_player()
        match = await service.create_match(
            tournament_id=t.id,
            scheduled_date="2025-01-15",
            scheduled_time="14:30",
            player_ids=[player.id],
            actor=actor,
        )
        assert match.id is not None

    async def test_non_admin_actor_denied(self, service, db):
        actor = await make_player("nobody")
        t = await make_tournament()
        player = await make_player()
        with pytest.raises(PermissionError):
            await service.create_match(
                tournament_id=t.id,
                scheduled_date="2025-01-15",
                scheduled_time="14:30",
                player_ids=[player.id],
                actor=actor,
            )

    async def test_missing_player_user_raises(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        with pytest.raises(ValueError, match="User 424242 not found"):
            await service.create_match(
                tournament_id=t.id,
                scheduled_date="2025-01-15",
                scheduled_time="14:30",
                player_ids=[424242],
                actor=actor,
            )

    async def test_missing_commentator_user_raises(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        with pytest.raises(ValueError, match="not found"):
            await service.create_match(
                tournament_id=t.id,
                scheduled_date="2025-01-15",
                scheduled_time="14:30",
                player_ids=[player.id],
                commentator_ids=[424242],
                actor=actor,
            )

    async def test_missing_tracker_user_raises(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        with pytest.raises(ValueError, match="not found"):
            await service.create_match(
                tournament_id=t.id,
                scheduled_date="2025-01-15",
                scheduled_time="14:30",
                player_ids=[player.id],
                tracker_ids=[424242],
                actor=actor,
            )

    async def test_within_configured_hours_succeeds(self, service, db):
        await SystemConfiguration.create(
            name=KEY_TOURNAMENT_HOURS,
            value=json.dumps({"2025-01-15": {"open": "00:00", "close": "23:00"}}),
        )
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        match = await service.create_match(
            tournament_id=t.id,
            scheduled_date="2025-01-15",
            scheduled_time="14:30",
            player_ids=[player.id],
            actor=actor,
        )
        assert match.id is not None

    async def test_outside_configured_hours_raises(self, service, db):
        await SystemConfiguration.create(
            name=KEY_TOURNAMENT_HOURS,
            value=json.dumps({"2025-01-15": {"open": "20:00", "close": "23:00"}}),
        )
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        with pytest.raises(ValueError, match="can only start between"):
            await service.create_match(
                tournament_id=t.id,
                scheduled_date="2025-01-15",
                scheduled_time="14:30",
                player_ids=[player.id],
                actor=actor,
            )

    async def test_already_enrolled_player_not_re_enrolled(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        await TournamentPlayers.create(tournament=t, user=player)
        await service.create_match(
            tournament_id=t.id,
            scheduled_date="2025-01-15",
            scheduled_time="14:30",
            player_ids=[player.id],
            actor=actor,
        )
        assert await TournamentPlayers.filter(tournament=t, user=player).count() == 1


# ---------------------------------------------------------------------------
# update_match
# ---------------------------------------------------------------------------


async def _seed_match(service, actor, tournament, player_ids, **kwargs):
    return await service.create_match(
        tournament_id=tournament.id,
        scheduled_date="2025-01-15",
        scheduled_time="14:30",
        player_ids=player_ids,
        actor=actor,
        **kwargs,
    )


class TestUpdateMatch:
    async def test_not_found_raises(self, service, db):
        actor = await make_staff()
        with pytest.raises(ValueError, match="Match 999999 not found"):
            await service.update_match(match_id=999999, comment="x", actor=actor)

    async def test_reschedule_with_player_and_crew_resync(self, service, db, captured_events):
        actor = await make_staff()
        t = await make_tournament()
        alice = await make_player("alice", "Alice")
        bob = await make_player("bob", "Bob")
        carol = await make_player("carol", "Carol")
        commentator = await make_player("comm", "Comm")
        tracker = await make_player("trk", "Trk")

        match = await _seed_match(service, actor, t, [alice.id, bob.id])
        captured_events.clear()

        updated = await service.update_match(
            match_id=match.id,
            scheduled_date="2025-01-16",
            scheduled_time="16:00",
            player_ids=[alice.id, carol.id],  # drop bob, add carol
            commentator_ids=[commentator.id],
            tracker_ids=[tracker.id],
            comment="updated",
            actor=actor,
        )

        assert updated.comment == "updated"
        player_ids = {p.user_id for p in await MatchPlayers.filter(match=match)}
        assert player_ids == {alice.id, carol.id}
        assert not await MatchPlayers.filter(match=match, user=bob).exists()
        assert await Commentator.filter(match=match, user=commentator).exists()
        assert await Tracker.filter(match=match, user=tracker).exists()
        assert await TournamentPlayers.filter(tournament=t, user=carol).exists()

        assert EventType.MATCH_RESCHEDULED in [e.event_type for e in captured_events]
        # The audit row names the same action the event does.
        rows = await AuditLog.filter(action=AuditActions.MATCH_RESCHEDULED)
        assert [json.loads(r.details)['match_id'] for r in rows] == [match.id]

    async def test_players_only_change_emits_update_event(self, service, db, captured_events):
        actor = await make_staff()
        t = await make_tournament()
        alice = await make_player("alice", "Alice")
        bob = await make_player("bob", "Bob")
        carol = await make_player("carol", "Carol")
        match = await _seed_match(service, actor, t, [alice.id, bob.id])
        captured_events.clear()

        await service.update_match(match_id=match.id, player_ids=[alice.id, carol.id], actor=actor)

        types = [e.event_type for e in captured_events]
        assert EventType.MATCH_UPDATED in types
        assert EventType.MATCH_RESCHEDULED not in types
        assert not await AuditLog.filter(action=AuditActions.MATCH_RESCHEDULED).exists()

    async def test_clear_timestamps_and_seed(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        match = await _seed_match(service, actor, t, [player.id])
        # stamp lifecycle fields directly so the clear_* flags have something to clear
        match.seated_at = utc(2025, 1, 15, 18)
        match.started_at = utc(2025, 1, 15, 18, 5)
        match.finished_at = utc(2025, 1, 15, 18, 30)
        match.confirmed_at = utc(2025, 1, 15, 18, 35)
        await match.save()

        await service.update_match(
            match_id=match.id,
            clear_seated=True,
            clear_started=True,
            clear_finished=True,
            clear_confirmed=True,
            clear_seed=True,
            actor=actor,
        )

        refreshed = await Match.get(id=match.id)
        assert refreshed.seated_at is None
        assert refreshed.started_at is None
        assert refreshed.finished_at is None
        assert refreshed.confirmed_at is None
        assert refreshed.generated_seed_id is None

    async def test_rewind_clears_every_later_step_together(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        match = await _seed_match(service, actor, t, [player.id])
        match.seated_at = utc(2025, 1, 15, 18)
        match.started_at = utc(2025, 1, 15, 18, 5)
        match.finished_at = utc(2025, 1, 15, 18, 30)
        await match.save()

        await service.rewind_match(match.id, 'checked_in', actor=actor)

        refreshed = await Match.get(id=match.id)
        assert refreshed.seated_at is not None
        assert refreshed.started_at is None
        assert refreshed.finished_at is None

    async def test_rewind_rejects_a_step_the_match_has_not_passed(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        match = await _seed_match(service, actor, t, [player.id])
        match.seated_at = utc(2025, 1, 15, 18)
        await match.save()

        with pytest.raises(ValueError):
            await service.rewind_match(match.id, 'started', actor=actor)

    async def test_clear_seed_without_a_seed_is_refused(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        match = await _seed_match(service, actor, t, [player.id])

        with pytest.raises(ValueError):
            await service.clear_match_seed(match.id, actor=actor)

    async def test_crew_removed_when_cleared(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        commentator = await make_player("comm", "Comm")
        match = await _seed_match(service, actor, t, [player.id], commentator_ids=[commentator.id])
        assert await Commentator.filter(match=match).count() == 1

        await service.update_match(match_id=match.id, commentator_ids=[], actor=actor)
        assert await Commentator.filter(match=match).count() == 0

    async def test_reassign_tournament(self, service, db):
        actor = await make_staff()
        t = await make_tournament(name="Old")
        other = await make_tournament(name="New")
        player = await make_player()
        match = await _seed_match(service, actor, t, [player.id])

        await service.update_match(match_id=match.id, tournament_id=other.id, actor=actor)
        assert (await Match.get(id=match.id)).tournament_id == other.id

    async def test_reassign_tournament_rejected_without_target_admin(self, service, db):
        # A non-staff TA of the SOURCE tournament must not be able to move a
        # match into a tournament they do not administer.
        source = await make_tournament(name="Source")
        target = await make_tournament(name="Target")
        ta = await make_player("srcadmin")
        await source.admins.add(ta)
        player = await make_player()
        match = await _seed_match(service, await make_staff(), source, [player.id])
        with pytest.raises(PermissionError, match="cannot move match into tournament"):
            await service.update_match(match_id=match.id, tournament_id=target.id, actor=ta)
        # The reassignment was rejected before any write.
        assert (await Match.get(id=match.id)).tournament_id == source.id

    async def test_reassign_tournament_allowed_for_admin_of_both(self, service, db):
        source = await make_tournament(name="Source")
        target = await make_tournament(name="Target")
        ta = await make_player("bothadmin")
        await source.admins.add(ta)
        await target.admins.add(ta)
        player = await make_player()
        match = await _seed_match(service, await make_staff(), source, [player.id])
        await service.update_match(match_id=match.id, tournament_id=target.id, actor=ta)
        assert (await Match.get(id=match.id)).tournament_id == target.id

    async def test_sync_players_missing_user_raises(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        match = await _seed_match(service, actor, t, [player.id])
        with pytest.raises(ValueError, match="User 424242 not found"):
            await service.update_match(match_id=match.id, player_ids=[player.id, 424242], actor=actor)

    async def test_sync_crew_missing_user_raises(self, service, db):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        match = await _seed_match(service, actor, t, [player.id])
        with pytest.raises(ValueError, match="User 424242 not found"):
            await service.update_match(match_id=match.id, commentator_ids=[424242], actor=actor)


# ---------------------------------------------------------------------------
# admin-assigned crew is an approval
# ---------------------------------------------------------------------------


def _crew_ack_dms(captured):
    return [c for c in captured if c.cr_code.co_name == "send_dm_with_crew_acknowledgment_button"]


def _approval_events(captured_events):
    return [e for e in captured_events if e.event_type == EventType.CREW_APPROVAL_CHANGED]


class TestAdminAssignedCrew:
    async def test_create_announces_each_assignee(
        self, service, db, captured_events, stub_discord_queue,
    ):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        commentator = await make_player("comm", "Comm")
        tracker = await make_player("trk", "Trk")

        match = await _seed_match(
            service, actor, t, [player.id],
            commentator_ids=[commentator.id], tracker_ids=[tracker.id],
            stage_id=(await Stage.create(name="Kraid")).id,
        )

        events = _approval_events(captured_events)
        assert sorted((e.payload["crew_type"], e.payload["user_id"]) for e in events) == [
            ("commentator", commentator.id), ("tracker", tracker.id),
        ]
        assert all(e.payload["approved"] is True and e.payload["match_id"] == match.id for e in events)
        assert len(_crew_ack_dms(stub_discord_queue)) == 2
        assert (await Commentator.get(match=match)).approved_by_id == actor.id
        assert await AuditLog.filter(action=AuditActions.CREW_APPROVAL_CHANGED).count() == 2

    async def test_update_announces_only_new_assignees(
        self, service, db, captured_events, stub_discord_queue,
    ):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        kept = await make_player("kept", "Kept")
        added = await make_player("added", "Added")
        match = await _seed_match(
            service, actor, t, [player.id], commentator_ids=[kept.id],
            stage_id=(await Stage.create(name="Kraid")).id,
        )
        captured_events.clear()
        for coro in stub_discord_queue:
            coro.close()
        stub_discord_queue.clear()

        await service.update_match(
            match_id=match.id, commentator_ids=[kept.id, added.id], actor=actor,
        )

        assert [e.payload["user_id"] for e in _approval_events(captured_events)] == [added.id]
        assert len(_crew_ack_dms(stub_discord_queue)) == 1

    async def test_no_crew_change_announces_nothing(
        self, service, db, captured_events, stub_discord_queue,
    ):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        match = await _seed_match(service, actor, t, [player.id])

        await service.update_match(match_id=match.id, comment="x", actor=actor)

        assert _approval_events(captured_events) == []
        assert _crew_ack_dms(stub_discord_queue) == []

    async def test_update_announces_removed_crew(
        self, service, db, captured_events, stub_discord_queue,
    ):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        dropped = await make_player("dropped", "Dropped")
        match = await _seed_match(
            service, actor, t, [player.id], tracker_ids=[dropped.id],
            stage_id=(await Stage.create(name="Kraid")).id,
        )
        captured_events.clear()
        for coro in stub_discord_queue:
            coro.close()
        stub_discord_queue.clear()

        await service.update_match(match_id=match.id, tracker_ids=[], actor=actor)

        removed = [e for e in captured_events if e.event_type == EventType.CREW_SIGNUP_REMOVED]
        assert [(e.payload["user_id"], e.payload["role"]) for e in removed] == [(dropped.id, "tracker")]
        assert removed[0].actor_id == actor.id
        assert [c.cr_code.co_name for c in stub_discord_queue] == ["send_dm"]

    async def test_unstaged_match_records_crew_but_sends_no_dm(
        self, service, db, captured_events, stub_discord_queue,
    ):
        """Crew hear about a match only once it's on a stage."""
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        commentator = await make_player("comm", "Comm")

        match = await _seed_match(
            service, actor, t, [player.id], commentator_ids=[commentator.id],
        )
        await service.update_match(match_id=match.id, commentator_ids=[], actor=actor)

        assert len(_approval_events(captured_events)) == 1
        assert _crew_ack_dms(stub_discord_queue) == []
        assert "send_dm" not in [c.cr_code.co_name for c in stub_discord_queue]


# ---------------------------------------------------------------------------
# a player taken off the line-up is told
# ---------------------------------------------------------------------------


def _removed_player_dms(captured):
    return [c for c in captured if c.cr_code.co_name == "notify_players_removed"]


class TestRemovedPlayers:
    async def test_update_dms_only_the_removed_player(self, service, db, stub_discord_queue):
        actor = await make_staff()
        t = await make_tournament(name="Cup")
        kept = await make_player("kept", "Kept")
        dropped = await make_player("dropped", "Dropped")
        added = await make_player("added", "Added")
        match = await _seed_match(service, actor, t, [kept.id, dropped.id])
        for coro in stub_discord_queue:
            coro.close()
        stub_discord_queue.clear()

        await service.update_match(match_id=match.id, player_ids=[kept.id, added.id], actor=actor)

        dms = _removed_player_dms(stub_discord_queue)
        assert len(dms) == 1
        args = dms[0].cr_frame.f_locals
        assert args["discord_ids"] == [int(dropped.discord_id)]
        assert "taken you off a match in **Cup**" in args["message"]

    async def test_unchanged_line_up_dms_nobody(self, service, db, stub_discord_queue):
        actor = await make_staff()
        t = await make_tournament()
        player = await make_player()
        match = await _seed_match(service, actor, t, [player.id])

        await service.update_match(match_id=match.id, player_ids=[player.id], actor=actor)

        assert _removed_player_dms(stub_discord_queue) == []
