"""Every background worker, where it runs, and the switch between the two modes.

``WORKER_MODE`` picks how workers run:

- ``loop`` (default, today's production): one process runs every worker as an
  in-process :class:`~application.utils.background_loop.BackgroundLoop`.
- ``scheduled``: Cloud Scheduler calls ``POST /internal/workers/<key>/tick``
  (``api/worker_ticks.py``) for each :attr:`Placement.SCHEDULED` worker, so any
  API instance can run a tick and none keeps a loop. The other placements still
  loop in-process: see :class:`Placement`.

``main.py`` starts and stops workers through :func:`start_in_process` and
:func:`stop_all`, and ``scripts/run_worker_tick.py`` reads :data:`WORKERS`, so
a new worker is added here and nowhere else (``tests/test_registry_drift.py``).
"""

from __future__ import annotations

import asyncio
import importlib
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from types import ModuleType
from typing import Callable, Dict, Optional

from application.utils.background_loop import BackgroundLoop


class WorkerMode(StrEnum):
    LOOP = 'loop'
    SCHEDULED = 'scheduled'


class Placement(StrEnum):
    SCHEDULED = 'scheduled'
    """Runs from Cloud Scheduler in scheduled mode."""
    PER_PROCESS = 'per_process'
    """Reads the process it runs in (its memory, its loops), so every process keeps its own loop."""
    BOT = 'bot'
    """Needs a long-lived connection (a bot's websocket, Telegram long-polling) or
    that process's memory; stays a loop beside the bots until they move out."""


def worker_mode() -> WorkerMode:
    raw = (os.environ.get('WORKER_MODE') or '').strip().lower()
    return WorkerMode.SCHEDULED if raw == WorkerMode.SCHEDULED else WorkerMode.LOOP


def _always() -> bool:
    return True


def _racetime() -> bool:
    from application.utils.environment import racetime_bot_enabled
    return racetime_bot_enabled()


def _speedgaming() -> bool:
    from application.utils.environment import speedgaming_sync_enabled
    return speedgaming_sync_enabled()


def _discord_events() -> bool:
    from application.utils.environment import discord_events_sync_enabled
    return discord_events_sync_enabled()


def _telegram() -> bool:
    from application.utils.clients import telegram_client
    from application.utils.mocks.mock_discord import is_mock_discord
    return telegram_client.is_configured() and not is_mock_discord()


def _sentry_metrics() -> bool:
    from application.utils import sentry_metrics
    return sentry_metrics.enabled()


@dataclass(frozen=True)
class WorkerSpec:
    key: str
    module: str
    placement: Placement = Placement.SCHEDULED
    enabled: Callable[[], bool] = _always
    boot: Optional[str] = None
    """A module function every process calls at startup in either mode
    (an event-bus subscription that feeds the worker)."""

    def load(self) -> ModuleType:
        return importlib.import_module(self.module)

    def loop(self) -> BackgroundLoop:
        loop = getattr(self.load(), '_loop', None)
        if not isinstance(loop, BackgroundLoop):
            raise RuntimeError(f'{self.module} has no module-level _loop = run_worker_loop(...)')
        return loop

    def runs_in_process(self, mode: WorkerMode) -> bool:
        return mode is WorkerMode.LOOP or self.placement is not Placement.SCHEDULED


def _spec(key: str, module: str, **kwargs) -> WorkerSpec:
    return WorkerSpec(key, module, **kwargs)


# Order is start order; stop runs in reverse.
WORKERS: Dict[str, WorkerSpec] = {s.key: s for s in (
    _spec('race_room', 'application.services.race_room_worker', placement=Placement.BOT, enabled=_racetime),
    _spec('speedgaming_sync', 'application.services.speedgaming_sync_worker', enabled=_speedgaming),
    _spec('discord_event', 'application.services.discord.discord_event_worker', enabled=_discord_events),
    _spec('service_health', 'application.services.service_health_worker', placement=Placement.BOT),
    _spec('async_qualifier', 'application.services.async_qualifier.async_qualifier_worker'),
    # Leave ungated: gating it would strand rolls already in flight across a restart.
    _spec('seed_roll', 'application.services.seed_roll_worker'),
    _spec('check_in_sync', 'application.services.check_in_sync_worker'),
    _spec('sglive_pool', 'application.services.sglive_pool_worker'),
    _spec('startgg_sync', 'application.services.startgg_sync_worker'),
    _spec('challonge_sync', 'application.services.challonge_sync_worker'),
    _spec('data_export', 'application.services.exports.data_export_worker'),
    _spec('discord_role_sync', 'application.services.discord.discord_role_sync_worker', boot='subscribe'),
    _spec('telegram_bot', 'application.services.telegram_bot_worker', placement=Placement.BOT, enabled=_telegram),
    _spec('volunteer_reminder', 'application.services.volunteer.volunteer_reminder'),
    _spec('stage_reminder', 'application.services.match.stage_reminder'),
    _spec('community_event_reminder', 'application.services.community_event_reminder'),
    _spec('active_users_metrics', 'application.services.active_users_metrics', enabled=_sentry_metrics),
    _spec('telemetry_retention', 'application.services.telemetry_retention'),
    _spec('event_retention', 'application.services.event_retention'),
    _spec('metrics_sampler', 'application.utils.metrics_sampler',
          placement=Placement.PER_PROCESS, enabled=_sentry_metrics),
    _spec('worker_watchdog', 'application.utils.worker_watchdog', placement=Placement.PER_PROCESS),
)}

# Started by hand in main.py after boot and stopped last, so they watch the whole lifespan.
LIFESPAN_WATCHERS = frozenset({'metrics_sampler', 'worker_watchdog'})


def scheduled(key: str) -> Optional[WorkerSpec]:
    """The worker a scheduled tick may run under ``key``, or ``None``."""
    spec = WORKERS.get(key)
    if spec is None or spec.placement is not Placement.SCHEDULED:
        return None
    return spec


def start_in_process(mode: Optional[WorkerMode] = None) -> None:
    """Run every boot hook, and start the loops this process keeps in ``mode``."""
    mode = mode or worker_mode()
    for spec in WORKERS.values():
        if spec.key in LIFESPAN_WATCHERS:
            continue
        if spec.boot:
            getattr(spec.load(), spec.boot)()
        if spec.runs_in_process(mode) and spec.enabled():
            spec.load().start()


async def stop_all() -> None:
    """Stop every worker this process may have started; safe when none ran."""
    for spec in reversed(list(WORKERS.values())):
        if spec.key not in LIFESPAN_WATCHERS:
            await spec.load().stop()


def cron_schedule(interval: float) -> str:
    """The Cloud Scheduler cron for a worker's interval. Scheduler fires at most
    once a minute, so a faster worker gets every minute and bursts inside it."""
    minutes = max(1, round(interval / 60))
    if minutes >= 24 * 60:
        return '17 4 * * *'
    if minutes >= 60:
        return f'7 */{minutes // 60} * * *'
    return '* * * * *' if minutes == 1 else f'*/{minutes} * * * *'


@dataclass(frozen=True)
class TickResult:
    ticks: int
    failures: int


async def run_scheduled(spec: WorkerSpec, window: float) -> TickResult:
    """One scheduled call's work: a single tick, or for a worker faster than the
    Scheduler's one-minute floor, ticks at its own interval until ``window`` ends."""
    loop = spec.loop()
    deadline = time.monotonic() + window
    ticks = failures = 0
    while True:
        ok = await loop.run_once()
        ticks += 1
        failures += not ok
        if time.monotonic() + loop.interval >= deadline:
            return TickResult(ticks, failures)
        await asyncio.sleep(loop.interval)
