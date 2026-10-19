"""The bracket ↔ scheduled-match seam (docs/features/brackets.md).

DB-backed (the ``db`` fixture: in-memory SQLite, default tenant id 1). Four units
meet here, and each has a failure mode that is silent in production:

* **U3a** — a cancelled best-of-1 used to strand its matchup as permanently
  unschedulable, invisible to the player dashboard *and* to staff. The first test
  in :class:`TestReleaseOnCancel` is that regression stated as an assertion.
* **U3b** — rewriting a ``Match``'s ranks after its game settled would leave the
  bracket advanced on the old answer, with nothing saying so.
* **U2** — the live status the bracket paints must come from the ``Match``, in a
  fixed number of queries however big the field is.
* **U5** — the matchup-ready DM must fire once per matchup, and must *not* fire
  for advancement or elimination.
"""

import pytest

from application.services.bracket_service import BracketService
from application.services.match.match_service import MatchService
from application.services.match.match_status import MatchStatus
from models import (
    BracketMatchGame,
    BracketMatchGameState,
    BracketMatchState,
    Match,
    MatchPlayers,
    RaceRoomStatus,
    RacetimeBot,
    RacetimeRoom,
    Role,
    Stage,
    Tournament,
    User,
    UserRole,
)
from tests.factories import join_community

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def service() -> BracketService:
    return BracketService()


async def _staff() -> User:
    user = await User.create(discord_id=1234, username='staff')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def _series(service, actor, *, best_of: int = 1, name: str = 'Cup'):
    """A started 2-entrant single-elim whose round-1 matchup is a best-of-N."""
    tournament = await Tournament.create(name=name)
    bracket = await service.create_bracket(
        actor, tournament.id, 'Main', 'single_elim',
        config={'rounds': {'1': {'best_of': best_of}}},
    )
    users = []
    for i in (1, 2):
        user = await User.create(discord_id=1000 + i, username=f'p{i}')
        await join_community(user)
        users.append(user)
        entrant = await service.add_entrant(
            actor, tournament.id, f'P{i}', user_id=user.id,
        )
        await service.enroll(actor, bracket.id, entrant.id, seed=i)
    await service.start_bracket(actor, bracket.id)
    bmatch = (await service.get_open_matches(bracket.id))[0]
    return tournament, bracket, users, bmatch


async def _groups(service, actor, *, name: str = 'Groups Cup'):
    """A started round robin: 6 entrants in 2 groups of 3, so rounds run 1-3.

    Three rounds is the shape that produced the bug — deep enough for the
    elimination naming to reach back to "Quarterfinals".
    """
    tournament = await Tournament.create(name=name)
    bracket = await service.create_bracket(
        actor, tournament.id, 'Groups', 'round_robin', config={'group_count': 2},
    )
    for i in range(1, 7):
        user = await User.create(discord_id=2000 + i, username=f'g{i}')
        await join_community(user)
        entrant = await service.add_entrant(
            actor, tournament.id, f'G{i}', user_id=user.id,
        )
        await service.enroll(actor, bracket.id, entrant.id, seed=i)
    await service.start_bracket(actor, bracket.id)
    bmatch = (await service.get_open_matches(bracket.id))[0]
    return tournament, bracket, bmatch


async def _casual_match(actor, *, name: str = 'Casual') -> Match:
    """A plain scheduled match no bracket ever touched — the common case."""
    tournament = await Tournament.create(name=name)
    player = await User.create(discord_id=9001, username='casual')
    await join_community(player)
    return await MatchService().create_match(
        tournament_id=tournament.id, player_ids=[player.id], actor=actor,
        scheduled_date='2026-06-12', scheduled_time='14:30',
    )


async def _play(service, actor, match: Match, winner: User) -> None:
    await MatchPlayers.filter(match=match, user=winner).update(finish_rank=1)
    await MatchPlayers.filter(match=match).exclude(user=winner).update(finish_rank=2)
    await service.settle_game_if_linked(match, actor)


