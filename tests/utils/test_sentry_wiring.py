"""Tests for the Sentry wiring in ``application/utils/sentry.py``.

Covers what ``init_sentry`` decides from the environment and what every event
picks up on the way out: the tenant in scope, the signed-in user when the
request scope is gone, and scoped tags that never outlive their block.
"""

import asyncio
import logging

import pytest
import sentry_sdk

from application.tenant_context import tenant_scope
from application.utils import sentry as wiring


class TestEnrichment:
    def test_tags_tenant_in_scope(self):
        with tenant_scope(42):
            event = wiring._before_send({'tags': {'worker': 'x'}}, None)
        assert event['tags'] == {'worker': 'x', 'tenant_id': '42', 'surface': 'server'}

    def test_no_tenant_no_tag(self):
        with tenant_scope(None):
            event = wiring._before_send({}, None)
        assert 'tenant_id' not in (event.get('tags') or {})

    def test_existing_user_is_kept(self, monkeypatch):
        monkeypatch.setattr(wiring, '_session_user', lambda: {'id': '9'})
        event = wiring._before_send({'user': {'id': '1'}}, None)
        assert event['user'] == {'id': '1'}

    def test_session_user_fills_a_missing_user(self, monkeypatch):
        monkeypatch.setattr(wiring, '_session_user', lambda: {'id': '9', 'username': 'u'})
        event = wiring._before_send({}, None)
        assert event['user'] == {'id': '9', 'username': 'u'}

    def test_session_user_is_none_outside_a_client(self):
        assert wiring._session_user() is None

    def test_still_scrubs_auth_headers(self):
        event = wiring._before_send(
            {'request': {'headers': {'Authorization': 'Bearer x'}, 'cookies': {'a': 'b'}}}, None,
        )
        assert event['request']['headers']['Authorization'] == '[Filtered]'
        assert 'cookies' not in event['request']

    def test_log_gets_tenant_attribute(self):
        with tenant_scope(7):
            log = wiring._before_send_log({'attributes': {}}, None)
        assert log['attributes']['tenant_id'] == 7

    def test_log_before_startup_creates_no_client(self, monkeypatch):
        # Before NiceGUI starts, reading context.client with no slot builds a
        # request-less script client that crashed prune_user_storage in prod.
        from nicegui import core
        from nicegui.app.app import State
        from nicegui.client import Client

        monkeypatch.setattr(core.app, '_state', State.STOPPED)
        monkeypatch.setattr(core, 'script_mode', False)
        monkeypatch.setattr(core, 'script_client', None)
        monkeypatch.setattr(Client, 'instances', {})
        with tenant_scope(None):
            log = wiring._before_send_log({'attributes': {}}, None)
        assert 'tenant_id' not in log['attributes']
        assert Client.instances == {}
        assert core.script_mode is False


