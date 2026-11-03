"""The live Watch page rebuilds only the sections whose content changed."""

from decimal import Decimal

from theme.watch_live import LiveWatch
from theme.watch_view import WatchDay, WatchMatch, WatchStage, WatchTournament, WatchView


class _Box:
    def __init__(self, name, log):
        self.name, self.log = name, log

    def clear(self):
        self.log.append(self.name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Theater:
    def is_watching(self, stage_id):
        return False


def _live(log):
    live = LiveWatch.__new__(LiveWatch)
    live._buttons, live._shown, live.theater = {}, {}, _Theater()
    live._stages_box = _Box('stages', log)
    live._coming_box = _Box('coming', log)
    live._tournaments_box = _Box('tournaments', log)
    live._stages = live._coming = live._tournaments = lambda view: None
    return live


def _view(pool='10'):
    return WatchView(
        community='C',
        stages=[WatchStage(id=1, name='Main', on_now=WatchMatch(time='1:00 PM', players='A vs B'))],
        coming=[WatchDay(label='Today', matches=[WatchMatch(time='2:00 PM', players='C vs D')])],
        tournaments=[WatchTournament(id=1, name='ALTTPR', prize_total=Decimal(pool))],
    )


def test_first_render_builds_every_section():
    log = []
    _live(log).render(_view())
    assert log == ['stages', 'coming', 'tournaments']


def test_an_unchanged_view_rebuilds_nothing():
    log = []
    live = _live(log)
    live.render(_view())
    log.clear()
    live.render(_view())
    assert log == []


def test_only_the_changed_section_is_rebuilt():
    log = []
    live = _live(log)
    live.render(_view())
    log.clear()
    live.render(_view(pool='20'))
    assert log == ['tournaments']
