"""An inbox action still confirms after the inbox redraws under it (SGLMAN-2B).

Approving a reschedule awaits the clash check and the service, and meanwhile a
live update can rebuild the inbox and delete the Approve button. The handler runs
in that button's slot, so its "Match moved" toast raised "The parent element this
slot belongs to has been deleted." even though the move went through.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from nicegui import context, events, ui
from nicegui.client import Client
from nicegui.events import ClickEventArguments
from nicegui.page import page

from models import RescheduleRequestKind
from theme import match_inbox


@pytest.fixture
def slot():
    return Client(page('/'))


def _press(button: ui.button):
    handler = next(listener.handler for listener in button._event_listeners.values()
                   if listener.type == 'click')
    return handler(ClickEventArguments(sender=button, client=button.client))


async def test_approve_toast_survives_the_inbox_redrawing(slot, monkeypatch):
    scheduled: list = []

    async def in_slot(coro, context):
        with context:
            await coro

    monkeypatch.setattr(events.background_tasks, 'create_or_defer',
                        lambda coro, *, context, **_kw: scheduled.append(in_slot(coro, context)))
    monkeypatch.setattr(match_inbox, '_when', lambda dt: 'later')

    async def current_actor():
        return object()

    async def confirm_time(**_kw):
        return True

    approved: list[int] = []

    class _Service:
        async def approve(self, request_id, actor):
            approved.append(request_id)
            holder.clear()

    monkeypatch.setattr(match_inbox, 'current_actor', current_actor)
    monkeypatch.setattr(match_inbox, 'confirm_time', confirm_time)
    monkeypatch.setattr(match_inbox, 'MatchRescheduleService', _Service)
    toasts: list[str] = []
    monkeypatch.setattr(match_inbox.ui, 'notify', lambda message, **_kw: (
        context.client, toasts.append(message)))

    player = SimpleNamespace(user_id=1, user=SimpleNamespace(preferred_name='Ana'))
    request = SimpleNamespace(
        id=7, match_id=3, kind=RescheduleRequestKind.RESCHEDULE,
        proposed_at=datetime(2026, 10, 23, tzinfo=timezone.utc), opponent_agreed_at=None,
        requested_by=SimpleNamespace(preferred_name='Ana'),
        match=SimpleNamespace(players=[player], tournament_id=2, title='',
                              tournament=SimpleNamespace(name='ALTTPR'), scheduled_at=None),
    )
    inbox = match_inbox.MatchInbox.__new__(match_inbox.MatchInbox)
    inbox.view = None
    inbox.refresh = lambda: None

    with slot:
        inbox._client = slot
        with ui.column() as holder:
            inbox._request_item(request)
        button = next(e for e in holder.descendants()
                      if isinstance(e, ui.button) and e.text == 'Approve')
        _press(button)
        for task in scheduled:
            await task
    assert approved == [7]
    assert toasts == ['Match moved and everyone told.']