# ---------------------------------------------------------------------------
# U3a — a cancelled or deleted Match frees its game slot (D3)
# ---------------------------------------------------------------------------
class TestReleaseOnCancel:
    async def test_cancelling_a_bo1_makes_the_matchup_schedulable_again(
        self, service,
    ):
        """The S3 regression, stated as a test.

        Before the fix the game row survived as SCHEDULED with a null match_id,
        its game_number stayed consumed, and the matchup vanished from every
        surface that offers a booking.
        """
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        assert await service.list_open_matches_for_user(users[0].id) == []

        await MatchService().cancel_match(match.id, actor=actor, reason='called off')

        assert await BracketMatchGame.filter(bracket_match_id=bmatch.id).count() == 0
        again = await service.list_open_matches_for_user(users[0].id)
        assert [m.id for m in again] == [bmatch.id]

    async def test_deleting_a_match_frees_the_slot_too(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )

        await MatchService().delete_match(match.id, actor=actor)

        assert await BracketMatchGame.filter(bracket_match_id=bmatch.id).count() == 0
        assert [m.id for m in await service.list_open_matches_for_user(users[0].id)] \
            == [bmatch.id]

    async def test_a_played_games_slot_and_result_survive_the_delete(self, service):
        """A COMPLETE game keeps both: the bracket has already advanced on it."""
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=3)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        await _play(service, actor, match, users[0])

        await MatchService().delete_match(match.id, actor=actor)

        game = await BracketMatchGame.get(bracket_match_id=bmatch.id, game_number=1)
        assert game.state == BracketMatchGameState.COMPLETE
        assert game.winner_entry_id is not None
        assert game.match_id is None  # SET_NULL, as designed

    async def test_a_clinch_does_not_hand_back_the_unplayed_games_slot(
        self, service,
    ):
        """The re-entrancy case.

        ``_clinch`` marks its leftover games CANCELLED *first*, then cancels each
        one's ``Match`` — which now routes through the release. Because release
        acts only on SCHEDULED games, game 3's number stays consumed and the
        decided series is not offered for booking again.
        """
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=3)
        matches = [
            await service.schedule_bracket_match(
                actor, bmatch.id,
                scheduled_date='2026-06-12', scheduled_time=f'1{n}:30',
            )
            for n in range(3)
        ]
        await _play(service, actor, matches[0], users[0])
        await _play(service, actor, matches[1], users[0])  # 2-0 clinch

        game3 = await BracketMatchGame.get(bracket_match_id=bmatch.id, game_number=3)
        assert game3.state == BracketMatchGameState.CANCELLED
        assert await service.list_open_matches_for_user(users[0].id) == []

    async def test_release_is_audited_and_published(self, service):
        from application.events import EventType
        from models import AuditLog

        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        seen = []
        from application.events import event_bus
        token = event_bus.subscribe_sync(
            lambda e: seen.append(e), (EventType.BRACKET_GAME_RELEASED,),
        )
        try:
            await MatchService().cancel_match(match.id, actor=actor, reason='off')
        finally:
            event_bus.unsubscribe(token)

        assert [e.event_type for e in seen] == [EventType.BRACKET_GAME_RELEASED]
        assert seen[0].payload['reason'] == 'off'
        assert await AuditLog.filter(action='bracket.game_released').count() == 1

    async def test_an_unlinked_match_cancels_exactly_as_before(self, service):
        """The overwhelming majority of matches back no game — release is a no-op."""
        actor = await _staff()
        match = await _casual_match(actor)
        await MatchService().cancel_match(match.id, actor=actor)
        assert await Match.filter(id=match.id).count() == 0


