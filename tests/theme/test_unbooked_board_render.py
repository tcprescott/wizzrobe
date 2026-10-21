"""The "Not booked yet" board stays cheap to build at event size (SGLMAN-1W).

Production built every row, and every row's ⋮ menu, in one synchronous pass;
a few hundred matchups blocked the shared event loop for 2s. These run the real
NiceGUI element tree (no browser) and check the three things that keep it cheap:
menus are built on first open, a row stays a handful of elements, and the build
hands the loop back between slices.
"""

import asyncio
from types import SimpleNamespace

from nicegui import Client, core, ui
from nicegui.page import page

from application.services.bracket_sources import UnbookedMatch
from theme import unbooked_board
from theme.unbooked_board import UnbookedBoard


def _items(n: int) -> list[UnbookedMatch]:
    out = []
    for i in range(n):
        tournament = SimpleNamespace(
            id=i % 4, name=f'Tournament {i % 4}',
            challonge_tournament_url='https://challonge.com/x', bracket_url=None,
        )
        ready = i % 3 != 0
        out.append(UnbookedMatch(
            source='challonge', source_label='Challonge', key=i, tournament=tournament,
            round_label='Round 1', player_names=[f'a{i}', f'b{i}'],
            player_ids=[1, 2] if ready else [1, None],
            unlinked=[] if ready else [f'b{i}'],
        ))
    return out


def _board(items: list[UnbookedMatch]) -> UnbookedBoard:
    board = UnbookedBoard.__new__(UnbookedBoard)
    board.status = 'all'
    board.search = ''
    board.can_open_native_entrants = False
    board._items = items
    return board


async def _build(n: int):
    core.loop = asyncio.get_running_loop()
    client = Client(page('/'), request=None)
    board = _board(_items(n))
    with client:
        with ui.column() as column:
            await board._rows()
    return client, column


async def test_row_menus_are_built_on_first_open():
    client, column = await _build(30)
    assert not [e for e in column.descendants() if isinstance(e, ui.menu_item)]
    menus = [e for e in column.descendants() if isinstance(e, ui.menu)]
    assert len(menus) == 30
    button = menus[0].parent_slot.parent
    with client:
        for listener in button._event_listeners.values():
            if listener.type == 'click':
                listener.handler(None)
    labels = [e for e in menus[0].descendants() if isinstance(e, ui.menu_item)]
    assert labels, 'opening the ⋮ fills its menu'


async def test_a_row_stays_a_handful_of_elements():
    _, column = await _build(100)
    per_row = len(list(column.descendants())) / 100
    assert per_row < 14, f'{per_row:.1f} elements per row'


async def test_building_hands_the_loop_back(monkeypatch):
    monkeypatch.setattr(unbooked_board, '_ROWS_PER_SLICE', 10)
    ticks = 0
    done = False

    async def other_user() -> None:
        nonlocal ticks
        while not done:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.ensure_future(other_user())
    await _build(60)
    done = True
    await task
    assert ticks >= 5, 'another task should run between slices of rows'
