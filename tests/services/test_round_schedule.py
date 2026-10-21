"""Round schedule: stage keys, windows, the staff editor and booking enforcement."""

from datetime import datetime, time, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services import round_schedule_service as rss
from application.services.challonge_service import ChallongeService
from application.services.round_schedule_service import (
    RoundScheduleService,
    RoundWindowError,
    StageWindow,
)
from application.services.round_windows import (
    RoundWindow,
    elimination_keys,
    stage_label,
    stage_sort_key,
    winners_final_round,
)
from application.services.tournament_config import validate_tournament_config
from models import (
    AuditLog,
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    Match,
    Tournament,
    User,
)

UTC = timezone.utc


def at(d: int, h: int, mi: int = 0) -> datetime:
    return datetime(2026, 10, d, h, mi, tzinfo=UTC)


@pytest.fixture(autouse=True)
def utc_community(monkeypatch):
    async def zone():
        return UTC
    monkeypatch.setattr(rss, '_tenant_zone', zone)


@pytest.fixture
def privileged(monkeypatch):
    def set_to(value: bool):
        async def gate(*_a, **_k):
            return value
        monkeypatch.setattr(rss.AuthService, 'can_edit_tournament', gate)
        async def ensure(ok, message):
            if not ok:
                raise PermissionError(message)
        monkeypatch.setattr(rss.AuthService, 'ensure', ensure)
    return set_to


# ----------------------------------------------------------------------
# Pure: keys, labels, windows
# ----------------------------------------------------------------------
class TestStageKeys:
    def test_single_elimination_counts_back_from_final(self):
        assert elimination_keys([1, 2, 3, 4], double=False) == {1: 'e3', 2: 'e2', 3: 'e1', 4: 'e0'}
        assert stage_label('e0') == 'Final'
        assert stage_label('e1') == 'Semifinals'
        assert stage_label('e2') == 'Quarterfinals'
        assert stage_label('e3', round_number=1) == 'Round 1'

    def test_double_elimination_splits_grand_final(self):
        # 8 players: winners 1-3, losers -1..-4, grand final 4 and its reset 5.
        rounds = [1, 2, 3, 4, 5, -1, -2, -3, -4]
        assert winners_final_round(rounds) == 3
        keys = elimination_keys(rounds, double=True)
        assert keys[3] == 'w0' and keys[1] == 'w2'
        assert keys[4] == keys[5] == 'gf'
        assert keys[-4] == 'l0' and keys[-1] == 'l3'
        assert stage_label('gf') == 'Grand Finals'
        assert stage_label('w0') == 'Winners Final'
        assert stage_label('l0') == 'Losers Final'

    def test_play_order(self):
        keys = ['gf', 'e0', 'group', 'e2', 'e1']
        assert sorted(keys, key=stage_sort_key) == ['group', 'e2', 'e1', 'e0', 'gf']


class TestRoundWindow:
    def test_open_and_close_hold_the_whole_match(self):
        w = RoundWindow(opens=at(20, 18), closes=at(21, 23))
        hour = timedelta(hours=1)
        assert w.slot_fits(at(20, 18), hour, UTC)
        assert not w.slot_fits(at(20, 17), hour, UTC)
        assert w.slot_fits(at(21, 22), hour, UTC)
        assert not w.slot_fits(at(21, 22, 30), hour, UTC)

    def test_daily_hours(self):
        w = RoundWindow(daily_from=time(18), daily_until=time(23))
        hour = timedelta(hours=1)
        assert w.slot_fits(at(20, 19), hour, UTC)
        assert not w.slot_fits(at(20, 12), hour, UTC)
        assert not w.slot_fits(at(20, 22, 30), hour, UTC)

    def test_daily_hours_past_midnight(self):
        w = RoundWindow(daily_from=time(22), daily_until=time(2))
        assert w.slot_fits(at(20, 23), timedelta(hours=2), UTC)

    def test_describe(self):
        assert RoundWindow(opens=at(17, 18), closes=at(18, 23)).describe(UTC) == 'Book between Sat 6 pm and Sun 11 pm'
        assert RoundWindow(closes=at(18, 23)).describe(UTC) == 'Book by Sun 11 pm'
        assert RoundWindow(daily_from=time(18), daily_until=time(23, 30)).describe(UTC) == 'Only 6 pm to 11:30 pm each day'

    def test_native_round_config_reads(self):
        w = RoundWindow.from_config({'scheduled_at': at(20, 18).isoformat()})
        assert w is not None and w.opens == at(20, 18)
        assert RoundWindow.from_config({}) is None