# ---------------------------------------------------------------------------
# U3b — one correction path for a settled result (D6)
# ---------------------------------------------------------------------------
class TestSettledResultGuard:
    async def test_recording_a_result_on_a_settled_game_is_blocked(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        await _play(service, actor, match, users[0])

        players = await MatchPlayers.filter(match_id=match.id)
        loser = next(p for p in players if p.user_id == users[1].id)
        with pytest.raises(ValueError, match='already recorded in Main'):
            await MatchService().record_match_result(match.id, loser.id, actor=actor)

    async def test_an_unsettled_game_records_normally(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        players = await MatchPlayers.filter(match_id=match.id)
        winner = next(p for p in players if p.user_id == users[0].id)

        await MatchService().record_match_result(match.id, winner.id, actor=actor)

        refreshed = await MatchPlayers.get(id=winner.id)
        assert refreshed.finish_rank == 1

    async def test_override_result_still_works_on_a_settled_matchup(self, service):
        """The path the guard's message points staff at must stay open."""
        actor = await _staff()
        _, bracket, users, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        await _play(service, actor, match, users[0])
        settled = await service.repository.get_match(bmatch.id)
        assert settled.state == BracketMatchState.COMPLETE

        other = (
            settled.entry2_id if settled.winner_id == settled.entry1_id
            else settled.entry1_id
        )
        amended = await service.override_result(actor, bmatch.id, other)
        assert amended.winner_id == other


# ---------------------------------------------------------------------------
# U2 — live match state into the bracket (D4)
# ---------------------------------------------------------------------------
class TestMatchupLiveState:
    async def test_a_started_game_reads_live_with_a_watch_link(self, service):
        actor = await _staff()
        tournament, bracket, _, bmatch = await _series(service, actor, best_of=1)
        stage = await Stage.create(
            name='Main Stage', stream_url='https://twitch.tv/wizzrobe',
        )
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
            stage_id=stage.id,
        )
        await Match.filter(id=match.id).update(started_at='2026-06-12 18:30:00')

        state = await service.matchup_live_state(
            await service.list_matches(bracket.id)
        )
        assert state[bmatch.id]['status'] == MatchStatus.LIVE
        assert state[bmatch.id]['watch_url'] == 'https://twitch.tv/wizzrobe'

    async def test_an_in_progress_room_makes_it_live_and_supplies_the_link(
        self, service,
    ):
        """The room is the truthier signal for a viewer; ``started_at`` lags the bot."""
        actor = await _staff()
        _, bracket, _, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        bot = await RacetimeBot.create(
            category='alttpr', client_id='c', client_secret='s', name='bot',
        )
        await RacetimeRoom.create(
            bot=bot, slug='alttpr/live-room', category='alttpr',
            status=RaceRoomStatus.IN_PROGRESS, match=match,
        )

        state = await service.matchup_live_state(
            await service.list_matches(bracket.id)
        )
        assert state[bmatch.id]['status'] == MatchStatus.LIVE
        assert state[bmatch.id]['watch_url'] == 'https://racetime.gg/alttpr/live-room'

    async def test_an_unbooked_matchup_reads_unscheduled(self, service):
        actor = await _staff()
        _, bracket, _, bmatch = await _series(service, actor, best_of=1)
        state = await service.matchup_live_state(
            await service.list_matches(bracket.id)
        )
        assert state[bmatch.id]['status'] == MatchStatus.UNSCHEDULED
        assert state[bmatch.id]['watch_url'] == ''

    async def test_a_finished_unconfirmed_game_reads_awaiting_result(self, service):
        actor = await _staff()
        _, bracket, _, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        await Match.filter(id=match.id).update(
            started_at='2026-06-12 18:30:00', finished_at='2026-06-12 20:00:00',
        )
        state = await service.matchup_live_state(
            await service.list_matches(bracket.id)
        )
        assert state[bmatch.id]['status'] == MatchStatus.AWAITING_RESULT

    async def test_the_room_lookup_is_one_query_for_the_whole_field(self, service):
        """A per-card lookup would be an N+1 across a bracket that repaints often."""
        from tortoise import connections

        actor = await _staff()
        _, bracket, _, bmatch = await _series(service, actor, best_of=3)
        for hour in ('14:30', '16:30', '18:30'):
            await service.schedule_bracket_match(
                actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time=hour,
            )
        matches = await service.list_matches(bracket.id)

        conn = connections.get('default')
        original = conn.execute_query
        count = {'n': 0}

        async def counting(*args, **kwargs):
            count['n'] += 1
            return await original(*args, **kwargs)

        conn.execute_query = counting
        try:
            await service.matchup_live_state(matches)
        finally:
            conn.execute_query = original

        assert count['n'] == 1


# ---------------------------------------------------------------------------
# U5 — the one new notification (D7)
# ---------------------------------------------------------------------------
class TestMatchupReadyNotification:
    @staticmethod
    def _capture(monkeypatch) -> list:
        """Record what the matchup-ready fan-out would send, without Discord."""
        sent: list = []

        async def _fake(**kwargs):
            sent.append(kwargs)

        monkeypatch.setattr(
            'application.services._bracket.notifications.BracketNotificationMixin'
            '._send_matchup_ready',
            staticmethod(_fake),
        )
        # discord_queue defers to a worker that no test runs; await inline.
        monkeypatch.setattr(
            'application.services._bracket.notifications.discord_queue.enqueue',
            lambda coro: __import__('asyncio').get_event_loop().create_task(coro),
        )
        return sent

    async def test_generation_invites_both_entrants_of_round_one(
        self, service, monkeypatch,
    ):
        sent = self._capture(monkeypatch)
        actor = await _staff()
        await _series(service, actor, best_of=1)
        import asyncio
        await asyncio.sleep(0)

        assert len(sent) == 2
        assert {s['opponent_name'] for s in sent} == {'P1', 'P2'}
        assert all(s['rebook'] is False for s in sent)

    async def test_the_invitation_links_to_a_dialog_the_entrant_can_use(
        self, service, monkeypatch,
    ):
        """The DM's button opens the schedule dialog, not the bracket page.

        The bracket's own Schedule button is ``is_staff``-gated, so the surface
        this DM used to link to was one neither recipient could act on.
        """
        import asyncio

        sent = self._capture(monkeypatch)
        actor = await _staff()
        await _series(service, actor, best_of=1)
        await asyncio.sleep(0)

        links = [s['schedule_link'] for s in sent]
        assert all(link is not None for link in links), 'the invitation had no button'
        assert all('/home/player?schedule=' in link.url for link in links)
        assert all('/brackets/' not in link.url for link in links)
        # Both entrants are pointed at the same matchup.
        assert len({link.url for link in links}) == 1

    async def test_a_rebook_invitation_says_new_time_on_its_button(
        self, service, monkeypatch,
    ):
        """A released game must not read as a duplicate of the first invitation —
        including on the button, which is the part people actually press."""
        import asyncio

        sent = self._capture(monkeypatch)
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=1)
        sent.clear()
        await service.notify_matchup_reopened(bmatch)
        await asyncio.sleep(0)

        assert sent, 'the reopened matchup announced nothing'
        assert all(s['schedule_link'].label == 'Pick a new time' for s in sent)

    async def test_advancing_invites_the_next_matchup_once_not_twice(
        self, service, monkeypatch,
    ):
        """A matchup whose two slots fill at different times must not double-DM."""
        import asyncio

        actor = await _staff()
        tournament = await Tournament.create(name='Cup')
        bracket = await service.create_bracket(
            actor, tournament.id, 'Main', 'single_elim',
        )
        users = []
        for i in range(1, 5):
            user = await User.create(discord_id=2000 + i, username=f'q{i}')
            await join_community(user)
            users.append(user)
            entrant = await service.add_entrant(
                actor, tournament.id, f'Q{i}', user_id=user.id,
            )
            await service.enroll(actor, bracket.id, entrant.id, seed=i)
        await service.start_bracket(actor, bracket.id)
        await asyncio.sleep(0)

        sent = self._capture(monkeypatch)
        semis = await service.get_open_matches(bracket.id)
        for semi in semis:
            await service.report_result(actor, semi.id, semi.entry1_id)
            await asyncio.sleep(0)

        # Exactly one invitation per entrant of the final — the first semi's
        # result leaves it PENDING (one slot), the second opens it.
        assert len(sent) == 2, [s['opponent_name'] for s in sent]

    async def test_a_released_game_invites_the_entrants_to_rebook(
        self, service, monkeypatch,
    ):
        import asyncio

        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        await asyncio.sleep(0)

        sent = self._capture(monkeypatch)
        await MatchService().cancel_match(match.id, actor=actor, reason='off')
        await asyncio.sleep(0)

        assert len(sent) == 2
        assert all(s['rebook'] is True for s in sent)

    async def test_a_clinch_sends_nothing_to_either_entrant(
        self, service, monkeypatch,
    ):
        """No advancement DM, no elimination DM — the bracket shows both (D7)."""
        import asyncio

        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        await asyncio.sleep(0)

        sent = self._capture(monkeypatch)
        await _play(service, actor, match, users[0])
        await asyncio.sleep(0)

        # The 2-entrant final has no onward matchup, so nothing is bookable and
        # nothing is sent — not "you advanced", not "you're eliminated".
        assert sent == []

    @pytest.mark.parametrize('rebook', [False, True])
    async def test_the_invitation_arrives_silently(self, monkeypatch, rebook):
        """Ready-to-book is a heads-up: the matchup waits in Pick a time."""
        from application.services._bracket.notifications import (
            BracketNotificationMixin,
        )
        from application.services.discord.discord_service import DiscordService

        calls: list = []

        async def _send_dm(self, user_id, message, **kwargs):
            calls.append(kwargs)
            return True, ''

        monkeypatch.setattr(DiscordService, 'send_dm', _send_dm)
        user = await User.create(discord_id=3001, username='silent')
        await BracketNotificationMixin._send_matchup_ready(
            user=user, opponent_name='P2', opponent_seed=2,
            tournament_name='Cup', round_name='Round 1', best_of=1,
            schedule_link=None, rebook=rebook,
        )

        assert len(calls) == 1
        assert calls[0]['silent'] is True


