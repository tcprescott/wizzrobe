"""The start.gg mirror: linking, entrant mapping, booking, DMs and writeback."""

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.errors import FeatureDisabledError
from application.services import bracket_sources
from application.services.startgg_service import StartggService
from application.utils.clients.startgg_client import StartggAPIError, parse_event_slug
from application.utils.mocks.mock_startgg import mock_event_full
from models import (
    AuditLog,
    FeatureFlag,
    Match,
    MatchPlayers,
    StartggEntrant,
    StartggSet,
    StartggSetState,
    Tournament,
    TournamentPlayers,
)
from tests.factories import make_member, make_user

pytestmark = pytest.mark.usefixtures("bypass_auth")

SLUG = 'tournament/test/event/singles'


class FakeClient:
    def __init__(self, full=None):
        self.full = full or mock_event_full(SLUG)
        self.set_complete = False
        self.reported: list = []

    async def get_event_full(self, slug):
        return copy.deepcopy(self.full)

    async def get_set_state(self, set_id):
        return {'complete': self.set_complete, 'entrant_ids': []}

    async def report_winner(self, set_id, winner_entrant_id):
        self.reported.append((set_id, winner_entrant_id))


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(StartggService, '_client', staticmethod(lambda: fake))
    monkeypatch.setattr(StartggService, 'is_configured', staticmethod(lambda: True))
    return fake


@pytest.fixture
def sent(monkeypatch):
    calls: list = []

    async def record(tournament, row):
        calls.append(row.set_id)

    monkeypatch.setattr(StartggService, '_notify_matchup_ready', staticmethod(record))
    return calls


async def _players():
    """player one/two link start.gg on their profile, as the mock event expects."""
    one = await make_member(11, 'one', display_name='Player One', startgg_user_id='39001')
    two = await make_member(12, 'two', display_name='Player Two', startgg_user_id='39002')
    return one, two


async def _linked(client, actor=None):
    actor = actor or await make_user(1, 'staff')
    tournament = await Tournament.create(name='OoTR')
    await StartggService().link_event(tournament.id, f'https://www.start.gg/{SLUG}/overview', actor)
    await tournament.refresh_from_db()
    return tournament, actor


def test_parse_event_slug_accepts_links_and_refuses_a_whole_tournament():
    assert parse_event_slug(f'https://www.start.gg/{SLUG}/brackets/1/2') == SLUG
    assert parse_event_slug(SLUG) == SLUG
    with pytest.raises(ValueError):
        parse_event_slug('https://www.start.gg/tournament/test/details')