class TestConfig:
    def test_release_prefers_explicit(self, monkeypatch):
        monkeypatch.setenv('SENTRY_RELEASE', 'custom-1')
        monkeypatch.setenv('GIT_SHA', 'abcdef1234567890')
        assert wiring._release() == 'custom-1'

    def test_release_from_git_sha(self, monkeypatch):
        monkeypatch.delenv('SENTRY_RELEASE', raising=False)
        monkeypatch.setenv('GIT_SHA', 'abcdef1234567890')
        assert wiring._release() == 'wizzrobe@abcdef123456'

    def test_release_unset(self, monkeypatch):
        monkeypatch.delenv('SENTRY_RELEASE', raising=False)
        monkeypatch.delenv('GIT_SHA', raising=False)
        assert wiring._release() is None

    def test_traces_default_on_in_production_only(self, monkeypatch):
        monkeypatch.delenv('SENTRY_TRACES_SAMPLE_RATE', raising=False)
        monkeypatch.setattr(wiring, 'is_production', lambda: True)
        assert wiring._traces_sample_rate() == 0.1
        monkeypatch.setattr(wiring, 'is_production', lambda: False)
        assert wiring._traces_sample_rate() == 0.0

    def test_traces_env_wins_and_is_clamped(self, monkeypatch):
        monkeypatch.setattr(wiring, 'is_production', lambda: True)
        monkeypatch.setenv('SENTRY_TRACES_SAMPLE_RATE', '0')
        assert wiring._traces_sample_rate() == 0.0
        monkeypatch.setenv('SENTRY_TRACES_SAMPLE_RATE', '7')
        assert wiring._traces_sample_rate() == 1.0
        monkeypatch.setenv('SENTRY_TRACES_SAMPLE_RATE', 'lots')
        assert wiring._traces_sample_rate() == 0.0

    def test_profiles_every_trace_in_production_only(self, monkeypatch):
        monkeypatch.delenv('SENTRY_PROFILES_SAMPLE_RATE', raising=False)
        monkeypatch.setattr(wiring, 'is_production', lambda: True)
        assert wiring._profiles_sample_rate() == 1.0
        monkeypatch.setattr(wiring, 'is_production', lambda: False)
        assert wiring._profiles_sample_rate() == 0.0

    def test_profiles_env_wins_and_is_clamped(self, monkeypatch):
        monkeypatch.setattr(wiring, 'is_production', lambda: True)
        monkeypatch.setenv('SENTRY_PROFILES_SAMPLE_RATE', '0.25')
        assert wiring._profiles_sample_rate() == 0.25
        monkeypatch.setenv('SENTRY_PROFILES_SAMPLE_RATE', '-1')
        assert wiring._profiles_sample_rate() == 0.0
        monkeypatch.setenv('SENTRY_PROFILES_SAMPLE_RATE', 'lots')
        assert wiring._profiles_sample_rate() == 0.0

    def test_logs_level(self, monkeypatch):
        monkeypatch.delenv('SENTRY_LOGS_LEVEL', raising=False)
        assert wiring._sentry_logs_level() == logging.INFO
        monkeypatch.setenv('SENTRY_LOGS_LEVEL', 'warning')
        assert wiring._sentry_logs_level() == logging.WARNING
        monkeypatch.setenv('SENTRY_LOGS_LEVEL', 'off')
        assert wiring._sentry_logs_level() is None


class TestScopedTags:
    def test_tags_live_only_inside_the_block(self):
        with wiring.sentry_tags(worker='seed roll', skipped=None):
            tags = sentry_sdk.get_current_scope()._tags
            assert tags['worker'] == 'seed roll'
            assert 'skipped' not in tags
            wiring.tag_current_scope(tenant_id=3)
            assert sentry_sdk.get_current_scope()._tags['tenant_id'] == '3'
        outside = sentry_sdk.get_current_scope()._tags
        assert 'worker' not in outside and 'tenant_id' not in outside


