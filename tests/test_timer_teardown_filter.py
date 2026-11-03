"""A `ui.timer` outliving its page is not an error anyone can act on.

`Timer._run_in_loop` sleeps for its interval, waits for the client, and only then
enters `self.parent_slot` — while its own `_should_stop` (which does check
`is_deleted`) is evaluated *inside* that context. Navigating away from any board
carrying the 5s roll-label tick lands in that gap, so the timer raises instead of
stopping quietly.

It was logged as an unhandled UI exception, which buried real errors in Sentry
and made `scripts/ui_flag_sweep.sh` report a failure for merely visiting pages —
costing a full stash-and-rerun against `main` to establish it was pre-existing.

The filter has to stay narrow: an application `RuntimeError` that happens to
reach a deleted slot is a real bug and must still be reported. That holds on every
path the race reaches: the app's exception handler, NiceGUI's own `log.exception`
on the `nicegui` logger, and Sentry's `before_send`.
"""

import logging

import pytest
from nicegui import ui

from application.utils.sentry import _before_send
from application.utils.timer_teardown import (
    TimerTeardownLogFilter,
    install_log_filter,
    is_timer_teardown_race,
)
from theme.timer_teardown import PageTimer

MESSAGE = 'The parent slot of the element has been deleted.'


def _raised_in(module_name: str, exc: Exception) -> Exception:
    """Give ``exc`` a traceback whose frame claims to belong to ``module_name``.

    `walk_tb` reads `frame.f_globals['__name__']`, so a function executing with a
    doctored `__name__` global produces a frame indistinguishable from one raised
    inside that module.
    """
    source = compile('raise exc', f'<{module_name}>', 'exec')
    namespace = {'__name__': module_name, 'exc': exc}
    try:
        exec(source, namespace)
    except Exception as raised:
        # Broad on purpose: the caller chooses the exception.
        return raised
    raise AssertionError('exc was not raised')


class TestItFiltersTheRace:
    def test_a_timer_teardown_is_filtered(self):
        exc = _raised_in('nicegui.timer', RuntimeError(MESSAGE))
        assert is_timer_teardown_race(exc)

    def test_the_elements_timer_submodule_counts_too(self):
        # nicegui.elements.timer subclasses nicegui.timer; the raising frame can belong to either.
        exc = _raised_in('nicegui.timer.something', RuntimeError(MESSAGE))
        assert is_timer_teardown_race(exc)


class TestItFiltersNothingElse:
    def test_the_same_error_from_application_code_is_reported(self):
        exc = _raised_in('pages.home_tabs.event', RuntimeError(MESSAGE))
        assert not is_timer_teardown_race(exc)

    def test_a_different_runtime_error_from_a_timer_is_reported(self):
        exc = _raised_in('nicegui.timer', RuntimeError('database is gone'))
        assert not is_timer_teardown_race(exc)

    @pytest.mark.parametrize('exc_type', [ValueError, KeyError, AttributeError, TypeError])
    def test_other_exception_types_are_reported(self, exc_type):
        exc = _raised_in('nicegui.timer', exc_type(MESSAGE))
        assert not is_timer_teardown_race(exc)

    def test_an_exception_with_no_traceback_is_reported(self):
        assert not is_timer_teardown_race(RuntimeError(MESSAGE))


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def nicegui_log():
    """The ``nicegui`` logger with the filter installed and a handler that keeps
    whatever gets past it. NiceGUI's default exception handler is ``log.exception``
    on exactly this logger."""
    logger = logging.getLogger('nicegui')
    filters_before = list(logger.filters)
    handler = _Collect()
    logger.addHandler(handler)
    install_log_filter()
    try:
        yield logger, handler.records
    finally:
        logger.removeHandler(handler)
        logger.filters[:] = filters_before


def _log_like_nicegui(logger: logging.Logger, exc: Exception) -> None:
    # Same shape as NiceGUI's background-task handler: the exception is both message and exc_info.
    try:
        raise exc
    except Exception as e:
        logger.exception(e)


class TestTheNiceguiLoggerPath:
    def test_the_race_is_not_logged(self, nicegui_log):
        logger, records = nicegui_log
        _log_like_nicegui(logger, _raised_in('nicegui.timer', RuntimeError(MESSAGE)))
        assert records == []

    def test_the_same_error_from_application_code_is_logged(self, nicegui_log):
        logger, records = nicegui_log
        _log_like_nicegui(logger, _raised_in('pages.home_tabs.event', RuntimeError(MESSAGE)))
        assert len(records) == 1
        assert MESSAGE in records[0].getMessage()

    def test_a_record_without_an_exception_is_logged(self, nicegui_log):
        logger, records = nicegui_log
        logger.error(MESSAGE)
        assert len(records) == 1

    def test_the_exception_as_the_message_alone_is_enough(self):
        exc = _raised_in('nicegui.timer', RuntimeError(MESSAGE))
        record = logging.LogRecord('nicegui', logging.ERROR, __file__, 1, exc, None, None)
        assert not TimerTeardownLogFilter().filter(record)

    def test_installing_twice_adds_one_filter(self, nicegui_log):
        logger, _ = nicegui_log
        install_log_filter()
        assert sum(isinstance(f, TimerTeardownLogFilter) for f in logger.filters) == 1


class TestTheSentryPath:
    def test_the_race_is_dropped(self):
        exc = _raised_in('nicegui.timer', RuntimeError(MESSAGE))
        hint = {'exc_info': (type(exc), exc, exc.__traceback__)}
        assert _before_send({'level': 'error'}, hint) is None

    def test_the_same_error_from_application_code_is_sent(self):
        exc = _raised_in('pages.home_tabs.event', RuntimeError(MESSAGE))
        hint = {'exc_info': (type(exc), exc, exc.__traceback__)}
        sent = _before_send({'level': 'error'}, hint)
        assert sent is not None and sent['level'] == 'error'

    def test_other_events_are_still_scrubbed(self):
        event = {'request': {'headers': {'Authorization': 'Bearer x'}, 'cookies': {'a': 'b'}}}
        sent = _before_send(event, None)
        assert sent['request'] == {'headers': {'Authorization': '[Filtered]'}}


class TestPageTimerDoesNotStartOnADeletedPage:
    """The race itself: a page closed while its timer waits for the client."""

    @pytest.fixture(autouse=True)
    def _client_connects(self, monkeypatch):
        async def connected(self):
            return True
        monkeypatch.setattr(ui.timer, '_can_start', connected)

    async def test_a_deleted_timer_does_not_start(self):
        timer = PageTimer.__new__(PageTimer)
        timer._deleted = True
        assert not await timer._can_start()

    async def test_a_live_timer_starts(self):
        timer = PageTimer.__new__(PageTimer)
        timer._deleted = False
        assert await timer._can_start()

    def test_the_nicegui_hooks_it_relies_on_still_exist(self):
        # PageTimer overrides a private hook; a NiceGUI upgrade that renames it silently brings the race back.
        import inspect

        from nicegui.timer import Timer

        assert 'self._can_start()' in inspect.getsource(Timer._run_in_loop)
        assert 'self._can_start()' in inspect.getsource(Timer._run_once)