class TestLinkAndMirror:
    async def test_link_mirrors_phases_pools_sets_and_roster(self, db, client, sent):
        one, two = await _players()
        tournament, _ = await _linked(client)
        assert tournament.startgg_event_id == '7001'
        assert await StartggEntrant.filter(tournament=tournament).count() == 8
        assert await StartggSet.filter(tournament=tournament).count() == 15
        roster = set(await TournamentPlayers.filter(tournament=tournament).values_list('user_id', flat=True))
        assert roster == {one.id, two.id}

    async def test_grand_final_waits_with_a_label(self, db, client, sent):
        await _players()
        tournament, _ = await _linked(client)
        final = await StartggSet.get(tournament=tournament, set_id='8103')
        assert final.state == StartggSetState.PENDING
        assert (final.slot1_label, final.slot2_label) == ('Winner of A', 'Winner of B')

    async def test_name_match_is_only_a_suggestion_and_only_for_members(self, db, client, sent):
        three = await make_member(13, 'three', display_name='Player Three')
        await make_user(14, 'four', display_name='Player Four')
        tournament, _ = await _linked(client)
        e3 = await StartggEntrant.get(tournament=tournament, entrant_id='9003')
        e4 = await StartggEntrant.get(tournament=tournament, entrant_id='9004')
        assert e3.user_id is None and e3.suggested_user_id == three.id
        assert e4.suggested_user_id is None

    async def test_discord_connected_on_startgg_maps(self, db, client, sent):
        client.full['entrants'][2]['discord_id'] = '555'
        three = await make_member(555, 'three')
        tournament, _ = await _linked(client)
        assert (await StartggEntrant.get(tournament=tournament, entrant_id='9003')).user_id == three.id

    async def test_refuses_a_challonge_linked_tournament(self, db, client):
        actor = await make_user(1, 'staff')
        tournament = await Tournament.create(name='T', challonge_tournament_id='C1')
        with pytest.raises(ValueError, match='Challonge'):
            await StartggService().link_event(tournament.id, SLUG, actor)

    async def test_resync_prunes_what_startgg_dropped(self, db, client, sent):
        await _players()
        tournament, actor = await _linked(client)
        client.full['entrants'] = [e for e in client.full['entrants'] if e['entrant_id'] != '9008']
        await StartggService().sync_event(tournament.id, actor, force=True)
        assert not await StartggEntrant.filter(tournament=tournament, entrant_id='9008').exists()

    async def test_feature_off_refuses(self, db, client, monkeypatch):
        from application.services.feature_flag_service import FeatureFlagService

        async def off(self, flag):
            return flag != FeatureFlag.STARTGG
        monkeypatch.setattr(FeatureFlagService, 'is_enabled', off)
        actor = await make_user(1, 'staff')
        tournament = await Tournament.create(name='T')
        with pytest.raises(FeatureDisabledError):
            await StartggService().link_event(tournament.id, SLUG, actor)


    async def test_only_staff_link_and_unlink(self, db, client, sent, monkeypatch):
        from application.services.auth_service import AuthService

        tournament, actor = await _linked(client)

        async def no(*_a, **_k):
            return False

        async def ensure(ok, message):
            if not ok:
                raise PermissionError(message)

        monkeypatch.setattr(AuthService, 'is_staff', no)
        monkeypatch.setattr(AuthService, 'ensure', ensure)
        with pytest.raises(PermissionError, match='Only staff'):
            await StartggService().unlink_event(tournament.id, actor)
        with pytest.raises(PermissionError, match='Only staff'):
            await StartggService().link_event(tournament.id, SLUG, actor)
        await StartggService().sync_event(tournament.id, actor, force=True)


class TestMatchupReady:
    async def test_open_set_in_last_phase_is_announced_once(self, db, client, sent):
        await _players()
        tournament, actor = await _linked(client)
        assert sent == ['8101']
        await StartggService().sync_event(tournament.id, actor, force=True)
        assert sent == ['8101']

    async def test_staff_assignment_that_completes_a_pair_announces(self, db, client, sent):
        await _players()
        tournament, actor = await _linked(client)
        saria = await make_member(21, 'saria')
        darunia = await make_member(22, 'darunia')
        service = StartggService()
        await service.assign_entrant(
            (await StartggEntrant.get(tournament=tournament, entrant_id='9005')).id, saria.id, actor)
        assert sent == ['8101']
        await service.assign_entrant(
            (await StartggEntrant.get(tournament=tournament, entrant_id='9006')).id, darunia.id, actor)
        assert sent == ['8101', '8102']

    async def test_manual_assignment_survives_a_sync(self, db, client, sent):
        await _players()
        tournament, actor = await _linked(client)
        saria = await make_member(21, 'saria')
        entrant = await StartggEntrant.get(tournament=tournament, entrant_id='9005')
        await StartggService().assign_entrant(entrant.id, saria.id, actor)
        await StartggService().sync_event(tournament.id, actor, force=True)
        await entrant.refresh_from_db()
        assert entrant.user_id == saria.id and entrant.user_assigned_manually

    async def test_assignment_refuses_a_non_member(self, db, client, sent):
        await _players()
        tournament, actor = await _linked(client)
        outsider = await make_user(30, 'outsider')
        entrant = await StartggEntrant.get(tournament=tournament, entrant_id='9005')
        with pytest.raises(ValueError, match="isn't a member"):
            await StartggService().assign_entrant(entrant.id, outsider.id, actor)


