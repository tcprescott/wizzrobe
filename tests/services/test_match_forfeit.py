"""Forfeits on a match, and staff dropping a player from a tournament.

Real ``MatchService`` / ``TournamentService`` against the in-memory DB; Discord
DMs are captured unawaited by the suite's ``stub_discord_queue``.
"""

import pytest

from application.events import EventType, event_bus
from application.repositories import ChallongeRepository, TournamentRepository
from application.services.match.match_service import MatchService
from application.services.tournament_service import TournamentService
from application.utils.discord_messages_forfeit import dropped_from_tournament_dm, forfeit_dm
from models import (
    ChallongeParticipant,
    ForfeitReason,
    Match,
    MatchPlayers,
    Role,
    TournamentPlayers,
)
from tests.factories import utc
from tests.services._match_service_setup import make_player, make_staff, make_tournament


@pytest.fixture
async def service():
    return MatchService()


@pytest.fixture
def captured_events():
    seen = []
    token = event_bus.subscribe_sync(seen.append)
    yield seen
    event_bus.unsubscribe(token)


async def head_to_head(tournament=None, *, hour=18):
    t = tournament or await make_tournament()
    match = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 15, hour))
    a = await MatchPlayers.create(match=match, user=await make_player('kara', 'Kara'))
    b = await MatchPlayers.create(match=match, user=await make_player('milo', 'Milo'))
    return match, a, b


class TestForfeitMatch:
    async def test_decides_and_confirms_in_one_step(self, service, db, captured_events, stub_discord_queue):
        staff = await make_staff()
        match, kara, milo = await head_to_head()

        await service.forfeit_match(
            match.id, milo.id, ForfeitReason.NO_SHOW, staff, note='  called twice  ',
        )

        match = await Match.get(id=match.id)
        assert match.seated_at and match.started_at and match.finished_at and match.confirmed_at
        assert match.forfeit_reason is ForfeitReason.NO_SHOW
        assert match.forfeit_note == 'called twice'
        assert (await MatchPlayers.get(id=kara.id)).finish_rank == 1
        assert (await MatchPlayers.get(id=milo.id)).finish_rank is None
        forfeited = [e for e in captured_events if e.event_type == EventType.MATCH_FORFEITED]
        assert forfeited and forfeited[0].payload['reason'] == 'no_show'
        assert forfeited[0].payload['forfeited_user_id'] == milo.user_id
        # Only the forfeit DM goes out: no check-in, start, finish or confirm DMs.
        assert EventType.MATCH_SEATED not in [e.event_type for e in captured_events]
        assert stub_discord_queue

    async def test_a_proctor_cannot_forfeit(self, service, db):
        proctor = await make_player('proc', roles=[Role.PROCTOR])
        match, _, milo = await head_to_head()
        with pytest.raises(PermissionError):
            await service.forfeit_match(match.id, milo.id, ForfeitReason.NO_SHOW, proctor)

    async def test_a_confirmed_match_is_refused(self, service, db):
        staff = await make_staff()
        match, _, milo = await head_to_head()
        await service.forfeit_match(match.id, milo.id, ForfeitReason.WITHDREW, staff)
        with pytest.raises(ValueError, match='already confirmed'):
            await service.forfeit_match(match.id, milo.id, ForfeitReason.WITHDREW, staff)

    async def test_needs_exactly_two_players(self, service, db):
        staff = await make_staff()
        match, _, milo = await head_to_head()
        await MatchPlayers.create(match=match, user=await make_player('third'))
        with pytest.raises(ValueError, match='exactly two players'):
            await service.forfeit_match(match.id, milo.id, ForfeitReason.NO_SHOW, staff)

    async def test_player_must_be_in_the_match(self, service, db):
        staff = await make_staff()
        match, _, _ = await head_to_head()
        with pytest.raises(ValueError, match="isn't part of this match"):
            await service.forfeit_match(match.id, 999999, ForfeitReason.NO_SHOW, staff)

    async def test_note_length_is_capped(self, service, db):
        staff = await make_staff()
        match, _, milo = await head_to_head()
        with pytest.raises(ValueError, match='under 280'):
            await service.forfeit_match(match.id, milo.id, ForfeitReason.NO_SHOW, staff, note='x' * 281)

    async def test_rewinding_past_the_finish_undoes_the_forfeit(self, service, db):
        staff = await make_staff()
        match, _, milo = await head_to_head()
        await service.forfeit_match(match.id, milo.id, ForfeitReason.DISQUALIFIED, staff)

        await service.update_match(match.id, clear_finished=True, clear_confirmed=True, actor=staff)

        match = await Match.get(id=match.id)
        assert match.forfeit_reason is None and match.forfeit_note is None
        assert match.finished_at is None and match.confirmed_at is None

    async def test_a_hand_recorded_result_replaces_the_forfeit(self, service, db):
        staff = await make_staff()
        match, kara, milo = await head_to_head()
        await service.forfeit_match(match.id, milo.id, ForfeitReason.NO_SHOW, staff, note='late')
        await service.update_match(match.id, clear_confirmed=True, actor=staff)

        await service.record_match_result(match.id, milo.id, staff)

        match = await Match.get(id=match.id)
        assert match.forfeit_reason is None and match.forfeit_note is None
        assert (await MatchPlayers.get(id=milo.id)).finish_rank == 1


