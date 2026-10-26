"""A slow button says what it's doing and can't be pressed twice (theme/busy.py)."""

import asyncio

import pytest
from nicegui import events, ui
from nicegui.client import Client
from nicegui.events import ClickEventArguments
from nicegui.page import page

from theme import busy as busy_module
from theme.busy import busy, busy_click, current_busy, is_busy
from theme.dialog.confirmation_dialog import ConfirmationDialog


@pytest.fixture
def slot():
    """A client to build into, entered inside each async test (a sync
    fixture's ``with`` doesn't reach the test's task)."""
    return Client(page('/'))


def _click(button: ui.button) -> ClickEventArguments:
    return ClickEventArguments(sender=button, client=button.client)




async def test_button_says_what_it_is_doing_and_recovers(slot):
    with slot:
        button = ui.button('Sync', icon='sync')
        async with busy(button, 'Syncing…'):
            assert button.text == 'Syncing…'
            assert not button.enabled
            assert 'icon' not in button.props
            assert 'wiz-busy-btn' in button.classes
            assert any(isinstance(e, ui.spinner) for e in button.descendants())
            assert is_busy(button)
        assert button.text == 'Sync'
        assert button.enabled
        assert button.props['icon'] == 'sync'
        assert 'wiz-busy-btn' not in button.classes
        assert not any(isinstance(e, ui.spinner) for e in button.descendants())
        assert not is_busy(button)


async def test_button_recovers_when_the_work_fails(slot):
    with slot:
        button = ui.button('Sync')
        with pytest.raises(RuntimeError):
            async with busy(button, 'Syncing…'):
                raise RuntimeError('boom')
        assert button.text == 'Sync'
        assert button.enabled
        assert not is_busy(button)


async def test_progress_rewrites_the_label(slot):
    with slot:
        button = ui.button('Roll')
        async with busy(button, 'Rolling…'):
            current_busy().progress('Rolling', 7, 25)
            assert button.text == 'Rolling 7 of 25…'


async def test_second_press_is_dropped_while_busy(slot):
    with slot:
        button = ui.button('Roll')
        release = asyncio.Event()
        calls: list[int] = []

        async def work() -> None:
            calls.append(1)
            await release.wait()

        handler = busy_click('Rolling…', work)

        async def press_again() -> None:
            await asyncio.sleep(0)
            await handler(_click(button))
            release.set()

        await asyncio.gather(handler(_click(button)), press_again())
        assert calls == [1]
        assert not is_busy(button)


async def test_a_menu_item_gets_a_spinner_toast(slot, monkeypatch):
    with slot:
        shown: list[dict] = []

        class _Toast:
            def __init__(self, message, **kwargs):
                self.message = message
                shown.append({'message': message, **kwargs})
                self.dismissed = False

            def dismiss(self):
                self.dismissed = True

        monkeypatch.setattr(busy_module.ui, 'notification', _Toast)
        async with busy(None, 'Syncing brackets…') as state:
            state.say('Syncing ALTTPR…')
            assert state._toast.message == 'Syncing ALTTPR…'
        assert shown[0]['spinner'] is True and shown[0]['timeout'] is None
        assert state._toast.dismissed


async def test_slow_confirm_keeps_the_dialog_open_until_done(slot, monkeypatch):
    # handle_event defers a coroutine until the app has started; run it here.
    scheduled: list = []
    monkeypatch.setattr(events.background_tasks, 'create_or_defer',
                        lambda coro, **_kw: scheduled.append(coro))
    with slot:
        release = asyncio.Event()
        seen: list[bool] = []
        dialog = ConfirmationDialog(message='Publish?', busy_text='Publishing…')

        async def publish() -> None:
            seen.append(dialog.dialog.value)
            await release.wait()

        dialog.on_confirm = publish
        dialog.open()
        confirm = [e for e in dialog.dialog.descendants() if isinstance(e, ui.button)][-1]
        handler = next(listener.handler for listener in confirm._event_listeners.values()
                       if listener.type == 'click')
        handler(_click(confirm))

        async def check_then_release() -> None:
            await asyncio.sleep(0)
            assert seen == [True]
            assert is_busy(confirm)
            release.set()

        await asyncio.gather(scheduled[0], check_then_release())
        assert dialog.dialog.value is False
        assert not is_busy(confirm)


async def test_a_button_disabled_beforehand_stays_disabled(slot):
    with slot:
        button = ui.button('Send')
        button.disable()
        async with busy(button, 'Sending…'):
            pass
        assert not button.enabled
