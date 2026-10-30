"""Unit tests for the tenant context primitive (Phase 0)."""

import asyncio

import pytest

from application import tenant_context
from application.tenant_context import (
    get_current_tenant_id,
    require_tenant_id,
    reset_tenant_id,
    set_tenant_id,
    tenant_scope,
)


@pytest.fixture(autouse=True)
def _clear_context():
    """Guarantee a clean contextvar around every test in this module."""
    token = set_tenant_id(None)
    try:
        yield
    finally:
        try:
            reset_tenant_id(token)
        except Exception:
            pass


def test_default_is_none():
    assert get_current_tenant_id() is None


def test_set_get_reset():
    token = set_tenant_id(7)
    assert get_current_tenant_id() == 7
    reset_tenant_id(token)
    assert get_current_tenant_id() is None


def test_require_returns_value_when_set():
    with tenant_scope(42):
        assert require_tenant_id() == 42


def test_require_raises_when_absent():
    with pytest.raises(RuntimeError):
        require_tenant_id()


def test_tenant_scope_restores_previous():
    with tenant_scope(1):
        assert get_current_tenant_id() == 1
        with tenant_scope(2):
            assert get_current_tenant_id() == 2
        assert get_current_tenant_id() == 1
    assert get_current_tenant_id() is None


def test_tenant_scope_resets_on_exception():
    with pytest.raises(ValueError):
        with tenant_scope(5):
            raise ValueError('boom')
    assert get_current_tenant_id() is None


def test_client_stash_fallback_used_when_contextvar_none(monkeypatch):
    """With no contextvar set, resolution falls through to the client stash."""
    monkeypatch.setattr(tenant_context, '_client_stash_tenant_id', lambda: 99)
    assert get_current_tenant_id() == 99
    assert require_tenant_id() == 99


def test_contextvar_wins_over_client_stash(monkeypatch):
    monkeypatch.setattr(tenant_context, '_client_stash_tenant_id', lambda: 99)
    with tenant_scope(3):
        assert get_current_tenant_id() == 3


def test_client_stash_helpers_are_safe_without_nicegui_context():
    # No active NiceGUI client here — both directions must no-op, not raise.
    assert tenant_context._client_stash_tenant_id() is None
    tenant_context.stash_client_tenant_id(1)


async def test_scope_propagates_across_await():
    async def read_after_await() -> int:
        await asyncio.sleep(0)
        return require_tenant_id()

    with tenant_scope(11):
        assert await read_after_await() == 11


async def test_scopes_isolated_between_concurrent_tasks():
    """Each task's tenant_scope is isolated — no bleed across the event loop."""
    seen: dict[int, int] = {}

    async def worker(tid: int) -> None:
        with tenant_scope(tid):
            await asyncio.sleep(0)
            seen[tid] = require_tenant_id()

    await asyncio.gather(worker(100), worker(200), worker(300))
    assert seen == {100: 100, 200: 200, 300: 300}


class _FakeSlot:
    def __init__(self, storage=None):
        self._storage = storage

    def _parent(self):
        return self.parent if self._storage is not None else None

    @property
    def parent(self):
        if self._storage is None:
            raise RuntimeError('The parent element this slot belongs to has been deleted.')
        client = type('C', (), {'storage': self._storage})()
        return type('E', (), {'client': client})()


def test_client_stash_skips_a_deleted_slot(monkeypatch):
    """A handler whose button was refreshed away still finds its client (SGLMAN-10)."""
    from nicegui.slot import Slot

    stack = [_FakeSlot({'tenant_id': 5}), _FakeSlot(None)]
    monkeypatch.setattr(Slot, 'get_stack', classmethod(lambda cls: stack))
    assert tenant_context._client_stash_tenant_id() == 5
    assert require_tenant_id() == 5


async def test_pin_client_context_carries_tenant_into_gather(monkeypatch):
    """Child tasks can't see the client stash, so the pin must carry it (SGLMAN-Z)."""
    from application import timezone_context
    from application.timezone_context import get_current_timezone_name, pin_client_context

    parent = asyncio.current_task()
    monkeypatch.setattr(
        tenant_context, '_client_stash_tenant_id',
        lambda: 7 if asyncio.current_task() is parent else None,
    )
    monkeypatch.setattr(
        timezone_context, '_client_stash_timezone',
        lambda: 'Europe/London' if asyncio.current_task() is parent else None,
    )

    async def read():
        return get_current_tenant_id(), get_current_timezone_name()

    assert await asyncio.gather(read()) == [(None, None)]
    with pin_client_context():
        assert await asyncio.gather(read(), read()) == [(7, 'Europe/London')] * 2
    assert get_current_tenant_id() == 7
    assert tenant_context._tenant_id_var.get() is None
