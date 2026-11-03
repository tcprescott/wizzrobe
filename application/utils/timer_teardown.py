"""Recognising a ``ui.timer`` that outlived the page which created it.

``Timer._run_in_loop`` waits for the client to connect and only *then* enters
``self.parent_slot`` — while its own ``_should_stop`` (which does check
``is_deleted``) is evaluated **inside** that context. A page closed during that
wait leaves a dead slot behind, so the timer raises out of the ``with`` instead
of stopping quietly. Every board carrying the 5s roll-label tick
(``theme/tables/match.py``) that is closed before its socket settles lands in it.

Nobody can act on the result: the page is gone, there is no UI left to notify,
and the timer stops either way. Reported, it buried real errors in Sentry and
made ``scripts/ui_flag_sweep.sh`` fail for merely visiting pages — which cost a
full stash-and-rerun against ``main`` to establish it was pre-existing rather
than a regression.

``theme.timer_teardown.PageTimer`` closes the gap for our own timers. This
predicate is the backstop for any timer that doesn't use it, on every path the
race can surface: the app's ``on_exception`` handler (``frontend.py``); NiceGUI's
own default handler, which is ``log.exception`` on the ``nicegui`` logger and
runs alongside ours (``TimerTeardownLogFilter`` — Sentry's logging hook sits
behind logger filters, so this also keeps it out of Sentry); and Sentry's
``before_send`` (``application/utils/sentry.py``) for any other integration.

Pure stdlib, so the Sentry setup can use it without importing the UI layer.
"""

import logging
import traceback

# Matched by message: NiceGUI raises a bare RuntimeError.
_DELETED_SLOT = 'parent slot of the element has been deleted'

_TIMER_MODULE = 'nicegui.timer'


def is_timer_teardown_race(exc: BaseException) -> bool:
    """Whether ``exc`` is the teardown race described above.

    Deliberately narrow — the exact type, the exact message, **and** a NiceGUI
    timer frame in the traceback. An application ``RuntimeError`` that reaches a
    deleted slot is a real bug (something is writing into a page that is gone)
    and must still be reported.
    """
    if not isinstance(exc, RuntimeError):
        return False
    if _DELETED_SLOT not in str(exc):
        return False
    return any(
        frame.f_globals.get('__name__', '').startswith(_TIMER_MODULE)
        for frame, _ in traceback.walk_tb(exc.__traceback__)
    )


class TimerTeardownLogFilter(logging.Filter):
    """Drops log records whose exception is the teardown race, and nothing else."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if exc is None and isinstance(record.msg, BaseException):
            exc = record.msg
        return exc is None or not is_timer_teardown_race(exc)


def install_log_filter() -> None:
    """Attach the filter to the ``nicegui`` logger once; safe to call repeatedly."""
    nicegui_logger = logging.getLogger('nicegui')
    if not any(isinstance(f, TimerTeardownLogFilter) for f in nicegui_logger.filters):
        nicegui_logger.addFilter(TimerTeardownLogFilter())
