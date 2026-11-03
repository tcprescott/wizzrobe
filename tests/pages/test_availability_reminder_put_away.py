"""Putting the "When you can't play" card away still reaches the editor (SGLMAN-1Z).

The card deletes itself when a button is pressed. Its ``on_block_out`` callback
used to run after that, from the deleted card's slot, so My Schedule's
``ui.run_javascript`` scroll raised "The parent element this slot belongs to has
been deleted."
"""

import pytest
from nicegui import context, events, ui
from nicegui.client import Client
from nicegui.events import ClickEventArguments
from nicegui.page import page

from pages.home_tabs import availability_reminder


class _Service:
    async def needs_reminder(self, user) -> bool:
        return True

    async def dismiss_reminder(self, user) -> None:
        pass


@pytest.fixture
def slot():
    return Client(page('/'))


def _press(button: ui.button) -> list:
    handler = next(listener.handler for listener in button._event_listeners.values()
                   if listener.type == 'click')
    return handler(ClickEventArguments(sender=button, client=button.client))


@pytest.mark.parametrize('label', ['Block out times', 'Got it'])
async def test_pressing_a_button_puts_the_card_away_without_raising(slot, monkeypatch, label):
    scheduled: list = []

    async def in_slot(coro, context):
        with context:
            await coro

    # Run the handler inside the button's slot, as the app does.
    monkeypatch.setattr(events.background_tasks, 'create_or_defer',
                        lambda coro, *, context, **_kw: scheduled.append(in_slot(coro, context)))
    monkeypatch.setattr(availability_reminder, 'PlayerAvailabilityService', _Service)
    opened: list[bool] = []

    async def on_block_out() -> None:
        opened.append(context.client is slot)

    with slot:
        await availability_reminder.render_availability_reminder(object(), on_block_out)
        button = next(e for e in slot.layout.descendants()
                      if isinstance(e, ui.button) and e.text == label)
        _press(button)
        for task in scheduled:
            await task
        assert not any(isinstance(e, ui.button) for e in slot.layout.descendants())
    assert opened == ([True] if label == 'Block out times' else [])
