"""``shared_lock`` on SQLite: the in-process half that every backend keeps."""

import asyncio

import pytest

from application.utils.shared_lock import LockBusy, advisory_key, shared_lock


async def test_try_lock_refuses_a_key_held_in_this_process(db):
    async with shared_lock('match.seed_roll', 1):
        with pytest.raises(LockBusy):
            async with shared_lock('match.seed_roll', 1, wait=False):
                pass
        async with shared_lock('match.seed_roll', 2, wait=False):
            pass
        async with shared_lock('race_room.open', 1, wait=False):
            pass


async def test_waiting_holders_take_turns(db):
    order = []

    async def hold(tag):
        async with shared_lock('check_in.sync', 9):
            order.append(f'{tag}-in')
            await asyncio.sleep(0.01)
            order.append(f'{tag}-out')

    await asyncio.gather(hold('a'), hold('b'))
    assert order in (['a-in', 'a-out', 'b-in', 'b-out'], ['b-in', 'b-out', 'a-in', 'a-out'])


def test_advisory_keys_fit_postgres_int4_and_differ_by_name():
    for name, key in [('match.seed_roll', 2**40), ('race_room.open', 'match:12'), ('x', -5)]:
        classid, objid = advisory_key(name, key)
        assert -2**31 <= classid < 2**31 and -2**31 <= objid < 2**31
    assert advisory_key('a', 1) != advisory_key('b', 1)
    assert advisory_key('a', 1) == advisory_key('a', 1)
