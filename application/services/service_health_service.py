"""Platform external-service health monitor (PR 5).

Surfaces the live health of every external dependency the app leans on — the
database, the Discord bot + OAuth, racetime bots, the SpeedGaming feed, Challonge
(reachability **and** token expiry), Twitch OAuth, the seed-generator upstreams,
web-push/VAPID, and Sentry — so a platform admin learns something is down *before*
it breaks a race day.

**Computed-and-cached, no persistence** (per the plan's decisions log): each probe
is an async ``check()`` returning a status + message; the latest result per
dependency lives in an in-memory cache, refreshed by a background worker on a
cadence and on demand from the ``/platform`` board. There is deliberately no
history model — a restart simply re-probes. The one persisted input is
``RacetimeBot.status``, which the runtime already writes; the racetime probe just
reads it.

The module-level cache is process-global on purpose: a single worker owns writes,
so there is no cross-user-state hazard (unlike per-user page state, which must
never live at module level).

**Alerting:** when a dependency transitions *into* an unhealthy state (``down`` or
a credential warning), the monitor publishes a platform-level
``SERVICE_HEALTH_ALERT`` event, captures a message to Sentry, and — when
``SERVICE_HEALTH_ALERT_DM`` is set — DMs every super-admin.

Scope: the full board is SUPER_ADMIN-only; a tenant's STAFF get a **read-only
subset** (:meth:`ServiceHealthService.tenant_subset`) covering just the services
their tenant depends on (its authorized racetime bots and its Challonge
connection).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from application.utils.timezone import to_utc_aware
from models import FeatureFlag, User

logger = logging.getLogger(__name__)

# A probe gets a hard ceiling so one hung dependency can't stall the whole board.
PROBE_TIMEOUT_SECONDS = 8.0
HTTP_TIMEOUT_SECONDS = 5.0
# Warn this far ahead of a Challonge token's expiry (a refresh should have long
# since happened; if it hasn't, staff need to reconnect before it hard-fails).
CHALLONGE_EXPIRY_WARNING = timedelta(days=3)


class ServiceStatus(str, Enum):
    """Health of one probed dependency.

    ``CREDENTIAL_WARNING`` is the distinct "still up, but a credential is expiring
    or was rejected" signal (an expiring Challonge token, a racetime auth failure)
    — actionable before it becomes an outage, so it is kept separate from ``DOWN``.
    """

    HEALTHY = 'healthy'
    DEGRADED = 'degraded'
    CREDENTIAL_WARNING = 'credential_warning'
    DOWN = 'down'
    UNKNOWN = 'unknown'


# Higher = worse; drives aggregation ("worst wins") and sort order on the board.
_SEVERITY: Dict[ServiceStatus, int] = {
    ServiceStatus.HEALTHY: 0,
    ServiceStatus.UNKNOWN: 1,
    ServiceStatus.CREDENTIAL_WARNING: 2,
    ServiceStatus.DEGRADED: 3,
    ServiceStatus.DOWN: 4,
}

# Transitioning *into* one of these fires an alert.
_ALERTABLE = frozenset({ServiceStatus.DOWN, ServiceStatus.CREDENTIAL_WARNING})


@dataclass(frozen=True)
class ProbeResult:
    """One dependency's latest probe outcome (rendered on the board + alerts)."""

    key: str
    label: str
    category: str
    status: ServiceStatus
    message: str
    checked_at: datetime

    def as_dict(self) -> Dict[str, object]:
        return {
            'key': self.key,
            'label': self.label,
            'category': self.category,
            'status': self.status.value,
            'message': self.message,
            'checked_at': self.checked_at.isoformat(),
        }


# A probe is an async callable returning (status, message). The registry pairs it
# with display metadata.
ProbeCheck = Callable[[], Awaitable[Tuple[ServiceStatus, str]]]


@dataclass(frozen=True)
class _Probe:
    key: str
    label: str
    category: str
    check: ProbeCheck


# Process-global cache: latest ProbeResult per probe key. Owned by the worker /
# on-demand refresh; readers get a snapshot.
_CACHE: Dict[str, ProbeResult] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _all_set(*names: str) -> bool:
    return all((os.environ.get(name) or '').strip() for name in names)


