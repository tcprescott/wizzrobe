"""The request-time event context: binding, scoping and the loud failure."""

import pytest

from application.event_context import (
    event_scope,
    get_current_event_id,
    require_event_id,
    reset_event_id,
    set_event_id,
)


@pytest.fixture(autouse=True)
def _no_ambient_event():
    """Start each test with no event bound, unlike the suite-wide default."""
    with event_scope(None):
        yield


def test_no_event_in_scope_resolves_to_none():
    assert get_current_event_id() is None


def test_require_raises_without_an_event():
    with pytest.raises(RuntimeError, match='No event in context'):
        require_event_id()


def test_event_scope_binds_and_restores():
    with event_scope(7):
        assert require_event_id() == 7
        with event_scope(9):
            assert require_event_id() == 9
        assert require_event_id() == 7
    assert get_current_event_id() is None


def test_set_and_reset_round_trip():
    token = set_event_id(3)
    try:
        assert get_current_event_id() == 3
    finally:
        reset_event_id(token)
    assert get_current_event_id() is None


async def test_scope_survives_awaits():
    async def inner() -> int:
        return require_event_id()

    with event_scope(11):
        assert await inner() == 11


def test_binding_belongs_to_its_tenant():
    from application.tenant_context import tenant_scope

    with tenant_scope(1), event_scope(5):
        assert get_current_event_id() == 5
        with tenant_scope(2):
            assert get_current_event_id() is None
            with event_scope(8):
                assert get_current_event_id() == 8
        assert get_current_event_id() == 5


def test_explicit_tenant_binding():
    from application.tenant_context import tenant_scope

    with tenant_scope(1), event_scope(5, tenant_id=2):
        assert get_current_event_id() is None
        with tenant_scope(2):
            assert get_current_event_id() == 5


def test_no_tenant_means_no_event(monkeypatch):
    from application import tenant_context
    from application.tenant_context import tenant_scope

    # Otherwise a NiceGUI client left current by an earlier test supplies its tenant.
    monkeypatch.setattr(tenant_context, '_client_stash_tenant_id', lambda: None)
    with tenant_scope(None), event_scope(5):
        assert get_current_event_id() is None


async def test_pin_client_context_carries_event_into_gather(monkeypatch):
    """Child tasks can't see the client stash, so the pin must carry the event (SGLMAN-2T)."""
    import asyncio

    from application import event_context
    from application.tenant_context import tenant_scope
    from application.timezone_context import pin_client_context

    parent = asyncio.current_task()
    monkeypatch.setattr(event_context, '_fallback_event_id', lambda tenant_id: None)
    monkeypatch.setattr(
        event_context, '_client_stash_event_id',
        lambda tenant_id: 4 if asyncio.current_task() is parent else None,
    )

    async def read():
        return get_current_event_id()

    with tenant_scope(1):
        assert get_current_event_id() == 4
        assert await asyncio.gather(read()) == [None]
        with pin_client_context():
            assert await asyncio.gather(read(), read()) == [4, 4]


async def test_deferred_tab_build_binds_the_event_for_its_background_tasks(monkeypatch):
    """A tab built later spawns tasks with no client stash, so the build binds the event (SGLMAN-2X)."""
    import asyncio
    from contextlib import nullcontext

    from application import event_context
    from theme import base

    monkeypatch.setattr(event_context, '_fallback_event_id', lambda tenant_id: None)
    monkeypatch.setattr(base, 'heading_fallback', lambda label: nullcontext())

    tasks: list = []

    async def load():
        return get_current_event_id()

    async def content():
        # Stands in for background_tasks.create, which needs a running NiceGUI loop; both copy the context.
        tasks.append(asyncio.ensure_future(load()))

    layout = object.__new__(base.BaseLayout)
    layout._pending_tabs = {'Stages': {'label': 'Stages', 'content': content, 'half': 'community'}}
    layout._tab_containers = {'Stages': nullcontext()}
    layout._tenant_id = 1
    layout._event_id = 6
    layout._table_prefs = {}
    layout.events = None
    layout.user = None

    assert await layout._build_tab('Stages', deferred=False) is True
    assert await asyncio.wait_for(tasks[0], 2) == 6
