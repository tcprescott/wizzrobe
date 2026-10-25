"""A player booking a best-of Challonge matchup gets every game's time, not one."""

from types import SimpleNamespace

from theme.dialog import challonge_schedule_dialog as module


def _matchup():
    return SimpleNamespace(
        id=41, round=2, tournament_id=16, tournament=SimpleNamespace(id=16, name='DASH'),
        participant1=SimpleNamespace(user_id=53), participant2=SimpleNamespace(user_id=82),
    )


class _Rounds:
    async def describe_for_challonge_match(self, cm):
        return 'Book by Sunday'


async def test_best_of_three_opens_the_set_dialog(monkeypatch):
    opened = []

    async def best_of_for(source, key):
        assert (source, key) == ('challonge', 41)
        return 3

    class FakeSetDialog:
        def __init__(self, item, actor, on_submit=None):
            opened.append((item, actor, on_submit))

        async def open(self):
            pass

    monkeypatch.setattr(module.bracket_sources, 'best_of_for', best_of_for)
    monkeypatch.setattr(module, 'RoundScheduleService', _Rounds)
    monkeypatch.setattr(module, 'BracketSourceScheduleDialog', FakeSetDialog)

    async def after():
        pass

    actor = object()
    await module.ChallongeScheduleDialog(_matchup(), actor, opponent_name='mm2', on_submit=after).open()

    [(item, got_actor, on_submit)] = opened
    assert (item.source, item.key, item.player_ids) == ('challonge', 41, [53, 82])
    assert item.opponent_name == 'mm2'
    assert item.window_text == 'Book by Sunday'
    assert got_actor is actor and on_submit is after


async def test_best_of_one_keeps_the_single_game_dialog(monkeypatch):
    async def best_of_for(source, key):
        return 1

    class Refuse:
        def __init__(self, *a, **k):
            raise AssertionError('a best-of-1 matchup should not open the set dialog')

    class Stop(Exception):
        pass

    class NoClient:
        @property
        def client(self):
            raise Stop

    monkeypatch.setattr(module.bracket_sources, 'best_of_for', best_of_for)
    monkeypatch.setattr(module, 'BracketSourceScheduleDialog', Refuse)
    monkeypatch.setattr(module, 'context', NoClient())
    try:
        await module.ChallongeScheduleDialog(_matchup(), object(), opponent_name='mm2').open()
    except Stop:
        pass
    else:
        raise AssertionError('expected the single-game dialog to start building')
