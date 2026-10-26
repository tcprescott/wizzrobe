"""The "Result in" DM: tournament admins hear each result as it first lands."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services import result_alerts
from application.services.challonge_service import ChallongeService
from application.services.match import MatchService
from application.services.notification_catalog import ADMINS, RESULT_IN
from application.services.startgg_connection_service import StartggConnectionService
from application.services.startgg_service import StartggService
from application.utils import discord_messages_results as text
from application.utils.discord_messages_results import Placing
from application.utils.mocks.mock_startgg import mock_event_full
from models import (
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    Match,
    MatchPlayers,
    NotificationMode,
    Tournament,
    TournamentNotificationPreference,
)
from tests.factories import make_member, make_user, utc

pytestmark = pytest.mark.usefixtures("db", "bypass_auth")


@pytest.fixture
def sent(monkeypatch):
    calls = []

    class Recorder:
        async def send_dm(self, user_id, message, *, embed=None, link=None, notice=None, **_):
            calls.append({'to': int(user_id), 'message': message, 'embed': embed, 'link': link, 'notice': notice})
            return True, None

    monkeypatch.setattr(result_alerts, 'DiscordService', Recorder)
    return calls


def _queued(captured, name):
    return [c for c in captured if c.__name__ == name]


def _site_results(captured):
    """``(site, url, results)`` of each queued site DM, read off the unstarted coroutine."""
    return [
        (c.cr_frame.f_locals['site'], c.cr_frame.f_locals['site_url'], c.cr_frame.f_locals['results'])
        for c in _queued(captured, 'site_results_in')
    ]


async def _match(*players, admins=()):
    tournament = await Tournament.create(name='ALTTPR Main')
    for admin in admins:
        await tournament.admins.add(admin)
    match = await Match.create(tournament=tournament, scheduled_at=utc(2026, 10, 26, 14))
    rows = [await MatchPlayers.create(match=match, user=p) for p in players]
    return tournament, match, rows


def test_catalogue_entry_is_silent_for_admins():
    assert RESULT_IN.defaults == {ADMINS: NotificationMode.SILENT}


def test_board_puts_the_winner_first_with_times_or_scores():
    race = [Placing('Bob', 2, 6350), Placing('Alice', 1, 6133)]
    assert text.result_board(race) == '1st  **Alice** · 1:42:13\n2nd  Bob · 1:45:50'
    assert text.result_line(race) == '**Alice** beat Bob in 1:42:13'
    sets = [Placing('Carol', 1, score=2), Placing('Dave', 2, score=1)]
    assert text.result_line(sets) == '**Carol** beat Dave 2–1'


def test_a_big_race_shows_its_top_three_and_a_count():
    field = [Placing(f'P{n}', n, 3600 + n) for n in range(1, 41)]
    board = text.result_board(field)
    assert board.count('\n') == 3 and board.endswith('…and 37 more')


async def test_recording_the_first_result_queues_the_admin_dm(stub_discord_queue):
    staff = await make_member(9001, 'staffer')
    alice = await make_member(9002, 'alice')
    bob = await make_member(9003, 'bob')
    _, match, (a, b) = await _match(alice, bob)

    await MatchService().record_match_result(match.id, a.id, actor=staff)
    assert len(_queued(stub_discord_queue, 'match_result_in')) == 1

    await MatchService().record_match_result(match.id, b.id, actor=staff)
    assert len(_queued(stub_discord_queue, 'match_result_in')) == 1, 'a correction sends nothing new'


async def test_a_result_copied_from_the_bracket_site_is_not_announced_twice(stub_discord_queue):
    staff = await make_member(9001, 'staffer')
    alice = await make_member(9002, 'alice')
    bob = await make_member(9003, 'bob')
    _, match, (a, _b) = await _match(alice, bob)
    await MatchService().record_match_result(match.id, a.id, actor=staff, announce=False)
    assert _queued(stub_discord_queue, 'match_result_in') == []


async def test_a_forfeit_is_a_first_result_too(stub_discord_queue):
    from models import ForfeitReason
    staff = await make_member(9001, 'staffer')
    alice = await make_member(9002, 'alice')
    bob = await make_member(9003, 'bob')
    _, match, (_a, b) = await _match(alice, bob)
    await MatchService().forfeit_match(match.id, b.id, ForfeitReason.NO_SHOW, staff)
    [queued] = _queued(stub_discord_queue, 'match_result_in')
    assert queued.cr_frame.f_locals['source'] == result_alerts.FORFEIT


async def test_admins_hear_it_but_not_whoever_recorded_it_or_the_players(sent):
    hurfy = await make_member(9010, 'hurfy')
    rick = await make_member(9011, 'rick')
    opted_out = await make_member(9012, 'quiet')
    alice = await make_member(9002, 'alice')
    bob = await make_member(9003, 'bob')
    tournament, match, (a, b) = await _match(alice, bob, admins=(hurfy, rick, opted_out, alice))
    await TournamentNotificationPreference.create(user=opted_out, tournament=tournament, staff_alerts=False)
    a.finish_rank, b.finish_rank = 1, 2
    await a.save()
    await b.save()

    await result_alerts.match_result_in(match.id, rick.id)

    assert [c['to'] for c in sent] == [9010]
    dm = sent[0]
    assert dm['notice'] == RESULT_IN.to(ADMINS)
    assert dm['embed'].title == '🏁 ALTTPR Main'
    assert '1st  **alice**' in dm['embed'].description
    assert 'Recorded by rick' in dm['embed'].description
    assert 'Played <t:' in dm['embed'].description
    assert dm['link'].label == 'Open the match'
    assert dm['message'] == '**alice** beat bob in ALTTPR Main. Recorded by rick.'


async def test_no_result_sends_nothing(sent):
    hurfy = await make_member(9010, 'hurfy')
    _, match, _ = await _match(await make_member(9002, 'alice'), await make_member(9003, 'bob'), admins=(hurfy,))
    await result_alerts.match_result_in(match.id, None)
    assert sent == []


async def test_a_busy_sync_sends_one_roundup(sent):
    hurfy = await make_member(9010, 'hurfy')
    tournament = await Tournament.create(name='Super Metroid')
    await tournament.admins.add(hurfy)
    one = result_alerts.SiteResultIn(placings=[Placing('Carol', 1, score=2), Placing('Dave', 2, score=1)])

    await result_alerts.site_results_in(tournament.id, 'Challonge', 'https://challonge.com/sm', [one])
    assert len(sent) == 1 and sent[0]['link'].label == 'Open on Challonge'
    assert 'Reported on Challonge' in sent[0]['embed'].description

    sent.clear()
    await result_alerts.site_results_in(tournament.id, 'Challonge', 'https://challonge.com/sm', [one] * 5)
    assert len(sent) == 1
    assert sent[0]['embed'].title == '🏁 Super Metroid · 5 results from Challonge'
    assert sent[0]['embed'].description.count('**Carol** beat Dave 2–1') == 5


class TestChallongeSync:
    def _remote(self, state, winner=None):
        return {
            'tournament': {'id': 'T1', 'name': 'T', 'url': 'https://challonge.com/t1', 'state': 'underway'},
            'participants': [
                {'participant_id': '241', 'name': 'Carol', 'challonge_user_id': None, 'username': None},
                {'participant_id': '244', 'name': 'Dave', 'challonge_user_id': None, 'username': None},
            ],
            'matches': [{
                'match_id': 'M1', 'state': state, 'round': 1, 'identifier': 'A',
                'player1_participant_id': '241', 'player2_participant_id': '244',
                'winner_participant_id': winner, 'scores': {'241': 2, '244': 1} if winner else {},
            }],
        }

    async def _sync(self, remote, tournament, actor):
        api = MagicMock()
        api.get_tournament_full = AsyncMock(return_value=remote)
        service = ChallongeService()
        service._api_client = lambda: api
        await service.sync_bracket(tournament.id, actor, force=True)

    async def test_a_match_finished_on_challonge_is_announced_once(self, stub_discord_queue):
        actor = await make_user(1, 'staff')
        tournament = await Tournament.create(
            name='T', challonge_tournament_id='T1', challonge_tournament_url='https://challonge.com/t1',
        )
        await self._sync(self._remote('complete', '241'), tournament, actor)
        assert _queued(stub_discord_queue, 'site_results_in') == [], 'first sight of a bracket is not news'

        await ChallongeMatch.filter(challonge_match_id='M1').update(state=ChallongeMatchState.OPEN)
        await self._sync(self._remote('complete', '241'), tournament, actor)
        assert len(_queued(stub_discord_queue, 'site_results_in')) == 1

        await self._sync(self._remote('complete', '241'), tournament, actor)
        assert len(_queued(stub_discord_queue, 'site_results_in')) == 1

    async def test_the_result_carries_names_and_score(self, stub_discord_queue):
        actor = await make_user(1, 'staff')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        await self._sync(self._remote('open'), tournament, actor)
        await self._sync(self._remote('complete', '241'), tournament, actor)
        [(site, _url, results)] = _site_results(stub_discord_queue)
        assert site == 'Challonge'
        assert [(p.name, p.rank, p.score) for p in results[0].placings] == [('Carol', 1, 2), ('Dave', 2, 1)]

    async def test_a_booked_match_with_its_result_is_skipped(self, stub_discord_queue):
        actor = await make_user(1, 'staff')
        tournament = await Tournament.create(name='T', challonge_tournament_id='T1')
        await self._sync(self._remote('open'), tournament, actor)
        carol = await make_member(9020, 'carol')
        match = await Match.create(tournament=tournament)
        await MatchPlayers.create(match=match, user=carol, finish_rank=1)
        await ChallongeMatch.filter(challonge_match_id='M1').update(match_id=match.id)
        assert await ChallongeParticipant.filter(tournament=tournament).count() == 2

        await self._sync(self._remote('complete', '241'), tournament, actor)
        assert _site_results(stub_discord_queue) == []


class TestStartggSync:
    async def test_a_set_finished_on_startgg_is_announced(self, monkeypatch, stub_discord_queue):
        full = mock_event_full('tournament/test/event/singles')

        async def _client(self):
            client = MagicMock()
            client.get_event_full = AsyncMock(side_effect=lambda slug: __import__('copy').deepcopy(full))
            return client

        async def has_access(self):
            return True

        async def quiet(*_a, **_k):
            return None

        monkeypatch.setattr(StartggService, '_client', _client)
        monkeypatch.setattr(StartggConnectionService, 'has_access', has_access)
        monkeypatch.setattr(StartggService, '_notify_matchup_ready', staticmethod(quiet))
        actor = await make_user(1, 'staff')
        tournament = await Tournament.create(name='OoTR')
        await StartggService().link_event(tournament.id, 'https://www.start.gg/tournament/test/event/singles', actor)
        assert _site_results(stub_discord_queue) == [], 'linking announces nothing'

        for pool in full['pools'].values():
            for s in pool['sets']:
                if s['set_id'] == '8101':
                    s.update(complete=True, winner_id='9001', completed_at=datetime.now(timezone.utc))
                    s['slots'][0]['score'], s['slots'][1]['score'] = 2, 0
        await StartggService().sync_event(tournament.id, actor, force=True)

        [(site, url, results)] = _site_results(stub_discord_queue)
        assert site == 'start.gg' and url == 'https://www.start.gg/tournament/test/event/singles'
        assert len(results) == 1
        assert [(p.rank, p.score) for p in results[0].placings] == [(1, 2), (2, 0)]
        assert results[0].round_label == 'Semi-Final'