class TestGracefulShutdownNoise:
    """uvicorn's shutdown-deadline cancellations are dropped; real errors are not."""

    @staticmethod
    def _hint(exc):
        return {'exc_info': (type(exc), exc, None)}

    def test_drops_cancelled_request_traceback(self):
        exc = asyncio.CancelledError('Task cancelled, timeout graceful shutdown exceeded')
        event = {'logger': 'uvicorn.error', 'logentry': {'message': 'Exception in ASGI application'}}
        assert wiring._before_send(event, self._hint(exc)) is None

    def test_drops_cancel_count_message(self):
        event = {
            'logger': 'uvicorn.error',
            'logentry': {
                'message': 'Cancel %s running task(s), timeout graceful shutdown exceeded',
                'params': [2],
                'formatted': 'Cancel 2 running task(s), timeout graceful shutdown exceeded',
            },
        }
        assert wiring._before_send(event, {}) is None

    def test_drops_write_to_a_gone_browser(self):
        from uvicorn.protocols.utils import ClientDisconnected
        exc = ClientDisconnected()
        assert wiring._before_send({'exception': {}}, self._hint(exc)) is None

    def test_keeps_other_os_errors(self):
        exc = ConnectionResetError('db socket reset')
        assert wiring._before_send({'exception': {}}, self._hint(exc)) is not None

    def test_keeps_other_cancelled_errors(self):
        exc = asyncio.CancelledError()
        assert wiring._before_send({}, self._hint(exc)) is not None

    def test_keeps_other_uvicorn_errors(self):
        event = {'logger': 'uvicorn.error', 'logentry': {'formatted': 'Exception in ASGI application'}}
        assert wiring._before_send(event, {}) is not None

    def test_keeps_same_text_from_another_logger(self):
        event = {
            'logger': 'wizzrobe.main',
            'logentry': {'formatted': 'Cancel 2 running task(s), timeout graceful shutdown exceeded'},
        }
        assert wiring._before_send(event, {}) is not None

    def test_keeps_real_exception_logged_with_the_message(self):
        exc = RuntimeError('boom')
        event = {
            'logger': 'uvicorn.error',
            'logentry': {'formatted': 'Cancel 2 running task(s), timeout graceful shutdown exceeded'},
        }
        assert wiring._before_send(event, self._hint(exc)) is not None

    def test_shutdown_warning_still_reaches_sentry_logs(self):
        # The per-request warning is what keeps a real stall visible; it must
        # stay out of the logger blocklist and at a level Sentry Logs ships.
        assert 'wizzrobe.shutdown' not in wiring._NOISY_FOR_SENTRY_LOGS
        assert wiring._sentry_logs_level() <= logging.WARNING


class TestFlush:
    async def test_flush_sentry_flushes_with_a_timeout(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sentry_sdk, 'flush', lambda timeout=None: calls.append(timeout))
        await wiring.flush_sentry()
        assert calls == [wiring._SHUTDOWN_FLUSH_TIMEOUT]

    async def test_flush_sentry_is_safe_without_init(self):
        await wiring.flush_sentry()


class TestBrowserSdk:
    @pytest.fixture(autouse=True)
    def _restore_snapshot(self):
        from application.utils import browser_monitoring
        saved = browser_monitoring.current()
        yield
        browser_monitoring.replace(saved)

    def test_nothing_loads_without_a_dsn(self, monkeypatch):
        monkeypatch.delenv('SENTRY_DSN', raising=False)
        assert wiring.browser_sdk_config() is None

    def test_nothing_loads_while_paused(self, monkeypatch):
        from application.utils import browser_monitoring
        monkeypatch.setenv('SENTRY_DSN', 'https://key@o1.ingest.us.sentry.io/2')
        browser_monitoring.replace(browser_monitoring.Snapshot(enabled=False))
        assert wiring.browser_sdk_config() is None

    def test_config_shares_the_server_dsn_and_tags_the_surface(self, monkeypatch):
        from application.utils import browser_monitoring
        monkeypatch.setenv('SENTRY_DSN', 'https://key@o1.ingest.us.sentry.io/2')
        browser_monitoring.replace(browser_monitoring.Snapshot(
            sample_rate=0.5, rules=(browser_monitoring.IgnoreRule('boom', 'message'),)))
        with tenant_scope(7):
            config = wiring.browser_sdk_config()
        assert config['dsn'] == 'https://key@o1.ingest.us.sentry.io/2'
        assert config['tags'] == {'surface': 'browser', 'tenant_id': '7'}
        assert config['sampleRate'] == 0.5
        assert config['rules'] == [{'pattern': 'boom', 'match': 'message'}]
        assert {'pattern': 'ResizeObserver loop', 'match': 'message'} in config['builtin']

    def test_server_events_carry_surface_and_replay(self, monkeypatch):
        monkeypatch.setattr(wiring, 'client_stash', lambda: {wiring.REPLAY_ID_KEY: 'a' * 32})
        with tenant_scope(None):
            event = wiring._before_send({}, None)
        assert event['tags']['surface'] == 'server'
        assert event['contexts']['replay'] == {'replay_id': 'a' * 32}


