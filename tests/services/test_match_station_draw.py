"""Unit tests for the check-in seating draw.

The draw is random by design, so almost nothing here asserts on a *specific*
station. What it asserts on is the constraint set: which side each player
landed on, whether the pair are neighbours, and which relaxations came back.
A test that pinned a label would pass or fail by coin flip.

Repositories are mocked — the pool's tenant scoping lives in
tests/tenancy/test_station_tenant_isolation.py.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.services.match.match_service import MatchService
from models import StationSide

pytestmark = pytest.mark.usefixtures("bypass_auth")

LEFT, RIGHT = StationSide.LEFT, StationSide.RIGHT


def _station(name, side=None, position=None, section='row'):
    return SimpleNamespace(
        name=name, side=side, position=position, section=section, is_active=True,
    )


def _match(player_count=2):
    return SimpleNamespace(
        id=1,
        tournament=SimpleNamespace(is_racetime_enabled=False),
        players=[SimpleNamespace(id=100 + i) for i in range(player_count)],
    )


@pytest.fixture
def service():
    svc = object.__new__(MatchService)
    svc.repository = MagicMock()
    svc.repository.occupied_stations = AsyncMock(return_value={})
    svc.station_repository = MagicMock()
    svc.station_repository.get_active = AsyncMock(return_value=[])
    svc._require_match = AsyncMock(return_value=_match())
    return svc


def _pool(service, stations):
    service.station_repository.get_active = AsyncMock(return_value=stations)


def _sides_of(service, suggestion):
    pool = {s.name: s for s in service.station_repository.get_active.return_value}
    return [pool[label].side for label in suggestion.assignments.values()]


def _two_sided_pool():
    return (
        [_station(f'L{n}', LEFT, n, 'left wall') for n in range(1, 5)]
        + [_station(f'R{n}', RIGHT, n, 'right wall') for n in range(1, 5)]
    )


class TestOppositeSides:
    async def test_players_land_on_different_sides(self, service):
        _pool(service, _two_sided_pool())
        for _ in range(20):
            suggestion = await service.suggest_stations(1)
            left, right = _sides_of(service, suggestion)
            assert left != right
        assert suggestion.relaxations == []

    async def test_both_players_get_a_station(self, service):
        _pool(service, _two_sided_pool())
        suggestion = await service.suggest_stations(1)
        assert set(suggestion.assignments) == {100, 101}
        assert len(set(suggestion.assignments.values())) == 2

    async def test_the_draw_is_not_the_same_pair_every_time(self, service):
        _pool(service, _two_sided_pool())
        seen = set()
        for _ in range(30):
            suggestion = await service.suggest_stations(1)
            seen.add(tuple(sorted(suggestion.assignments.values())))
        assert len(seen) > 1

    async def test_a_full_far_side_falls_back_and_says_so(self, service):
        _pool(service, _two_sided_pool())
        service.repository.occupied_stations = AsyncMock(
            return_value={f'R{n}': 9 for n in range(1, 5)}
        )
        suggestion = await service.suggest_stations(1)
        assert _sides_of(service, suggestion) == [LEFT, LEFT]
        assert any('same side' in note for note in suggestion.relaxations)

    async def test_an_unsided_pool_says_the_layout_is_missing(self, service):
        _pool(service, [_station(str(n)) for n in range(1, 5)])
        suggestion = await service.suggest_stations(1)
        assert any('no side recorded' in note for note in suggestion.relaxations)
        assert len(set(suggestion.assignments.values())) == 2


class TestSpread:
    async def test_a_seat_beside_a_live_match_is_avoided(self, service):
        _pool(service, [
            _station('L1', LEFT, 1), _station('L2', LEFT, 4),
            _station('L3', LEFT, 5), _station('R1', RIGHT, 1),
        ])
        service.repository.occupied_stations = AsyncMock(
            return_value={'L0': 9, 'L9': 9}
        )
        service.station_repository.get_active = AsyncMock(return_value=[
            _station('L0', LEFT, 2), _station('L9', LEFT, 6),
            _station('L1', LEFT, 1), _station('L2', LEFT, 4),
            _station('L3', LEFT, 5), _station('R1', RIGHT, 1),
        ])
        for _ in range(20):
            suggestion = await service.suggest_stations(1)
            assert 'L2' in suggestion.assignments.values()
            assert suggestion.relaxations == []

    async def test_a_crowded_room_seats_anyway_and_says_so(self, service):
        service.station_repository.get_active = AsyncMock(return_value=[
            _station('L1', LEFT, 1), _station('L2', LEFT, 2),
            _station('R1', RIGHT, 1), _station('R2', RIGHT, 2),
        ])
        service.repository.occupied_stations = AsyncMock(
            return_value={'L2': 9, 'R2': 9}
        )
        suggestion = await service.suggest_stations(1)
        assert sorted(suggestion.assignments.values()) == ['L1', 'R1']
        assert any('next to' in note for note in suggestion.relaxations)

    async def test_the_crowded_warning_is_said_once(self, service):
        service.station_repository.get_active = AsyncMock(return_value=[
            _station('L1', LEFT, 1), _station('L2', LEFT, 2),
            _station('R1', RIGHT, 1), _station('R2', RIGHT, 2),
        ])
        service.repository.occupied_stations = AsyncMock(
            return_value={'L2': 9, 'R2': 9}
        )
        suggestion = await service.suggest_stations(1)
        crowded = [n for n in suggestion.relaxations if 'next to' in n]
        assert len(crowded) == 1

    async def test_stations_in_different_sections_are_not_neighbours(self, service):
        service.station_repository.get_active = AsyncMock(return_value=[
            _station('L1', LEFT, 2, 'back row'), _station('R1', RIGHT, 2, 'far row'),
            _station('X1', LEFT, 1, 'front row'), _station('X2', RIGHT, 1, 'other row'),
        ])
        service.repository.occupied_stations = AsyncMock(
            return_value={'X1': 9, 'X2': 9}
        )
        suggestion = await service.suggest_stations(1)
        assert suggestion.relaxations == []

    async def test_a_pool_with_no_positions_never_warns_about_crowding(self, service):
        service.station_repository.get_active = AsyncMock(return_value=[
            _station('L1', LEFT), _station('L2', LEFT),
            _station('R1', RIGHT), _station('R2', RIGHT),
        ])
        service.repository.occupied_stations = AsyncMock(return_value={'L2': 9})
        suggestion = await service.suggest_stations(1)
        assert not any('next to' in note for note in suggestion.relaxations)


class TestRefusals:
    async def test_occupied_stations_are_never_drawn(self, service):
        _pool(service, _two_sided_pool())
        service.repository.occupied_stations = AsyncMock(
            return_value={'L1': 9, 'L2': 9, 'R1': 9}
        )
        for _ in range(20):
            suggestion = await service.suggest_stations(1)
            drawn = set(suggestion.assignments.values())
            assert not drawn & {'L1', 'L2', 'R1'}

    async def test_a_match_without_two_players_is_refused(self, service):
        _pool(service, _two_sided_pool())
        service._require_match = AsyncMock(return_value=_match(player_count=3))
        with pytest.raises(ValueError, match='exactly two players'):
            await service.suggest_stations(1)

    async def test_an_empty_pool_is_refused(self, service):
        with pytest.raises(ValueError, match='not defined any stations'):
            await service.suggest_stations(1)

    async def test_a_full_room_is_refused(self, service):
        _pool(service, _two_sided_pool())
        service.repository.occupied_stations = AsyncMock(
            return_value={s.name: 9 for s in _two_sided_pool()[:7]}
        )
        with pytest.raises(ValueError, match='Fewer than two stations'):
            await service.suggest_stations(1)

    async def test_racetime_tournaments_have_no_stations_to_draw(self, service):
        _pool(service, _two_sided_pool())
        match = _match()
        match.tournament = SimpleNamespace(is_racetime_enabled=True)
        service._require_match = AsyncMock(return_value=match)
        with pytest.raises(ValueError, match=r'racetime\.gg'):
            await service.suggest_stations(1)

    async def test_the_draw_writes_nothing(self, service):
        _pool(service, _two_sided_pool())
        await service.suggest_stations(1)
        for player in service._require_match.return_value.players:
            assert not hasattr(player, 'assigned_station')