class TestBracketLineForMatch:
    async def test_a_series_game_carries_round_game_and_standing(self, service):
        actor = await _staff()
        _, _, users, bmatch = await _series(service, actor, best_of=3)
        game1 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        game2 = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-13', scheduled_time='14:30',
        )
        await _play(service, actor, game1, users[0])

        assert await service.bracket_line_for_match(game2.id) == \
            'Final · Game 2 of 3 · Series 1-0'

    async def test_a_bo1_carries_only_the_round_name(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=1)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        assert await service.bracket_line_for_match(match.id) == 'Final'

    async def test_a_match_no_bracket_scheduled_carries_nothing(self, service):
        actor = await _staff()
        match = await _casual_match(actor)
        assert await service.bracket_line_for_match(match.id) == ''
        assert await service.match_dm_context(match.id) == ('', False)


class TestMatchupSummary:
    async def test_summary_describes_what_the_match_is_for(self, service):
        actor = await _staff()
        _, _, _, bmatch = await _series(service, actor, best_of=3)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )

        summary = await service.matchup_summary(match.id)
        assert summary['bracket_name'] == 'Main'
        assert summary['round_name'] == 'Final'
        assert summary['game_number'] == 1
        assert summary['best_of'] == 3
        assert summary['standing'] == ''
        assert [e['name'] for e in summary['entrants']] == ['P1', 'P2']
        assert summary['stakes'] == ''  # a final has no onward pointer

    async def test_no_summary_for_an_unlinked_match(self, service):
        actor = await _staff()
        match = await _casual_match(actor)
        assert await service.matchup_summary(match.id) is None


