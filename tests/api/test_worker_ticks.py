"""Cloud Scheduler drives the workers through POST /internal/workers/<key>/tick.

The endpoint is dark unless WORKER_MODE=scheduled, refuses anything but a
Google-signed token for the configured service account and audience, and runs
a worker through the same BackgroundLoop.run_once the in-process loop uses.
"""

import asyncio
import sys
import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api import worker_ticks
from application import worker_registry
from application.utils import google_oidc
from application.utils.background_loop import BackgroundLoop
from application.worker_registry import Placement, WorkerMode, WorkerSpec

AUDIENCE = 'https://api.wizzrobe.test'
ACCOUNT = 'scheduler@wizzrobe.iam.gserviceaccount.com'
KID = 'test-key'

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(key=_KEY, kid=KID, **overrides) -> str:
    now = int(time.time())
    claims = {
        'iss': 'https://accounts.google.com', 'aud': AUDIENCE, 'email': ACCOUNT,
        'email_verified': True, 'iat': now, 'exp': now + 300, 'sub': '1234',
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm='RS256', headers={'kid': kid})


@pytest.fixture(autouse=True)
def google_keys(monkeypatch):
    jwk = jwt.PyJWK(jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True) | {'kid': KID})
    fetches = []

    async def fake_fetch():
        fetches.append(1)
        monkeypatch.setattr(google_oidc, '_keys', {KID: jwk})
        monkeypatch.setattr(google_oidc, '_expires_at', time.monotonic() + 3600)
        monkeypatch.setattr(google_oidc, '_last_fetch', time.monotonic())

    monkeypatch.setattr(google_oidc, '_fetch_keys', fake_fetch)
    monkeypatch.setattr(google_oidc, '_keys', {})
    monkeypatch.setattr(google_oidc, '_expires_at', 0.0)
    monkeypatch.setattr(google_oidc, '_last_fetch', 0.0)
    return fetches


@pytest.fixture
def scheduled(monkeypatch):
    monkeypatch.setenv('WORKER_MODE', 'scheduled')
    monkeypatch.setenv('WORKER_TICK_AUDIENCE', AUDIENCE)
    monkeypatch.setenv('WORKER_TICK_SERVICE_ACCOUNT', ACCOUNT)
    monkeypatch.setattr(worker_ticks, '_running', {})


def _fake_worker(monkeypatch, key, *, tick, interval=60.0, placement=Placement.SCHEDULED, enabled=lambda: True):
    module_name = f'tests._fake_worker_{key}'
    module = types.ModuleType(module_name)
    module._tick = tick
    module._loop = BackgroundLoop(tick, interval=interval, name=f'zz {key}')
    module.started = []
    module.start = lambda: module.started.append(True)

    async def stop():
        module.started.append(False)

    module.stop = stop
    monkeypatch.setitem(sys.modules, module_name, module)
    spec = WorkerSpec(key, module_name, placement=placement, enabled=enabled)
    monkeypatch.setitem(worker_registry.WORKERS, key, spec)
    return module


def _client() -> AsyncClient:
    app = FastAPI()
    worker_ticks.mount(app)
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


async def _post(key='zz_fake', token=None):
    headers = {} if token is None else {'Authorization': f'Bearer {token}'}
    async with _client() as client:
        return await client.post(f'/internal/workers/{key}/tick', headers=headers)


async def _noop():
    return None


async def test_dark_in_loop_mode(monkeypatch):
    monkeypatch.delenv('WORKER_MODE', raising=False)
    _fake_worker(monkeypatch, 'zz_fake', tick=_noop)
    assert worker_registry.worker_mode() is WorkerMode.LOOP
    assert (await _post(token=_token())).status_code == 404


async def test_scheduled_mode_without_config_refuses(monkeypatch):
    monkeypatch.setenv('WORKER_MODE', 'scheduled')
    monkeypatch.delenv('WORKER_TICK_AUDIENCE', raising=False)
    monkeypatch.delenv('WORKER_TICK_SERVICE_ACCOUNT', raising=False)
    assert (await _post(token=_token())).status_code == 503


@pytest.mark.usefixtures('scheduled')
class TestAuth:
    async def test_no_token(self, monkeypatch):
        _fake_worker(monkeypatch, 'zz_fake', tick=_noop)
        assert (await _post()).status_code == 401

    @pytest.mark.parametrize('overrides', [
        {'aud': 'https://elsewhere.test'},
        {'email': 'someone@else.iam.gserviceaccount.com'},
        {'email_verified': False},
        {'iss': 'https://evil.test'},
        {'exp': int(time.time()) - 3600},
        {'exp': None},
    ])
    async def test_wrong_claims(self, monkeypatch, overrides):
        calls = []

        async def tick():
            calls.append(1)

        _fake_worker(monkeypatch, 'zz_fake', tick=tick)
        assert (await _post(token=_token(**overrides))).status_code == 401
        assert calls == []

    async def test_signed_by_someone_else(self, monkeypatch):
        _fake_worker(monkeypatch, 'zz_fake', tick=_noop)
        assert (await _post(token=_token(key=_OTHER_KEY))).status_code == 401

    async def test_unknown_key_refetches_once_then_refuses(self, monkeypatch, google_keys):
        _fake_worker(monkeypatch, 'zz_fake', tick=_noop)
        assert (await _post(token=_token())).status_code == 200
        assert (await _post(token=_token(kid='rotated'))).status_code == 401
        assert (await _post(token=_token(kid='rotated'))).status_code == 401
        assert len(google_keys) == 1

    async def test_symmetric_token_refused(self, monkeypatch):
        _fake_worker(monkeypatch, 'zz_fake', tick=_noop)
        forged = jwt.encode({'aud': AUDIENCE, 'email': ACCOUNT}, 'secret' * 6, algorithm='HS256')
        assert (await _post(token=forged)).status_code == 401


