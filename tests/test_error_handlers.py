"""Tests for the themed error-handler plumbing.

The themed pages themselves render through NiceGUI (verified manually with a
browser); these tests cover the framework-independent core: that every unhandled
error is tagged with a unique, log-traceable UUID and that the UUID is surfaced
in a way a feedback report can capture.
"""

import logging
import re
import uuid
from unittest.mock import patch

from middleware.error_handlers import log_unhandled_error

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)


class TestLogUnhandledError:
    def test_returns_a_valid_uuid(self):
        with patch('middleware.error_handlers.sentry_sdk'):
            error_id = log_unhandled_error(RuntimeError('boom'), '/somewhere')
        assert _UUID_RE.match(error_id)
        assert str(uuid.UUID(error_id)) == error_id

    def test_each_call_is_unique(self):
        with patch('middleware.error_handlers.sentry_sdk'):
            first = log_unhandled_error(RuntimeError('a'), '/x')
            second = log_unhandled_error(RuntimeError('b'), '/y')
        assert first != second

    def test_logs_marked_with_uuid_and_path_and_traceback(self, caplog):
        exc = RuntimeError('kaboom')
        with patch('middleware.error_handlers.sentry_sdk'), \
                caplog.at_level(logging.ERROR, logger='middleware.error_handlers'):
            error_id = log_unhandled_error(exc, '/boom')

        record = next(r for r in caplog.records if 'UNHANDLED ERROR' in r.getMessage())
        message = record.getMessage()
        assert f'error_id={error_id}' in message
        assert 'path=/boom' in message
        assert record.exc_info is not None
        assert record.exc_info[1] is exc

    def test_tags_error_id_on_sentry_scope(self):
        with patch('middleware.error_handlers.sentry_sdk') as sentry:
            error_id = log_unhandled_error(RuntimeError('boom'), '/x')
        sentry.set_tag.assert_called_once_with('error_id', error_id)

    def test_tags_error_id_before_logging_it(self):
        """The tag must precede the log call, not follow it.

        Sentry's LoggingIntegration converts ``logger.error`` into an event
        synchronously, capturing the scope's tags at that moment. Tagging after
        the log left every event without an ``error_id``, making the id on the
        user's error page unsearchable — so the *order* is the contract here, and
        asserting ``set_tag`` was merely called does not pin it.
        """
        calls = []
        with patch('middleware.error_handlers.sentry_sdk') as sentry, \
                patch('middleware.error_handlers.logger') as log:
            sentry.set_tag.side_effect = lambda *a, **k: calls.append('set_tag')
            log.error.side_effect = lambda *a, **k: calls.append('log')
            log_unhandled_error(RuntimeError('boom'), '/x')
        assert calls == ['set_tag', 'log'], (
            f'expected the Sentry tag to be set before the log call, got {calls}'
        )

    def test_sentry_failure_does_not_break_logging(self):
        with patch('middleware.error_handlers.sentry_sdk') as sentry:
            sentry.set_tag.side_effect = RuntimeError('sentry down')
            error_id = log_unhandled_error(RuntimeError('boom'), '/x')
        assert _UUID_RE.match(error_id)
