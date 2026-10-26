"""The request-time event context: binding, scoping and the loud failure."""

import pytest

from application.event_context import (
    event_scope,
    get_current_event_id,
    require_event_id,
    reset_event_id,
    set_event_id,
)


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


def test_no_tenant_means_no_event():
    from application.tenant_context import tenant_scope

    with tenant_scope(None), event_scope(5):
        assert get_current_event_id() is None