class TestConfigValidation:
    def test_accepts_a_schedule(self):
        cfg = validate_tournament_config({'round_schedule': {
            'e0': {'opens_at': at(20, 18).isoformat(), 'closes_at': at(21, 18).isoformat(),
                   'daily_from': '18:00', 'daily_until': '23:00'},
        }})
        assert cfg['round_schedule']['e0']['daily_from'] == '18:00'

    @pytest.mark.parametrize('entry', [
        {'opens_at': at(21, 18).isoformat(), 'closes_at': at(20, 18).isoformat()},
        {'daily_from': '18:00'},
        {'daily_from': '18:00', 'daily_until': '18:00'},
        {'opens_at': 'soon'},
        {'colour': 'red'},
    ])
    def test_rejects_bad_entries(self, entry):
        with pytest.raises(ValueError):
            validate_tournament_config({'round_schedule': {'e0': entry}})


# ----------------------------------------------------------------------
# Service: editor, lookups, enforcement
# ----------------------------------------------------------------------
async def _challonge_bracket(group: int = 2, rounds=(1, 2, 3)):
    t = await Tournament.create(name='T', challonge_tournament_id='T1', challonge_group_stage=bool(group))
    p1 = await ChallongeParticipant.create(tournament=t, challonge_participant_id='1', name='A')
    p2 = await ChallongeParticipant.create(tournament=t, challonge_participant_id='2', name='B')
    n = 0
    made = {}
    for _ in range(group):
        n += 1
        made.setdefault('group', []).append(await ChallongeMatch.create(
            tournament=t, challonge_match_id=f'g{n}', round=1, group_stage=True,
            state=ChallongeMatchState.OPEN, participant1=p1, participant2=p2,
        ))
    for r in rounds:
        n += 1
        made[r] = await ChallongeMatch.create(
            tournament=t, challonge_match_id=f'm{n}', round=r,
            state=ChallongeMatchState.OPEN, participant1=p1, participant2=p2,
        )
    return t, made


class TestListAndSave:
    async def test_lists_group_and_rounds(self, db, privileged):
        privileged(True)
        actor = await User.create(discord_id=1, username='staff')
        t, _ = await _challonge_bracket()
        stages = await RoundScheduleService().list_stages(t.id, actor)
        assert [s.label for s in stages] == ['Group stage', 'Quarterfinals', 'Semifinals', 'Final']
        assert stages[0].total == 2 and not stages[0].placeholder

    async def test_placeholders_before_the_bracket_exists(self, db, privileged):
        privileged(True)
        actor = await User.create(discord_id=1, username='staff')
        t, _ = await _challonge_bracket(rounds=())
        stages = await RoundScheduleService().list_stages(t.id, actor)
        assert [s.key for s in stages] == ['group', 'e2', 'e1', 'e0']
        assert all(s.placeholder for s in stages[1:])

    async def test_save_audits_and_drops_blank_rows(self, db, privileged):
        privileged(True)
        actor = await User.create(discord_id=1, username='staff')
        t, _ = await _challonge_bracket()
        await RoundScheduleService().save_schedule(t.id, {
            'e0': {'opens': at(20, 18), 'closes': None, 'daily_from': '18:00', 'daily_until': '23:00'},
            'e1': {'opens': None, 'closes': None, 'daily_from': None, 'daily_until': None},
        }, actor)
        await t.refresh_from_db()
        assert set(t.config['round_schedule']) == {'e0'}
        assert t.config['round_schedule']['e0']['daily_until'] == '23:00'
        assert await AuditLog.filter(action='tournament.round_schedule_updated').count() == 1

    async def test_players_cannot_edit(self, db, privileged):
        privileged(False)
        player = await User.create(discord_id=2, username='player')
        t, _ = await _challonge_bracket()
        with pytest.raises(PermissionError):
            await RoundScheduleService().list_stages(t.id, player)
        with pytest.raises(PermissionError):
            await RoundScheduleService().save_schedule(t.id, {}, player)