def _worst(statuses: List[ServiceStatus]) -> ServiceStatus:
    if not statuses:
        return ServiceStatus.UNKNOWN
    return max(statuses, key=lambda s: _SEVERITY[s])


# --------------------------------------------------------------------------- probes

async def _http_reachable(url: str) -> Tuple[bool, str]:
    """True when ``url`` answers at all (any HTTP status = the host is up)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.get(url)
        return True, f'HTTP {resp.status_code}'
    except Exception as e:  # DNS / TLS / connect / timeout
        return False, f'{type(e).__name__}: {e}'


async def _probe_postgres() -> Tuple[ServiceStatus, str]:
    from tortoise import connections
    try:
        await connections.get('default').execute_query('SELECT 1')
        return ServiceStatus.HEALTHY, 'Query round-trip OK'
    except Exception as e:
        return ServiceStatus.DOWN, f'Query failed: {e}'


async def _probe_discord_bot() -> Tuple[ServiceStatus, str]:
    from application.utils.mocks.mock_discord import is_mock_discord
    if is_mock_discord():
        return ServiceStatus.HEALTHY, 'Mock transport (MOCK_DISCORD)'
    if not _all_set('DISCORD_TOKEN'):
        return ServiceStatus.UNKNOWN, 'DISCORD_TOKEN not set'
    from application.services.discord.discord_service import DiscordService
    service = DiscordService()
    bot = service.get_bot() if hasattr(service, 'get_bot') else None
    if bot is None:
        return ServiceStatus.DOWN, 'Bot runtime not started'
    if bot.is_ready():
        return ServiceStatus.HEALTHY, f'Connected as {bot.user}' if bot.user else 'Connected'
    return ServiceStatus.DOWN, 'Gateway not ready (disconnected)'


async def _probe_discord_oauth() -> Tuple[ServiceStatus, str]:
    from application.utils.mocks.mock_discord import is_mock_discord
    if is_mock_discord():
        return ServiceStatus.HEALTHY, 'Mock OAuth (MOCK_DISCORD)'
    if _all_set('DISCORD_CLIENT_ID', 'DISCORD_CLIENT_SECRET'):
        return ServiceStatus.HEALTHY, 'Client credentials configured'
    return ServiceStatus.DOWN, 'DISCORD_CLIENT_ID / DISCORD_CLIENT_SECRET not set'


async def _probe_twitch_oauth() -> Tuple[ServiceStatus, str]:
    from application.utils.mocks.mock_twitch import is_mock_twitch
    if is_mock_twitch():
        return ServiceStatus.HEALTHY, 'Mock OAuth (MOCK_TWITCH)'
    if _all_set('TWITCH_CLIENT_ID', 'TWITCH_CLIENT_SECRET'):
        return ServiceStatus.HEALTHY, 'Client credentials configured'
    return ServiceStatus.UNKNOWN, 'Twitch OAuth not configured (optional)'


async def _probe_racetime() -> Tuple[ServiceStatus, str]:
    from application.services.racetime_bot_service import RacetimeBotService
    bots = await RacetimeBotService().list_active_bots()
    if not bots:
        return ServiceStatus.UNKNOWN, 'No active racetime bots configured'
    statuses: List[ServiceStatus] = []
    issues: List[str] = []
    for bot in bots:
        status = _map_racetime_status(bot.status, bot.status_message)
        statuses.append(status)
        if status in _ALERTABLE:
            issues.append(f'{bot.category} ({_status_value(bot.status)})')
    worst = _worst(statuses)
    if issues:
        return worst, f'{len(bots)} bot(s); needs attention: {", ".join(issues)}'
    return worst, f'{len(bots)} bot(s), all healthy'


def _status_value(status: object) -> str:
    """The clean string value of a status enum member (str-Enum ``__str__`` is ugly)."""
    return getattr(status, 'value', str(status))


def _map_racetime_status(status: str, message: Optional[str]) -> ServiceStatus:
    """Map a ``RacetimeBot.status`` (+ message) onto a health status.

    The health enum has no ``auth_failed`` member — an auth rejection is recorded
    as ``ERROR`` with the reason in ``status_message`` (the runtime carries the
    ``auth_failed`` flag on the audit detail). So an ``error`` whose message reads
    as an auth failure is surfaced as the distinct credential-warning signal.
    """
    value = (status or '').lower()
    if value == 'connected':
        return ServiceStatus.HEALTHY
    if value == 'disconnected':
        return ServiceStatus.DOWN
    if value == 'error':
        if message and 'auth' in message.lower():
            return ServiceStatus.CREDENTIAL_WARNING
        return ServiceStatus.DOWN
    return ServiceStatus.UNKNOWN


async def _probe_speedgaming() -> Tuple[ServiceStatus, str]:
    from application.utils.clients.speedgaming_client import SPEEDGAMING_BASE, is_mock_speedgaming
    if is_mock_speedgaming():
        return ServiceStatus.HEALTHY, 'Mock feed (MOCK_SPEEDGAMING)'
    ok, detail = await _http_reachable(SPEEDGAMING_BASE)
    if ok:
        return ServiceStatus.HEALTHY, f'Reachable ({detail})'
    return ServiceStatus.DOWN, f'Unreachable: {detail}'


async def _probe_challonge() -> Tuple[ServiceStatus, str]:
    """Reachability of the Challonge API **and** token validity across tenants."""
    from application.utils.mocks.mock_challonge import is_mock_challonge
    reach_status = ServiceStatus.HEALTHY
    reach_msg = 'Mock API (MOCK_CHALLONGE)'
    if not is_mock_challonge():
        ok, detail = await _http_reachable('https://api.challonge.com/v1')
        reach_status = ServiceStatus.HEALTHY if ok else ServiceStatus.DOWN
        reach_msg = f'API {"reachable" if ok else "unreachable"} ({detail})'

    cred_status, cred_msg = await _challonge_token_health()
    status = _worst([reach_status, cred_status])
    return status, '; '.join(m for m in (reach_msg, cred_msg) if m)


async def _challonge_token_health() -> Tuple[ServiceStatus, str]:
    """Scan every tenant's Challonge connection for an expired/expiring token."""
    from application.repositories import ChallongeRepository
    connections = await ChallongeRepository.list_all_connections()
    if not connections:
        return ServiceStatus.UNKNOWN, 'no tenant connections'
    now = _now()
    expired, warning = 0, 0
    for conn in connections:
        expires = conn.token_expires_at
        if expires is None:
            continue
        expires = to_utc_aware(expires)
        if expires <= now:
            expired += 1
        elif expires <= now + CHALLONGE_EXPIRY_WARNING:
            warning += 1
    total = len(connections)
    if expired:
        return ServiceStatus.DOWN, f'{expired}/{total} token(s) expired'
    if warning:
        return ServiceStatus.CREDENTIAL_WARNING, f'{warning}/{total} token(s) expiring soon'
    return ServiceStatus.HEALTHY, f'{total} token(s) valid'


