"""Survivors of a full collection are frozen, and handed back once an hour."""

import asyncio
import gc

import pytest

from application.utils import gc_monitor


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(gc_monitor.gc, 'freeze', lambda: seen.append('freeze'))
    monkeypatch.setattr(gc_monitor.gc, 'unfreeze', lambda: seen.append('unfreeze'))
    return seen


def test_freezes_after_a_full_pass_within_the_hour(calls, monkeypatch):
    monkeypatch.setattr(gc_monitor, '_last_thaw', gc_monitor.time.monotonic())
    gc_monitor._after_full_pass()
    assert calls == ['freeze']


def test_thaws_once_the_hour_is_up(calls, monkeypatch):
    monkeypatch.setattr(gc_monitor, '_last_thaw', gc_monitor.time.monotonic() - gc_monitor.THAW_SECONDS - 1)
    gc_monitor._after_full_pass()
    gc_monitor._after_full_pass()
    assert calls == ['unfreeze', 'freeze']


class _Loop:
    def __init__(self, closed=False):
        self.scheduled = []
        self.closed = closed

    def call_soon_threadsafe(self, fn):
        if self.closed:
            raise RuntimeError('Event loop is closed')
        self.scheduled.append(fn)


@pytest.mark.parametrize('phase,generation,expected', [
    ('stop', 2, 1), ('start', 2, 0), ('stop', 1, 0), ('stop', 0, 0),
])
def test_only_the_end_of_a_full_pass_schedules(monkeypatch, phase, generation, expected):
    loop = _Loop()
    monkeypatch.setattr(gc_monitor, '_refreeze_loop', loop)
    gc_monitor._refreeze_callback(phase, {'generation': generation})
    assert len(loop.scheduled) == expected


def test_a_closed_loop_is_ignored(monkeypatch):
    monkeypatch.setattr(gc_monitor, '_refreeze_loop', _Loop(closed=True))
    gc_monitor._refreeze_callback('stop', {'generation': 2})


def test_nothing_scheduled_when_not_started(monkeypatch):
    monkeypatch.setattr(gc_monitor, '_refreeze_loop', None)
    gc_monitor._refreeze_callback('stop', {'generation': 2})


async def test_a_full_pass_freezes_its_survivors():
    gc_monitor.refreeze_after_full_passes(asyncio.get_running_loop())
    gc_monitor.refreeze_after_full_passes(asyncio.get_running_loop())
    try:
        assert gc.callbacks.count(gc_monitor._refreeze_callback) == 1
        before = gc_monitor._refreezes
        gc.collect()
        await asyncio.sleep(0)
        assert gc_monitor._refreezes == before + 1
        assert gc.get_freeze_count() > 0
    finally:
        gc_monitor.stop_refreeze()
        gc.unfreeze()
    assert gc_monitor._refreeze_callback not in gc.callbacks