class TestDropPlayer:
    async def _setup(self):
        staff = await make_staff()
        t = await make_tournament(name='ALTTPR')
        first, kara, milo = await head_to_head(t, hour=18)
        dropped = milo.user
        await TournamentRepository.enroll_player(t, dropped)
        second = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 16, 18))
        rin = await MatchPlayers.create(match=second, user=await make_player('rin', 'Rin'))
        await MatchPlayers.create(match=second, user=dropped)
        return staff, t, dropped, first, second, kara, rin

    async def test_forfeits_every_open_match_and_leaves_the_roster(self, db, captured_events):
        staff, t, dropped, first, second, kara, rin = await self._setup()

        result = await TournamentService().drop_player(
            t, dropped, staff, reason=ForfeitReason.WITHDREW, forfeit_matches=True,
        )

        assert (result.forfeited, result.called_off) == (2, 0)
        assert (await MatchPlayers.get(id=kara.id)).finish_rank == 1
        assert (await MatchPlayers.get(id=rin.id)).finish_rank == 1
        assert (await Match.get(id=second.id)).forfeit_reason is ForfeitReason.WITHDREW
        assert not await TournamentPlayers.filter(tournament=t, user=dropped).exists()
        dropped_events = [e for e in captured_events if e.event_type == EventType.TOURNAMENT_PLAYER_DROPPED]
        assert dropped_events and dropped_events[0].payload['forfeited'] == 2

    async def test_call_off_deletes_the_matches(self, db):
        staff, t, dropped, first, second, _, _ = await self._setup()

        result = await TournamentService().drop_player(
            t, dropped, staff, reason=ForfeitReason.DISQUALIFIED, forfeit_matches=False,
        )

        assert result.called_off == 2
        assert not await Match.filter(id__in=[first.id, second.id]).exists()

    async def test_a_played_match_keeps_its_result(self, db):
        staff, t, dropped, first, _, kara, _ = await self._setup()
        kara.finish_rank = 1
        await kara.save()

        plan = await TournamentService().plan_drop(t, dropped, staff)

        assert [m.id for m in plan.head_to_head] != [] and first.id not in [m.id for m in plan.head_to_head]

    async def test_a_larger_match_just_loses_the_player(self, db):
        staff, t, dropped, *_ = await self._setup()
        race = await Match.create(tournament=t, scheduled_at=utc(2025, 1, 17, 18))
        for name in ('x', 'y'):
            await MatchPlayers.create(match=race, user=await make_player(name))
        await MatchPlayers.create(match=race, user=dropped)

        result = await TournamentService().drop_player(
            t, dropped, staff, reason=ForfeitReason.NO_SHOW, forfeit_matches=True,
        )

        assert result.removed_from == 1
        assert not await MatchPlayers.filter(match=race, user=dropped).exists()
        assert await MatchPlayers.filter(match=race).count() == 2

    async def test_someone_not_in_the_tournament_is_refused(self, db):
        staff = await make_staff()
        t = await make_tournament()
        stranger = await make_player('stranger')
        with pytest.raises(ValueError, match='is not in'):
            await TournamentService().drop_player(
                t, stranger, staff, reason=ForfeitReason.NO_SHOW, forfeit_matches=True,
            )

    async def test_needs_tournament_edit_rights(self, db):
        _, t, dropped, *_ = await self._setup()
        player = await make_player('nobody')
        with pytest.raises(PermissionError):
            await TournamentService().drop_player(
                t, dropped, player, reason=ForfeitReason.NO_SHOW, forfeit_matches=True,
            )


class TestInactiveChallongeEntrants:
    async def test_an_inactive_entrant_leaves_the_roster(self, db):
        from application.services.challonge_service import ChallongeService

        staff = await make_staff()
        t = await make_tournament(challonge_tournament_id='abc')
        player = await make_player('gone')
        await TournamentRepository.enroll_player(t, player)
        participant = await ChallongeParticipant.create(
            tournament=t, challonge_participant_id='p1', name='gone', user=player,
        )
        await ChallongeRepository.set_participant_active(participant, False)

        await ChallongeService()._mirror_players(t, staff)

        assert not await TournamentPlayers.filter(tournament=t, user=player).exists()


class TestCopy:
    def test_each_player_reads_their_own_side(self):
        common = dict(winner_name='Kara', forfeiter_name='Milo', reason_label='No-show')
        assert 'you win' in forfeit_dm('ALTTPR', **common, you='winner')
        assert 'Kara wins this one' in forfeit_dm('ALTTPR', **common, you='forfeiter')
        assert "won't be played" in forfeit_dm('ALTTPR', **common)

    def test_drop_message_counts_the_matches(self):
        text = dropped_from_tournament_dm('ALTTPR', reason_label='Withdrew', forfeited=1, called_off=2)
        assert '1 match goes to your opponents by forfeit' in text
        assert '2 matches were called off' in text