class TestBooking:
    async def test_player_books_an_open_set(self, db, client, sent, monkeypatch):
        one, two = await _players()
        tournament, _ = await _linked(client)
        created = await Match.create(tournament=tournament)
        fake = MagicMock()
        fake.submit_match_request = AsyncMock(return_value=created)
        monkeypatch.setattr('application.services.startgg_service.MatchService', lambda: fake)

        bookable = await bracket_sources.list_bookable_for_user(one)
        assert [b.opponent_name for b in bookable] == ['Player Two']
        await bracket_sources.schedule('startgg', bookable[0].key, '2026-10-23', '14:00', one)

        _, kwargs = fake.submit_match_request.call_args
        assert sorted(kwargs['player_ids']) == sorted([one.id, two.id])
        assert kwargs['from_bracket'] is True
        assert (await StartggSet.get(set_id='8101')).match_id == created.id
        assert await bracket_sources.list_bookable_for_user(one) == []

    async def test_sets_outside_the_bookable_phase_cannot_be_booked(self, db, client, sent):
        one, _ = await _players()
        tournament, _ = await _linked(client)
        pool_set = await StartggSet.get(tournament=tournament, set_id='8001')
        with pytest.raises(ValueError, match='ready'):
            await StartggService().schedule_set(pool_set.id, '2026-10-23', '14:00', one)


class TestWriteback:
    async def _confirmed(self, client, sent):
        one, two = await _players()
        tournament, actor = await _linked(client)
        match = await Match.create(tournament=tournament)
        await MatchPlayers.create(match=match, user=one, finish_rank=1)
        await MatchPlayers.create(match=match, user=two, finish_rank=2)
        row = await StartggSet.get(set_id='8101')
        await StartggSet.filter(id=row.id).update(match_id=match.id)
        return tournament, actor, match

    async def test_off_by_default(self, db, client, sent):
        _, actor, match = await self._confirmed(client, sent)
        assert await StartggService().report_result_if_linked(match, actor) is False
        assert client.reported == []

    async def test_reports_the_winner_and_audits(self, db, client, sent):
        tournament, actor, match = await self._confirmed(client, sent)
        await StartggService().set_report_results(tournament.id, True, actor)
        assert await StartggService().report_result_if_linked(match, actor) is True
        assert client.reported == [('8101', '9001')]
        assert await AuditLog.filter(action='startgg.result_reported').exists()

    async def test_never_overwrites_a_result_already_on_startgg(self, db, client, sent):
        tournament, actor, match = await self._confirmed(client, sent)
        await StartggService().set_report_results(tournament.id, True, actor)
        client.set_complete = True
        assert await StartggService().report_result_if_linked(match, actor) is False
        assert client.reported == []

    async def test_api_failure_is_swallowed(self, db, client, sent, monkeypatch):
        tournament, actor, match = await self._confirmed(client, sent)
        await StartggService().set_report_results(tournament.id, True, actor)

        async def boom(*_a, **_k):
            raise StartggAPIError('down', 500)
        monkeypatch.setattr(client, 'report_winner', boom)
        assert await StartggService().report_result_if_linked(match, actor) is False

    async def test_shared_hook_isolates_a_failing_site(self, db, monkeypatch):
        async def boom(self, match, actor):
            raise RuntimeError('nope')
        monkeypatch.setattr(bracket_sources.StartggSource, 'on_match_confirmed', boom)
        await bracket_sources.on_match_confirmed(MagicMock(id=1), None)


class TestSync:
    async def test_sync_all_reports_each_site(self, db, client, sent):
        await _players()
        tournament, actor = await _linked(client)
        other = await Tournament.create(name='Plain')
        results = await bracket_sources.sync_all([tournament, other], actor)
        assert [(r.tournament_name, r.source_label, r.error) for r in results] == [
            ('OoTR', 'start.gg', None),
        ]

    async def test_signup_card_points_at_startgg(self, db, client, sent):
        from application.services.tournament_service import TournamentService

        one, _ = await _players()
        tournament, _ = await _linked(client)
        cards = await TournamentService().list_signup_cards(one)
        card = next(c for c in cards if c.tournament.id == tournament.id)
        assert card.startgg_url and not card.can_sign_up and not card.can_withdraw
        with pytest.raises(ValueError, match=r'start\.gg'):
            await TournamentService().self_enroll(tournament.id, one)