@pytest.mark.usefixtures('scheduled')
class TestTicks:
    async def test_runs_one_tick(self, monkeypatch):
        calls = []

        async def tick():
            calls.append(1)

        _fake_worker(monkeypatch, 'zz_fake', tick=tick)
        response = await _post(token=_token())
        assert response.status_code == 200
        assert response.json() == {'worker': 'zz_fake', 'ticks': 1, 'failures': 0}
        assert calls == [1]

    async def test_failing_tick_is_a_500(self, monkeypatch):
        async def tick():
            raise RuntimeError('boom')

        _fake_worker(monkeypatch, 'zz_fake', tick=tick)
        response = await _post(token=_token())
        assert response.status_code == 500
        assert response.json()['failures'] == 1

    async def test_fast_worker_bursts_inside_the_window(self, monkeypatch):
        monkeypatch.setenv('WORKER_TICK_WINDOW_SECONDS', '0.2')
        calls = []

        async def tick():
            calls.append(1)

        _fake_worker(monkeypatch, 'zz_fake', tick=tick, interval=0.02)
        response = await _post(token=_token())
        assert response.status_code == 200
        assert response.json()['ticks'] == len(calls) > 3

    async def test_disabled_worker_is_skipped(self, monkeypatch):
        calls = []

        async def tick():
            calls.append(1)

        _fake_worker(monkeypatch, 'zz_fake', tick=tick, enabled=lambda: False)
        response = await _post(token=_token())
        assert response.json() == {'worker': 'zz_fake', 'skipped': 'disabled'}
        assert calls == []

    async def test_a_tick_already_running_here_is_not_doubled(self, monkeypatch):
        _fake_worker(monkeypatch, 'zz_fake', tick=_noop)
        lock = worker_ticks._running.setdefault('zz_fake', asyncio.Lock())
        await lock.acquire()
        try:
            response = await _post(token=_token())
        finally:
            lock.release()
        assert response.json() == {'worker': 'zz_fake', 'skipped': 'running'}

    @pytest.mark.parametrize('placement', [Placement.BOT, Placement.PER_PROCESS])
    async def test_only_scheduled_placements_answer(self, monkeypatch, placement):
        _fake_worker(monkeypatch, 'zz_fake', tick=_noop, placement=placement)
        assert (await _post(token=_token())).status_code == 404

    async def test_unknown_worker(self):
        assert (await _post('no_such_worker', token=_token())).status_code == 404


class TestInProcessStart:
    def _fakes(self, monkeypatch):
        monkeypatch.setattr(worker_registry, 'WORKERS', {})
        return {
            'sched': _fake_worker(monkeypatch, 'sched', tick=_noop),
            'bot': _fake_worker(monkeypatch, 'bot', tick=_noop, placement=Placement.BOT),
            'off': _fake_worker(monkeypatch, 'off', tick=_noop, enabled=lambda: False),
        }

    def test_loop_mode_starts_every_enabled_worker(self, monkeypatch):
        fakes = self._fakes(monkeypatch)
        worker_registry.start_in_process(WorkerMode.LOOP)
        assert fakes['sched'].started == [True]
        assert fakes['bot'].started == [True]
        assert fakes['off'].started == []

    def test_scheduled_mode_keeps_only_the_loops_that_cannot_move(self, monkeypatch):
        fakes = self._fakes(monkeypatch)
        worker_registry.start_in_process(WorkerMode.SCHEDULED)
        assert fakes['sched'].started == []
        assert fakes['bot'].started == [True]

    async def test_stop_all_stops_everything(self, monkeypatch):
        fakes = self._fakes(monkeypatch)
        await worker_registry.stop_all()
        assert all(f.started == [False] for f in fakes.values())


def test_role_sync_subscribes_in_either_mode():
    spec = worker_registry.WORKERS['discord_role_sync']
    assert spec.placement is Placement.SCHEDULED
    assert spec.boot == 'subscribe'


@pytest.mark.parametrize('interval, cron', [
    (5, '* * * * *'), (60, '* * * * *'), (120, '*/2 * * * *'), (900, '*/15 * * * *'),
    (6 * 3600, '7 */6 * * *'), (24 * 3600, '17 4 * * *'),
])
def test_cron_schedule(interval, cron):
    assert worker_registry.cron_schedule(interval) == cron


