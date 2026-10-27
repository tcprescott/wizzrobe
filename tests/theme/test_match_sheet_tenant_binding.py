"""The match sheet's controls must run their callbacks with the board's tenant.

Sentry SGLMAN-V: changing a match's Stage from the sheet on Admin > Schedule
raised "No tenant in context". The callback read the tenant fine on its first
line, saved the stage, then failed on its closing row refresh. The write pushes
``match_live``, the board repaints the sheet, and the select that fired the
event is deleted mid-await. NiceGUI runs an awaited handler inside the sender's
parent slot, which holds its element only by weakref, so the client stash the
tenant (and display zone) fall back to goes with it.
"""

import asyncio
import gc
import inspect
import re
from types import SimpleNamespace

import pytest
from nicegui import core
from nicegui.slot import Slot

from application.tenant_context import get_current_tenant_id, require_tenant_id, tenant_scope
from theme.dialog.match_sheet import MatchSheet
from theme.tables.admin_crud import scoped_background


@pytest.fixture(autouse=True)
def own_slot_stacks(monkeypatch):
    """Start from empty slot stacks.

    A sync test shares NiceGUI's task-id-0 stack with every other sync test in
    the worker, so a slot another test left behind became the fallback the
    dead-slot walk found, and the stash read back that test's tenant.
    """
    monkeypatch.setattr(Slot, 'stacks', {})


class _Row:
    """Stands in for the sheet's row element: a slot parent with a client."""

    def __init__(self, tenant_id: int):
        self.client = SimpleNamespace(storage={'tenant_id': tenant_id})


def test_a_deleted_slot_parent_takes_the_client_stash_with_it():
    """The mechanism behind SGLMAN-V, with no tenant contextvar set."""
    row = _Row(7)
    with tenant_scope(None), Slot(row, 'default'):
        assert get_current_tenant_id() == 7
        del row
        gc.collect()
        assert get_current_tenant_id() is None
        with pytest.raises(RuntimeError, match='No tenant in context'):
            require_tenant_id()


@pytest.mark.asyncio
async def test_a_sheet_callback_keeps_the_tenant_after_its_control_is_deleted(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    render_context = (7, 'UTC', None, False, None)
    sheet = MatchSheet.__new__(MatchSheet)
    sheet.view = SimpleNamespace(_bg=lambda coro: scoped_background(render_context, coro))

    repainted = asyncio.Event()
    seen: dict = {}

    async def on_set_stage(match_id, stage):
        seen['before'] = require_tenant_id()
        await repainted.wait()
        seen['after'] = require_tenant_id()

    row = _Row(7)
    with tenant_scope(None), Slot(row, 'default'):
        sheet._run(on_set_stage, 1, None)
    await asyncio.sleep(0)
    del row
    gc.collect()
    repainted.set()
    for _ in range(5):
        await asyncio.sleep(0)

    assert seen == {'before': 7, 'after': 7}


def test_every_sheet_control_reaches_its_callback_through_run():
    """A new button wired straight to a handler would bring the bug back."""
    src = inspect.getsource(MatchSheet)
    direct = re.findall(r'(?<!_run\()\b(?:h|self\.handlers)\.on_\w+\(', src)
    assert not direct, (
        f'match sheet calls lifecycle callbacks directly: {direct}. Wrap them in '
        f'self._run(handler, *args) so they run with the board tenant and zone.'
    )
