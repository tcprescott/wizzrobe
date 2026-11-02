"""The Cloud Run config scripts/cloudrun.py builds from deploy/cloudrun/staging.toml.

The Scheduler jobs come from WORKERS, so these check the two never drift, and
that the api/bots split keeps each long-lived connection in exactly one place.
"""

from pathlib import Path

import pytest
import yaml

from application.worker_registry import WORKERS, Placement
from scripts import cloudrun

CONFIG = Path(__file__).parent.parent / 'deploy' / 'cloudrun' / 'staging.toml'


@pytest.fixture
def cfg():
    config = cloudrun.load_config(CONFIG)
    config.image = 'registry/wizzrobe:abc'
    return config


def _env(service):
    app = service['spec']['template']['spec']['containers'][0]
    return {e['name']: e.get('value', e.get('valueFrom')) for e in app['env']}


def test_one_job_per_scheduled_worker(cfg):
    jobs = {job.worker for job in cloudrun.scheduler_jobs(cfg)}
    assert jobs == {k for k, s in WORKERS.items() if s.placement is Placement.SCHEDULED}


def test_jobs_tick_the_api_with_the_invoker_token(cfg):
    for job in cloudrun.scheduler_jobs(cfg):
        assert job.uri == f'{cfg.api_url}/internal/workers/{job.worker}/tick'
        flags = cloudrun._scheduler_flags(cfg, job)
        assert f'--oidc-service-account-email={cfg.invoker_service_account}' in flags
        assert f'--oidc-token-audience={cfg.api_url}' in flags
        assert 60 <= job.attempt_deadline_seconds <= 1800


def test_task_backed_workers_get_a_queue_and_a_slow_safety_net(cfg):
    queues = {q.worker: q for q in cloudrun.task_queues(cfg)}
    assert set(queues) == {'seed_roll', 'data_export'}
    assert queues['data_export'].max_concurrent == 1
    schedules = {job.worker: job.schedule for job in cloudrun.scheduler_jobs(cfg)}
    assert schedules['seed_roll'] == '*/5 * * * *'
    cfg.cloud_tasks = False
    assert cloudrun.task_queues(cfg) == []
    assert {j.worker: j.schedule for j in cloudrun.scheduler_jobs(cfg)}['seed_roll'] == '* * * * *'


def test_queue_names_match_what_the_api_enqueues_on(cfg, monkeypatch):
    from application import task_queue
    env = cloudrun.api_env(cfg)
    monkeypatch.setenv('CLOUD_TASKS_QUEUE_PREFIX', env['CLOUD_TASKS_QUEUE_PREFIX'])
    for queue in cloudrun.task_queues(cfg):
        assert task_queue.queue_name(queue.worker) == queue.name


def test_api_leaves_the_connections_to_the_bot_service(cfg):
    env = _env(cloudrun.api_service(cfg))
    assert env['WORKER_MODE'] == 'scheduled'
    assert env['DISCORD_GATEWAY'] == 'off'
    assert env['RACETIME_BOT_ENABLED'] == 'off'
    assert env['WORKER_TICK_AUDIENCE'] == cfg.api_url
    assert env['WORKER_TICK_SERVICE_ACCOUNT'] == cfg.invoker_service_account


def test_bot_service_is_one_always_on_instance(cfg):
    service = cloudrun.bots_service(cfg)
    annotations = service['spec']['template']['metadata']['annotations']
    assert annotations['autoscaling.knative.dev/minScale'] == '1'
    assert annotations['autoscaling.knative.dev/maxScale'] == '1'
    assert annotations['run.googleapis.com/cpu-throttling'] == 'false'
    assert service['spec']['template']['spec']['containers'][0]['args'] == ['./start.sh', 'bots']
    assert _env(service)['RACETIME_BOT_ENABLED'] == 'on'


def test_both_services_run_the_same_image_with_the_db_proxy(cfg):
    for service in (cloudrun.api_service(cfg), cloudrun.bots_service(cfg)):
        containers = service['spec']['template']['spec']['containers']
        assert containers[0]['image'] == 'registry/wizzrobe:abc'
        assert [c['name'] for c in containers] == ['app', 'cloud-sql-proxy']
        assert 'ports' not in containers[1]
        assert _env(service)['DB_HOST'] == '127.0.0.1'


def test_secrets_are_references_not_values(cfg):
    env = _env(cloudrun.api_service(cfg))
    for name, secret in cfg.secrets.items():
        assert env[name] == {'secretKeyRef': {'name': secret, 'key': 'latest'}}


def test_render_writes_valid_yaml(cfg, tmp_path):
    paths = cloudrun.render(cfg, tmp_path)
    for name, path in paths.items():
        assert yaml.safe_load(path.read_text())['metadata']['name'] == name


def test_stale_jobs_are_only_this_environments(cfg):
    listed = [
        {'name': 'projects/p/locations/r/jobs/wizzrobe-staging-stage-reminder',
         'httpTarget': {'uri': f'{cfg.api_url}/internal/workers/stage_reminder/tick'}},
        {'name': 'projects/p/locations/r/jobs/wizzrobe-staging-gone',
         'httpTarget': {'uri': f'{cfg.api_url}/internal/workers/gone/tick'}},
        {'name': 'projects/p/locations/r/jobs/someone-elses',
         'httpTarget': {'uri': 'https://other.example/internal/workers/gone/tick'}},
    ]
    assert cloudrun._stale(cfg, listed) == ['wizzrobe-staging-gone']


def test_apply_refuses_todo_values():
    with pytest.raises(SystemExit):
        cloudrun.main(['apply', str(CONFIG), '--yes'])