def _seedgen_probe(host: str) -> ProbeCheck:
    async def check() -> Tuple[ServiceStatus, str]:
        ok, detail = await _http_reachable(f'https://{host}')
        if ok:
            return ServiceStatus.HEALTHY, f'Reachable ({detail})'
        return ServiceStatus.DOWN, f'Unreachable: {detail}'
    return check


async def _probe_web_push() -> Tuple[ServiceStatus, str]:
    if _all_set('VAPID_PRIVATE_KEY', 'VAPID_SUBJECT'):
        return ServiceStatus.HEALTHY, 'VAPID configured'
    if _all_set('VAPID_PRIVATE_KEY'):
        return ServiceStatus.CREDENTIAL_WARNING, 'VAPID_PRIVATE_KEY set but VAPID_SUBJECT missing'
    return ServiceStatus.UNKNOWN, 'Web push not configured (optional)'


async def _probe_sentry() -> Tuple[ServiceStatus, str]:
    if _all_set('SENTRY_DSN'):
        return ServiceStatus.HEALTHY, 'DSN configured'
    return ServiceStatus.UNKNOWN, 'Sentry not configured (optional)'


# --------------------------------------------------------------------------- registry

_PROBES: List[_Probe] = [
    _Probe('postgres', 'PostgreSQL', 'core', _probe_postgres),
    _Probe('discord_bot', 'Discord bot', 'discord', _probe_discord_bot),
    _Probe('discord_oauth', 'Discord OAuth', 'discord', _probe_discord_oauth),
    _Probe('racetime_bots', 'Racetime bots', 'racetime', _probe_racetime),
    _Probe('speedgaming', 'SpeedGaming feed', 'integrations', _probe_speedgaming),
    _Probe('challonge', 'Challonge', 'integrations', _probe_challonge),
    _Probe('twitch_oauth', 'Twitch OAuth', 'integrations', _probe_twitch_oauth),
    _Probe('seedgen_alttpr', 'ALTTPR (alttpr.com)', 'seedgen', _seedgen_probe('alttpr.com')),
    _Probe('seedgen_ootr', 'OoT Randomizer (ootrandomizer.com)', 'seedgen', _seedgen_probe('ootrandomizer.com')),
    _Probe('seedgen_maprando', 'Map Rando (maprando.com)', 'seedgen', _seedgen_probe('maprando.com')),
    _Probe('web_push', 'Web push (VAPID)', 'monitoring', _probe_web_push),
    _Probe('sentry', 'Sentry', 'monitoring', _probe_sentry),
]

