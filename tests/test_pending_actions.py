"""Held actions: committed after the window, or never if undone."""

import asyncio

import pytest

from application import pending_actions


@pytest.fixture(autouse=True)
def _clean():
    yield
    for token in list(pending_actions._held):
        pending_actions.cancel(token)


def _recorder():
    calls = []

    async def commit():
        calls.append('committed')
    return calls, commit


async def test_commits_after_the_window():
    calls, commit = _recorder()
    pending_actions.hold(('t', 1), 'A wins', commit, seconds=0.01)
    assert pending_actions.pending_label(('t', 1)) == 'A wins'
    await asyncio.sleep(0.05)
    assert calls == ['committed']
    assert pending_actions.pending_label(('t', 1)) is None


async def test_cancel_means_it_never_runs():
    calls, commit = _recorder()
    token = pending_actions.hold(('t', 2), 'A wins', commit, seconds=0.01)
    assert pending_actions.cancel(token) is True
    await asyncio.sleep(0.05)
    assert calls == []
    assert pending_actions.cancel(token) is False


async def test_a_second_hold_on_the_same_thing_is_refused():
    _calls, commit = _recorder()
    pending_actions.hold(('t', 3), 'A wins', commit, seconds=5)
    with pytest.raises(ValueError):
        pending_actions.hold(('t', 3), 'B wins', commit, seconds=5)


async def test_commit_now_skips_the_wait_and_runs_once():
    calls, commit = _recorder()
    token = pending_actions.hold(('t', 4), 'A wins', commit, seconds=5)
    assert await pending_actions.commit_now(token) is True
    assert await pending_actions.commit_now(token) is False
    await asyncio.sleep(0.01)
    assert calls == ['committed']


async def test_flush_all_commits_everything_held():
    calls, commit = _recorder()
    pending_actions.hold(('t', 5), 'A', commit, seconds=5)
    pending_actions.hold(('t', 6), 'B', commit, seconds=5)
    await pending_actions.flush_all()
    assert calls == ['committed', 'committed']


async def test_a_refused_commit_reaches_on_error():
    errors = []

    async def commit():
        raise ValueError('nope')

    async def on_error(e):
        errors.append(str(e))

    pending_actions.hold(('t', 7), 'A', commit, on_error=on_error, seconds=0.01)
    await asyncio.sleep(0.05)
    assert errors == ['nope']


async def test_the_commit_runs_in_the_holders_context():
    import contextvars
    var = contextvars.ContextVar('who', default=None)
    seen = []

    async def commit():
        seen.append(var.get())

    var.set('tenant-7')
    pending_actions.hold(('t', 8), 'A', commit, seconds=0.01)
    var.set(None)
    await asyncio.sleep(0.05)
    assert seen == ['tenant-7']