class TestStaleSocketSession:
    """Sentry SGLMAN-13/14: engine.io's stale-session error is a WARNING, not an issue."""

    def _record(self, msg, level=logging.ERROR):
        return logging.LogRecord('engineio.server', level, __file__, 1, msg, None, None)

    def test_invalid_session_is_downgraded(self):
        record = self._record('Invalid session abc (further occurrences of this error will be logged with level INFO)')
        assert wiring.StaleSocketSessionFilter().filter(record) is True
        assert record.levelno == logging.WARNING
        assert record.levelname == 'WARNING'

    def test_disconnected_session_is_downgraded(self):
        record = self._record("'Session is disconnected' abc (further occurrences ...)")
        wiring.StaleSocketSessionFilter().filter(record)
        assert record.levelno == logging.WARNING

    def test_invalid_websocket_upgrade_is_downgraded(self):
        record = self._record('Invalid websocket upgrade (further occurrences of this error will be logged with level INFO)')
        wiring.StaleSocketSessionFilter().filter(record)
        assert record.levelno == logging.WARNING

    def test_other_engineio_errors_stay_errors(self):
        record = self._record('https://evil.example is not an accepted origin.')
        wiring.StaleSocketSessionFilter().filter(record)
        assert record.levelno == logging.ERROR

    def test_installs_once(self):
        wiring.install_stale_session_filter()
        wiring.install_stale_session_filter()
        filters = logging.getLogger('engineio.server').filters
        assert sum(isinstance(f, wiring.StaleSocketSessionFilter) for f in filters) == 1


class TestTracesSampler:
    def test_websocket_is_never_traced(self):
        sampler = wiring._traces_sampler(1.0)
        assert sampler({'asgi_scope': {'type': 'websocket'}, 'parent_sampled': True}) == 0.0

    def test_http_uses_the_rate(self):
        sampler = wiring._traces_sampler(0.1)
        assert sampler({'asgi_scope': {'type': 'http'}, 'parent_sampled': None}) == 0.1
        assert sampler({}) == 0.1

    def test_http_follows_the_parent_decision(self):
        sampler = wiring._traces_sampler(0.1)
        assert sampler({'asgi_scope': {'type': 'http'}, 'parent_sampled': True}) == 1.0
        assert sampler({'asgi_scope': {'type': 'http'}, 'parent_sampled': False}) == 0.0

    def test_asgi_middleware_drops_websocket_but_keeps_http(self):
        from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
        from sentry_sdk.transport import Transport

        sent = []

        class Capture(Transport):
            def capture_envelope(self, envelope):
                sent.extend(item.payload.json for item in envelope.items if item.type == 'transaction')

        async def asgi_app(scope, receive, send):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.accept'})
                await send({'type': 'websocket.close'})
            else:
                await send({'type': 'http.response.start', 'status': 200, 'headers': []})
                await send({'type': 'http.response.body', 'body': b''})

        async def receive():
            return {'type': 'websocket.connect'}

        async def send(_message):
            pass

        async def call(scope_type):
            scope = {'type': scope_type, 'path': '/x', 'headers': [], 'query_string': b'',
                     'asgi': {'version': '3.0'}, 'scheme': 'http', 'server': ('t', 80), 'client': ('c', 1)}
            if scope_type == 'http':
                scope['method'] = 'GET'
            await SentryAsgiMiddleware(asgi_app)(scope, receive, send)

        previous = sentry_sdk.get_client()
        sentry_sdk.init(dsn='https://k@example.invalid/1', transport=Capture(),
                        traces_sampler=wiring._traces_sampler(1.0))
        try:
            asyncio.run(call('websocket'))
            asyncio.run(call('http'))
            sentry_sdk.flush()
        finally:
            sentry_sdk.get_global_scope().set_client(previous)

        assert [t['contexts']['trace']['op'] for t in sent] == ['http.server']