class TestGroupStageRoundNaming:
    """A group stage told its entrants they were in a knockout.

    Round naming is depth-from-the-deepest-round, which is only meaningful in an
    elimination graph. A round-robin group of 3 runs rounds 1-3 with no
    progression pointers, so the same rule read round 3 as the Final and named
    the two before it Semifinals and Quarterfinals — and that string went out in
    the matchup-ready DM as the round the player was about to schedule.
    """

    async def test_a_group_round_is_named_by_number_and_group(self, service):
        actor = await _staff()
        _, bracket, bmatch = await _groups(service, actor)

        assert await service.round_name_for(bracket, 1) == 'Round 1'
        assert await service.round_name_for(bracket, 3) == 'Round 3'
        assert await service.round_name_for_match(bracket, bmatch) == \
            f'Group {bmatch.group_number} · Round {bmatch.round}'

    async def test_the_matchup_ready_dm_carries_the_group_round(
        self, service, monkeypatch,
    ):
        sent = TestMatchupReadyNotification._capture(monkeypatch)
        actor = await _staff()
        await _groups(service, actor)
        import asyncio
        await asyncio.sleep(0)

        names = {kwargs['round_name'] for kwargs in sent}
        assert names, 'the group stage announced no matchups'
        assert not names & {'Quarterfinals', 'Semifinals', 'Final'}
        assert names <= {
            f'Group {g} · Round {r}' for g in (1, 2) for r in (1, 2, 3)
        }

    async def test_the_bracket_line_on_a_group_match(self, service):
        actor = await _staff()
        _, _, bmatch = await _groups(service, actor)
        match = await service.schedule_bracket_match(
            actor, bmatch.id, scheduled_date='2026-06-12', scheduled_time='14:30',
        )
        assert await service.bracket_line_for_match(match.id) == \
            f'Group {bmatch.group_number} · Round {bmatch.round}'