_PROBE_BY_KEY: Dict[str, _Probe] = {p.key: p for p in _PROBES}


class ServiceHealthService:
    """Run and cache dependency probes; alert on unhealthy transitions."""

    async def _run_probe(self, probe: _Probe) -> ProbeResult:
        try:
            status, message = await asyncio.wait_for(probe.check(), timeout=PROBE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            status, message = ServiceStatus.DOWN, f'Probe timed out after {PROBE_TIMEOUT_SECONDS:.0f}s'
        except Exception as e:  # a probe must never take the board down
            logger.exception('Health probe %s raised', probe.key)
            status, message = ServiceStatus.DOWN, f'Probe error: {e}'
        return ProbeResult(probe.key, probe.label, probe.category, status, message, _now())

    async def refresh(self, *, alert: bool = True) -> List[ProbeResult]:
        """Run every probe concurrently, update the cache, and alert on transitions."""
        results = await asyncio.gather(*(self._run_probe(p) for p in _PROBES))
        for result in results:
            previous = _CACHE.get(result.key)
            _CACHE[result.key] = result
            if alert and _is_alert_transition(previous, result):
                await self._alert(previous, result)
        return sorted(results, key=_board_sort_key)

    def snapshot(self) -> List[ProbeResult]:
        """Cached results (UNKNOWN placeholders for probes not yet run)."""
        results = []
        for probe in _PROBES:
            cached = _CACHE.get(probe.key)
            results.append(cached or ProbeResult(
                probe.key, probe.label, probe.category,
                ServiceStatus.UNKNOWN, 'Not yet probed', _now(),
            ))
        return sorted(results, key=_board_sort_key)

    async def tenant_subset(self, tenant_id: int, *, actor: Optional[User]) -> List[ProbeResult]:
        """The read-only subset a tenant's STAFF may see: its authorized racetime
        bots and its own Challonge connection. Probed live (not from the platform
        cache) so it reflects only this tenant's dependencies.

        Assumes the caller has established this tenant's scope, so the Challonge
        read resolves to the tenant's own connection.
        """
        from application.services.auth_service import AuthService

        await AuthService.ensure(
            await AuthService.is_staff(actor),
            'Only Staff can read service health.',
        )
        results: List[ProbeResult] = []

        from application.services.racetime_bot_service import RacetimeBotService
        bots = await RacetimeBotService().list_authorized_for_tenant(tenant_id)
        if bots:
            statuses = [_map_racetime_status(b.status, b.status_message) for b in bots]
            issues = [f'{b.category} ({_status_value(b.status)})' for b, s in zip(bots, statuses, strict=False) if s in _ALERTABLE]
            message = (f'{len(bots)} bot(s); needs attention: {", ".join(issues)}'
                       if issues else f'{len(bots)} authorized bot(s), all healthy')
            results.append(ProbeResult(
                'racetime_bots', 'Racetime bots', 'racetime', _worst(statuses), message, _now(),
            ))

        # A health board over the tenant's dependencies, not a Challonge surface:
        # a community without the feature has no connection to report, and the
        # gated service would refuse the probe rather than answer "not connected".
        from application.services.challonge_service import ChallongeService
        from application.services.feature_flag_service import FeatureFlagService
        if not await FeatureFlagService().is_enabled(FeatureFlag.CHALLONGE):
            return results
        connection = await ChallongeService().get_connection_status()
        if connection.get('connected'):
            status, message = _tenant_challonge_status(connection.get('token_expires_at'))
            results.append(ProbeResult(
                'challonge', 'Challonge', 'integrations', status, message, _now(),
            ))
        return results

    async def _alert(self, previous: Optional[ProbeResult], result: ProbeResult) -> None:
        """Publish an event, capture to Sentry, and optionally DM super-admins."""
        from application.events import Event, EventType, event_bus
        prior = previous.status.value if previous else 'unknown'
        payload = {
            'key': result.key,
            'label': result.label,
            'status': result.status.value,
            'previous_status': prior,
            'message': result.message,
        }
        event_bus.publish(Event.create(EventType.SERVICE_HEALTH_ALERT, payload))

        summary = f'Service health: {result.label} → {result.status.value} ({result.message})'
        try:
            import sentry_sdk
            level = 'error' if result.status == ServiceStatus.DOWN else 'warning'
            # The message carries the probe's own words, which vary run to run;
            # fingerprint on probe + status so one outage is one Sentry issue.
            with sentry_sdk.new_scope() as scope:
                scope.set_tag('health_probe', result.key)
                scope.set_tag('health_status', result.status.value)
                scope.fingerprint = ['service-health', result.key, result.status.value]
                sentry_sdk.capture_message(summary, level=level)
        except Exception:
            logger.exception('Sentry capture failed for health alert %s', result.key)

        logger.warning('%s (was %s)', summary, prior)
        await self._maybe_dm_super_admins(summary)

    async def _maybe_dm_super_admins(self, summary: str) -> None:
        from application.utils.environment import service_health_alert_dm_enabled
        if not service_health_alert_dm_enabled():
            return
        try:
            from application.services.discord.discord_service import DiscordService
            from models import Role, UserRole
            rows = await UserRole.filter(role=Role.SUPER_ADMIN, tenant=None).prefetch_related('user')
            service = DiscordService()
            for row in rows:
                discord_id = getattr(row.user, 'discord_id', None)
                if discord_id:
                    await service.send_dm(discord_id, f'⚠️ {summary}')
        except Exception:
            logger.exception('Failed to DM super-admins for health alert')


def _is_alert_transition(previous: Optional[ProbeResult], result: ProbeResult) -> bool:
    """Alert when a probe newly enters (or moves between) alertable states."""
    if result.status not in _ALERTABLE:
        return False
    return previous is None or previous.status != result.status


def _board_sort_key(result: ProbeResult) -> Tuple[int, str]:
    # Worst first, then stable by label.
    return (-_SEVERITY[result.status], result.label)


def _tenant_challonge_status(token_expires_at: Optional[datetime]) -> Tuple[ServiceStatus, str]:
    if token_expires_at is None:
        return ServiceStatus.HEALTHY, 'Connected (no token expiry)'
    expires = to_utc_aware(token_expires_at)
    now = _now()
    if expires <= now:
        return ServiceStatus.DOWN, 'Token expired — reconnect required'
    if expires <= now + CHALLONGE_EXPIRY_WARNING:
        return ServiceStatus.CREDENTIAL_WARNING, 'Token expiring soon'
    return ServiceStatus.HEALTHY, 'Connected, token valid'


def reset_cache() -> None:
    """Clear the in-memory cache (tests / a forced cold restart)."""
    _CACHE.clear()