class TestEnforcement:
    async def _setup(self):
        t, made = await _challonge_bracket()
        t.config = {'round_schedule': {
            'group': {'closes_at': at(20, 0).isoformat()},
            'e1': {'daily_from': '18:00', 'daily_until': '23:00'},
        }}
        t.average_match_duration = 60
        await t.save()
        return t, made

    async def test_lookup_by_stage(self, db):
        t, made = await self._setup()
        service = RoundScheduleService()
        group = await service.window_for_challonge_match(made['group'][0])
        semi = await service.window_for_challonge_match(made[2])
        assert group.label == 'Group stage' and group.window.closes == at(20, 0)
        assert semi.label == 'Semifinals' and semi.window.has_daily_hours
        assert await service.window_for_challonge_match(made[3]) is None

    async def test_player_is_refused(self, db, privileged):
        privileged(False)
        t, made = await self._setup()
        service = RoundScheduleService()
        found = await service.window_for_challonge_match(made[2])
        with pytest.raises(RoundWindowError) as err:
            await service.check_time(found, t, at(21, 12), None)
        assert not err.value.can_override
        assert 'Semifinals' in str(err.value)
        # Allowing the override does nothing for a player.
        with pytest.raises(RoundWindowError):
            await service.check_time(found, t, at(21, 12), None, allow_outside_round=True)
        await service.check_time(found, t, at(21, 19), None)

    async def test_staff_may_book_anyway(self, db, privileged):
        privileged(True)
        t, made = await self._setup()
        service = RoundScheduleService()
        found = StageWindow('Semifinals', RoundWindow(daily_from=time(18), daily_until=time(23)))
        with pytest.raises(RoundWindowError) as err:
            await service.check_time(found, t, at(21, 12), None)
        assert err.value.can_override
        await service.check_time(found, t, at(21, 12), None, allow_outside_round=True)

    async def test_check_reference_reads_the_matchup(self, db, privileged):
        privileged(False)
        t, made = await self._setup()
        service = RoundScheduleService()
        with pytest.raises(RoundWindowError) as err:
            await service.check_reference(at(21, 12), None, challonge_match_id=made[2].id)
        assert not err.value.can_override
        await service.check_reference(at(21, 19), None, challonge_match_id=made[2].id)
        await service.check_reference(at(21, 12), None, challonge_match_id=made[3].id)
        await service.check_reference(at(21, 12), None)

    async def test_challonge_booking_refuses_outside_window(self, db, monkeypatch, privileged):
        privileged(False)
        t, made = await self._setup()
        p1 = await ChallongeParticipant.get(id=made[2].participant1_id)
        p2 = await ChallongeParticipant.get(id=made[2].participant2_id)
        alice = await User.create(discord_id=11, username='alice', challonge_user_id='1001')
        bob = await User.create(discord_id=12, username='bob', challonge_user_id='1002')
        p1.user, p2.user = alice, bob
        await p1.save()
        await p2.save()
        fake_ms = MagicMock()
        fake_ms.book_from_bracket = AsyncMock(return_value=await Match.create(tournament=t))
        monkeypatch.setattr('application.services.challonge_service.MatchService', lambda: fake_ms)
        monkeypatch.setattr('application.timezone_context.current_zone', lambda: UTC, raising=False)

        with pytest.raises(RoundWindowError):
            await ChallongeService().schedule_challonge_match(
                challonge_match_pk=made[2].id, scheduled_date='2026-10-21', scheduled_time='12:00', actor=alice,
            )
        fake_ms.book_from_bracket.assert_not_awaited()


class TestGroupStageFlag:
    async def test_matches_seen_during_group_stage_stay_flagged(self, db):
        from application.repositories.challonge_repository import ChallongeRepository

        t = await Tournament.create(name='T', challonge_tournament_id='T1')
        repo = ChallongeRepository()
        for flag in (True, False):
            await repo.upsert_match(
                t, 'x', 1, ChallongeMatchState.OPEN, None, None, None, group_stage=flag,
            )
        cm = await ChallongeMatch.get(challonge_match_id='x')
        assert cm.group_stage is True


@pytest.mark.parametrize('remote, expected', [
    ({'group_stage': True, 'state': 'group_stages_underway'}, True),
    ({'group_stage': True, 'state': 'underway'}, False),
    ({'group_stage': False, 'state': 'underway'}, False),
    (None, False),
])
def test_group_stage_running(remote, expected):
    assert RoundScheduleService.group_stage_running(remote) is expected
