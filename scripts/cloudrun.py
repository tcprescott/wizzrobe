"""Render and apply a Cloud Run environment from ``deploy/cloudrun/<env>.toml``.

Two Cloud Run services share one image:

- ``<name>-api``: ``main.py`` with ``WORKER_MODE=scheduled``,
  ``DISCORD_GATEWAY=off`` and ``RACETIME_BOT_ENABLED=off``; scales on requests.
- ``<name>-bots``: ``bots.py`` (Discord gateway + racetime room bot), exactly
  one instance with CPU always on.

Plus one Cloud Scheduler job per ``Placement.SCHEDULED`` worker in
``application.worker_registry.WORKERS`` (so a new worker gets its job on the
next apply, and a removed one loses it), and one Cloud Tasks queue per
task-backed worker.

Usage::

    poetry run python scripts/cloudrun.py plan deploy/cloudrun/staging.toml --image TAG
    poetry run python scripts/cloudrun.py render deploy/cloudrun/staging.toml --image TAG --out build/
    poetry run python scripts/cloudrun.py apply deploy/cloudrun/staging.toml --image TAG --yes

``plan`` and ``render`` touch nothing. ``apply`` needs ``gcloud`` signed in with
rights on the project, and ``--yes``.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

CLOUDSQL_PROXY_IMAGE = 'gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.14.1'
PORT = 8000
HEALTH_PATH = '/api/health'
REQUEST_TIMEOUT_SECONDS = 900
SCHEDULER_TIME_ZONE = 'Etc/UTC'
EXPORT_QUEUE_CONCURRENCY = 1
DEFAULT_QUEUE_CONCURRENCY = 10


@dataclass
class Config:
    name: str
    project: str
    region: str
    image: str
    api_url: str
    runtime_service_account: str
    invoker_service_account: str
    cloudsql_instance: str = ''
    cloud_tasks: bool = True
    api: Dict[str, Any] = field(default_factory=dict)
    bots: Dict[str, Any] = field(default_factory=dict)
    env: Dict[str, str] = field(default_factory=dict)
    secrets: Dict[str, str] = field(default_factory=dict)

    @property
    def api_service(self) -> str:
        return f'{self.name}-api'

    @property
    def bots_service(self) -> str:
        return f'{self.name}-bots'

    @property
    def queue_prefix(self) -> str:
        return f'{self.name}-'


def load_config(path: Path) -> Config:
    with open(path, 'rb') as fh:
        raw = tomllib.load(fh)
    raw['env'] = {k: str(v) for k, v in raw.get('env', {}).items()}
    raw['secrets'] = {k: str(v) for k, v in raw.get('secrets', {}).items()}
    return Config(**raw)


def _env(pairs: Dict[str, str], secrets: Dict[str, str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = [{'name': k, 'value': v} for k, v in pairs.items()]
    out += [
        {'name': k, 'valueFrom': {'secretKeyRef': {'name': secret, 'key': 'latest'}}}
        for k, secret in secrets.items()
    ]
    return out


def _common_env(cfg: Config) -> Dict[str, str]:
    env = dict(cfg.env)
    if cfg.cloudsql_instance:
        env.update({'DB_HOST': '127.0.0.1', 'DB_PORT': '5432'})
    return env


def api_env(cfg: Config) -> Dict[str, str]:
    env = _common_env(cfg)
    env.update({
        'WORKER_MODE': 'scheduled',
        'WORKER_TICK_AUDIENCE': cfg.api_url,
        'WORKER_TICK_SERVICE_ACCOUNT': cfg.invoker_service_account,
        'DISCORD_GATEWAY': 'off',
        'RACETIME_BOT_ENABLED': 'off',
    })
    if cfg.cloud_tasks:
        env['CLOUD_TASKS_LOCATION'] = f'projects/{cfg.project}/locations/{cfg.region}'
        env['CLOUD_TASKS_QUEUE_PREFIX'] = cfg.queue_prefix
    return env


def bots_env(cfg: Config) -> Dict[str, str]:
    env = _common_env(cfg)
    env.update({'WORKER_MODE': 'scheduled', 'RACETIME_BOT_ENABLED': 'on'})
    return env


def _probe(path: str, port: int, *, period: int, failures: int) -> Dict[str, Any]:
    return {
        'httpGet': {'path': path, 'port': port},
        'periodSeconds': period, 'timeoutSeconds': min(period, 5), 'failureThreshold': failures,
    }


def _proxy_container(cfg: Config) -> Dict[str, Any]:
    return {
        'name': 'cloud-sql-proxy',
        'image': CLOUDSQL_PROXY_IMAGE,
        'args': [
            '--port=5432', '--address=127.0.0.1', '--health-check',
            '--http-address=0.0.0.0', '--http-port=9090', cfg.cloudsql_instance,
        ],
        'resources': {'limits': {'cpu': '0.5', 'memory': '256Mi'}},
        'startupProbe': _probe('/startup', 9090, period=2, failures=30),
    }


def _service(
    cfg: Config, name: str, *, args: List[str], env: Dict[str, str],
    scale: Dict[str, str], resources: Dict[str, Any], concurrency: int,
    ingress: str, session_affinity: bool,
) -> Dict[str, Any]:
    template_annotations = {
        **scale,
        'run.googleapis.com/execution-environment': 'gen2',
        # Off: DMs, event-bus subscribers and webhooks finish after the response.
        'run.googleapis.com/cpu-throttling': 'false',
        'run.googleapis.com/startup-cpu-boost': 'true',
    }
    if session_affinity:
        template_annotations['run.googleapis.com/sessionAffinity'] = 'true'
    app = {
        'name': 'app',
        'image': cfg.image,
        'args': args,
        'ports': [{'name': 'http1', 'containerPort': PORT}],
        'env': _env(env, cfg.secrets),
        'resources': {'limits': resources},
        # Generous: a boot that applies migrations waits on the migration lock.
        'startupProbe': _probe(HEALTH_PATH, PORT, period=5, failures=36),
        'livenessProbe': _probe(HEALTH_PATH, PORT, period=30, failures=3),
    }
    containers = [app]
    if cfg.cloudsql_instance:
        containers.append(_proxy_container(cfg))
        template_annotations['run.googleapis.com/container-dependencies'] = json.dumps({'app': ['cloud-sql-proxy']})
    return {
        'apiVersion': 'serving.knative.dev/v1',
        'kind': 'Service',
        'metadata': {
            'name': name,
            'labels': {'cloud.googleapis.com/location': cfg.region, 'app': 'wizzrobe'},
            'annotations': {'run.googleapis.com/ingress': ingress},
        },
        'spec': {'template': {
            'metadata': {'annotations': template_annotations},
            'spec': {
                'serviceAccountName': cfg.runtime_service_account,
                'containerConcurrency': concurrency,
                'timeoutSeconds': REQUEST_TIMEOUT_SECONDS,
                'containers': containers,
            },
        }},
    }


def api_service(cfg: Config) -> Dict[str, Any]:
    api = cfg.api
    return _service(
        cfg, cfg.api_service,
        args=['./start.sh', 'prod'],
        env=api_env(cfg),
        scale={
            'autoscaling.knative.dev/minScale': str(api.get('min_instances', 0)),
            'autoscaling.knative.dev/maxScale': str(api.get('max_instances', 4)),
        },
        resources={'cpu': str(api.get('cpu', '1')), 'memory': str(api.get('memory', '1Gi'))},
        concurrency=int(api.get('concurrency', 80)),
        ingress='all',
        # NiceGUI pages keep a websocket and per-tab state on one instance.
        session_affinity=True,
    )


def bots_service(cfg: Config) -> Dict[str, Any]:
    bots = cfg.bots
    return _service(
        cfg, cfg.bots_service,
        args=['./start.sh', 'bots'],
        env=bots_env(cfg),
        scale={'autoscaling.knative.dev/minScale': '1', 'autoscaling.knative.dev/maxScale': '1'},
        resources={'cpu': str(bots.get('cpu', '1')), 'memory': str(bots.get('memory', '512Mi'))},
        concurrency=10,
        ingress='internal',
        session_affinity=False,
    )


@dataclass(frozen=True)
class SchedulerJob:
    name: str
    worker: str
    schedule: str
    uri: str
    attempt_deadline_seconds: int


@dataclass(frozen=True)
class TaskQueue:
    name: str
    worker: str
    max_concurrent: int


def _scheduled_specs():
    from application.worker_registry import WORKERS, Placement
    return [s for s in WORKERS.values() if s.placement is Placement.SCHEDULED]


def scheduler_jobs(cfg: Config) -> List[SchedulerJob]:
    from application.worker_registry import scheduler_cron

    jobs = []
    for spec in _scheduled_specs():
        loop = spec.loop()
        jobs.append(SchedulerJob(
            name=f'{cfg.name}-{spec.key}'.replace('_', '-'),
            worker=spec.key,
            schedule=scheduler_cron(spec, cloud_tasks=cfg.cloud_tasks),
            uri=f'{cfg.api_url.rstrip("/")}/internal/workers/{spec.key}/tick',
            attempt_deadline_seconds=int(min(1800, max(60.0, loop.tick_timeout) + 60)),
        ))
    return jobs


def task_queues(cfg: Config) -> List[TaskQueue]:
    if not cfg.cloud_tasks:
        return []
    return [
        TaskQueue(
            name=f'{cfg.queue_prefix}{spec.key}'.replace('_', '-'),
            worker=spec.key,
            max_concurrent=EXPORT_QUEUE_CONCURRENCY if spec.key == 'data_export' else DEFAULT_QUEUE_CONCURRENCY,
        )
        for spec in _scheduled_specs() if spec.task_handler
    ]


def _scheduler_flags(cfg: Config, job: SchedulerJob) -> List[str]:
    return [
        f'--project={cfg.project}', f'--location={cfg.region}',
        f'--schedule={job.schedule}', f'--time-zone={SCHEDULER_TIME_ZONE}',
        f'--uri={job.uri}', '--http-method=POST',
        f'--oidc-service-account-email={cfg.invoker_service_account}',
        f'--oidc-token-audience={cfg.api_url}',
        f'--attempt-deadline={job.attempt_deadline_seconds}s',
        '--max-retry-attempts=0',
    ]


def _queue_flags(cfg: Config, queue: TaskQueue) -> List[str]:
    return [
        f'--project={cfg.project}', f'--location={cfg.region}',
        f'--max-concurrent-dispatches={queue.max_concurrent}',
        '--max-attempts=5', '--min-backoff=5s', '--max-backoff=60s',
    ]


def render(cfg: Config, out: Path) -> Dict[str, Path]:
    import yaml

    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for service in (api_service(cfg), bots_service(cfg)):
        path = out / f'{service["metadata"]["name"]}.yaml'
        path.write_text(yaml.safe_dump(service, sort_keys=False))
        paths[service['metadata']['name']] = path
    return paths


def _gcloud(*args: str) -> List[str]:
    return ['gcloud', *args, '--quiet']


def plan(cfg: Config, rendered: Dict[str, Path]) -> List[List[str]]:
    """Every gcloud command ``apply`` would run, in order, as create-or-update pairs."""
    commands: List[List[str]] = []
    for queue in task_queues(cfg):
        commands.append(_gcloud('tasks', 'queues', 'create|update', queue.name, *_queue_flags(cfg, queue)))
    for name in (cfg.api_service, cfg.bots_service):
        commands.append(_gcloud(
            'run', 'services', 'replace', str(rendered[name]),
            f'--project={cfg.project}', f'--region={cfg.region}',
        ))
    commands.append(_gcloud(
        'run', 'services', 'add-iam-policy-binding', cfg.api_service,
        f'--project={cfg.project}', f'--region={cfg.region}',
        '--member=allUsers', '--role=roles/run.invoker',
    ))
    for job in scheduler_jobs(cfg):
        commands.append(_gcloud('scheduler', 'jobs', 'create|update', 'http', job.name, *_scheduler_flags(cfg, job)))
    return commands


def _exists(kind: List[str], name: str, cfg: Config) -> bool:
    result = subprocess.run(
        ['gcloud', *kind, 'describe', name, f'--project={cfg.project}', f'--location={cfg.region}', '--format=value(name)'],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _resolve(command: List[str], cfg: Config) -> List[str]:
    if 'create|update' not in command:
        return command
    at = command.index('create|update')
    kind = command[1:at]
    target = command[at + 1:]
    name = target[1] if target[0] == 'http' else target[0]
    verb = 'update' if _exists(kind, name, cfg) else 'create'
    return [*command[:at], verb, *target]


def stale_jobs(cfg: Config) -> List[str]:
    """Scheduler jobs that tick this environment's API but that no worker owns any more.

    Matched on the target URL, not the name, so another environment's jobs in
    the same project are never touched.
    """
    result = subprocess.run(
        ['gcloud', 'scheduler', 'jobs', 'list', f'--project={cfg.project}', f'--location={cfg.region}',
         '--format=json(name,httpTarget.uri)'],
        capture_output=True, text=True, check=True,
    )
    return _stale(cfg, json.loads(result.stdout or '[]'))


def _stale(cfg: Config, listed: List[Dict[str, Any]]) -> List[str]:
    wanted = {job.name for job in scheduler_jobs(cfg)}
    tick_prefix = f'{cfg.api_url.rstrip("/")}/internal/workers/'
    stale = []
    for row in listed:
        name = str(row.get('name', '')).rsplit('/', 1)[-1]
        uri = str((row.get('httpTarget') or {}).get('uri', ''))
        if uri.startswith(tick_prefix) and name not in wanted:
            stale.append(name)
    return stale


def apply(cfg: Config, rendered: Dict[str, Path]) -> None:
    for command in plan(cfg, rendered):
        command = _resolve(command, cfg)
        print('+', shlex.join(command), flush=True)
        subprocess.run(command, check=True)
    for name in stale_jobs(cfg):
        command = _gcloud('scheduler', 'jobs', 'delete', name, f'--project={cfg.project}', f'--location={cfg.region}')
        print('+', shlex.join(command), flush=True)
        subprocess.run(command, check=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['plan', 'render', 'apply'])
    parser.add_argument('config', type=Path)
    parser.add_argument('--image', help='full image reference with tag; defaults to the config image :latest')
    parser.add_argument('--out', type=Path, help='where render writes the service YAML')
    parser.add_argument('--yes', action='store_true', help='apply really runs gcloud')
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    cfg.image = args.image or f'{cfg.image}:latest'
    if args.action == 'apply' and 'TODO' in json.dumps(cfg.__dict__):
        parser.error(f'{args.config} still has TODO values')
    if args.action == 'apply' and not args.yes:
        parser.error('apply changes the cloud project; pass --yes')

    out = args.out or Path(tempfile.mkdtemp(prefix='cloudrun-'))
    rendered = render(cfg, out)
    if args.action == 'render':
        for path in rendered.values():
            print(path)
        return 0
    if args.action == 'plan':
        for path in rendered.values():
            print(f'# {path}')
        for command in plan(cfg, rendered):
            print(shlex.join(command))
        print(f'# then: delete Scheduler jobs that tick {cfg.api_url} but no worker owns')
        return 0
    apply(cfg, rendered)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