def _task_worker(monkeypatch, key, *, handler, interval=5.0):
    module = _fake_worker(monkeypatch, key, tick=_noop, interval=interval)
    module.handle_task = handler
    spec = WorkerSpec(key, module.__name__, task_handler='handle_task')
    monkeypatch.setitem(worker_registry.WORKERS, key, spec)
    return module


@pytest.fixture
def enqueued(monkeypatch):
    from application.utils.clients import cloud_tasks_client
    calls = []

    async def fake_create(queue_path, body):
        calls.append((queue_path, body))
        return 'tasks/1'

    monkeypatch.setattr(cloud_tasks_client, 'create_task', fake_create)
    return calls


@pytest.fixture
def cloud_tasks(monkeypatch, scheduled):
    monkeypatch.setenv('CLOUD_TASKS_LOCATION', 'projects/p/locations/us-east4')
    monkeypatch.setenv('CLOUD_TASKS_QUEUE_PREFIX', 'wizzrobe-staging-')


async def _post_task(key, payload, token):
    async with _client() as client:
        return await client.post(
            f'/internal/tasks/{key}', json=payload, headers={'Authorization': f'Bearer {token}'},
        )


@pytest.mark.usefixtures('cloud_tasks')
class TestCloudTasks:
    async def test_runs_the_handler_and_queues_the_follow_up(self, monkeypatch, enqueued):
        seen = []

        async def handler(payload):
            seen.append(payload)
            return 5.0

        _task_worker(monkeypatch, 'zz_task', handler=handler)
        response = await _post_task('zz_task', {'task_id': 7}, _token())
        assert response.status_code == 200
        assert response.json() == {'worker': 'zz_task', 'follow_up': 5.0, 'queued': True}
        assert seen == [{'task_id': 7}]
        queue_path, body = enqueued[0]
        assert queue_path == 'projects/p/locations/us-east4/queues/wizzrobe-staging-zz-task'
        request = body['task']['httpRequest']
        assert request['url'] == f'{AUDIENCE}/internal/tasks/zz_task'
        assert request['oidcToken'] == {'serviceAccountEmail': ACCOUNT, 'audience': AUDIENCE}

    async def test_done_means_no_follow_up(self, monkeypatch, enqueued):
        async def handler(payload):
            return None

        _task_worker(monkeypatch, 'zz_task', handler=handler)
        response = await _post_task('zz_task', {}, _token())
        assert response.json()['queued'] is False
        assert enqueued == []

    async def test_needs_the_scheduler_token(self, monkeypatch, enqueued):
        _task_worker(monkeypatch, 'zz_task', handler=lambda p: None)
        async with _client() as client:
            assert (await client.post('/internal/tasks/zz_task', json={})).status_code == 401

    async def test_worker_without_a_handler_is_not_found(self, monkeypatch):
        _fake_worker(monkeypatch, 'zz_plain', tick=_noop)
        assert (await _post_task('zz_plain', {}, _token())).status_code == 404

    async def test_task_backed_worker_ticks_once_as_a_safety_net(self, monkeypatch):
        ticks = []

        async def tick():
            ticks.append(1)

        module = _task_worker(monkeypatch, 'zz_task', handler=lambda p: None, interval=0.01)
        module._loop = BackgroundLoop(tick, interval=0.01, name='zz task')
        result = await worker_registry.run_scheduled(worker_registry.WORKERS['zz_task'], window=1.0)
        assert result.ticks == 1 and ticks == [1]

    async def test_enqueue_failure_is_swallowed(self, monkeypatch):
        from application import task_queue
        from application.utils.clients import cloud_tasks_client

        async def broken(queue_path, body):
            raise cloud_tasks_client.CloudTasksError('HTTP 503')

        monkeypatch.setattr(cloud_tasks_client, 'create_task', broken)
        assert await task_queue.enqueue('seed_roll', {'task_id': 1}) is False


async def test_enqueue_is_a_no_op_without_cloud_tasks(monkeypatch, enqueued):
    from application import task_queue
    monkeypatch.setenv('WORKER_MODE', 'scheduled')
    monkeypatch.delenv('CLOUD_TASKS_LOCATION', raising=False)
    assert await task_queue.enqueue('seed_roll', {'task_id': 1}) is False
    monkeypatch.setenv('CLOUD_TASKS_LOCATION', 'projects/p/locations/r')
    monkeypatch.setenv('WORKER_MODE', 'loop')
    assert await task_queue.enqueue('seed_roll', {'task_id': 1}) is False
    assert enqueued == []


def test_scheduler_cron_slows_task_backed_workers():
    seed = worker_registry.WORKERS['seed_roll']
    assert worker_registry.scheduler_cron(seed, cloud_tasks=False) == '* * * * *'
    assert worker_registry.scheduler_cron(seed, cloud_tasks=True) == '*/5 * * * *'
    reminder = worker_registry.WORKERS['stage_reminder']
    assert worker_registry.scheduler_cron(reminder, cloud_tasks=True) == '* * * * *'
