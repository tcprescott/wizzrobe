"""Tests for the platform service-health monitor (PR 5).

Covers the pure status logic (severity aggregation, racetime/Challonge mapping),
the cache + alert-on-transition path (driven through a monkeypatched probe set so
no probe touches the network), and the tenant read-only subset (a granted racetime
bot in error + a near-expiry Challonge token → down / credential-warning).
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.events import EventType, event_bus
from application.services import service_health_service as shs
from application.services.feature_flag_service import reset_flag_cache
from application.services.racetime_bot_service import RacetimeBotService
from application.services.service_health_service import (
    ProbeResult,
    ServiceHealthService,
    ServiceStatus,
    _is_alert_transition,
    _map_racetime_status,
    _tenant_challonge_status,
    _worst,
)
from application.tenant_context import tenant_scope
from models import (
    BotStatus,
    ChallongeConnection,
    FeatureFlag,
    Role,
    Tenant,
    TenantFeatureFlag,
    User,
    UserRole,
)


async def _platform_admin():
    """``tenant_subset`` is staff-only; a super admin is staff in every community."""
    from models import Role, User, UserRole

    user, _ = await User.get_or_create(discord_id=93001, defaults={'username': 'platform'})
    await UserRole.get_or_create(user=user, role=Role.SUPER_ADMIN, tenant=None)
    return user


def _result(key, status, *, label='X', category='core', message='') -> ProbeResult:
    return ProbeResult(key, label, category, status, message, datetime.now(timezone.utc))


class TestStatusLogic:
    def test_worst_picks_highest_severity(self):
        assert _worst([ServiceStatus.HEALTHY, ServiceStatus.DOWN]) == ServiceStatus.DOWN
        assert _worst([ServiceStatus.HEALTHY, ServiceStatus.CREDENTIAL_WARNING]) == ServiceStatus.CREDENTIAL_WARNING
        assert _worst([ServiceStatus.HEALTHY, ServiceStatus.UNKNOWN]) == ServiceStatus.UNKNOWN
        assert _worst([]) == ServiceStatus.UNKNOWN

    def test_racetime_status_mapping(self):
        assert _map_racetime_status(BotStatus.CONNECTED, None) == ServiceStatus.HEALTHY
        assert _map_racetime_status(BotStatus.DISCONNECTED, None) == ServiceStatus.DOWN
        assert _map_racetime_status(BotStatus.ERROR, 'boom') == ServiceStatus.DOWN
        assert _map_racetime_status(BotStatus.ERROR, 'auth rejected') == ServiceStatus.CREDENTIAL_WARNING
        assert _map_racetime_status(BotStatus.UNKNOWN, None) == ServiceStatus.UNKNOWN

    def test_tenant_challonge_status(self):
        now = datetime.now(timezone.utc)
        assert _tenant_challonge_status(None)[0] == ServiceStatus.HEALTHY
        assert _tenant_challonge_status(now - timedelta(hours=1))[0] == ServiceStatus.DOWN
        assert _tenant_challonge_status(now + timedelta(days=1))[0] == ServiceStatus.CREDENTIAL_WARNING
        assert _tenant_challonge_status(now + timedelta(days=30))[0] == ServiceStatus.HEALTHY

    def test_alert_transition_only_into_alertable_states(self):
        down = _result('k', ServiceStatus.DOWN)
        healthy = _result('k', ServiceStatus.HEALTHY)
        warn = _result('k', ServiceStatus.CREDENTIAL_WARNING)
        assert _is_alert_transition(None, down) is True          # first probe, down
        assert _is_alert_transition(healthy, down) is True       # healthy -> down
        assert _is_alert_transition(down, down) is False         # stayed down: no re-alert
        assert _is_alert_transition(down, warn) is True          # down -> warning (changed)
        assert _is_alert_transition(down, healthy) is False      # recovered: not alertable
        assert _is_alert_transition(None, healthy) is False      # first probe, healthy



class TestMockMode:
    async def test_mocked_discord_reports_mock(self, monkeypatch):
        monkeypatch.setenv('MOCK_DISCORD', 'true')
        assert (await shs._probe_discord_bot())[0] == ServiceStatus.MOCK
        assert (await shs._probe_discord_oauth())[0] == ServiceStatus.MOCK

    async def test_mocked_seedgen_reports_mock_without_contacting_the_site(self, monkeypatch):
        async def unreachable(url):
            raise AssertionError(f'probe contacted {url}')

        monkeypatch.setenv('MOCK_SEEDGEN', 'true')
        monkeypatch.setattr(shs, '_http_reachable', unreachable)
        assert (await shs._seedgen_probe('alttpr.com', 'alttpr')())[0] == ServiceStatus.MOCK

    async def test_randomizer_the_mock_skips_is_still_probed(self, monkeypatch):
        async def reachable(url):
            return True, 'HTTP 200'

        monkeypatch.setenv('MOCK_SEEDGEN', 'true')
        monkeypatch.setattr(shs, '_http_reachable', reachable)
        assert (await shs._seedgen_probe('example.com', 'z1r')())[0] == ServiceStatus.HEALTHY

    async def test_mocked_challonge_still_flags_an_expired_token(self, db, monkeypatch):
        async def expired():
            return ServiceStatus.DOWN, '1/1 token(s) expired'

        monkeypatch.setenv('MOCK_CHALLONGE', 'true')
        monkeypatch.setattr(shs, '_challonge_token_health', expired)
        assert (await shs._probe_challonge())[0] == ServiceStatus.DOWN

        async def none():
            return ServiceStatus.UNKNOWN, 'no tenant connections'

        monkeypatch.setattr(shs, '_challonge_token_health', none)
        assert (await shs._probe_challonge())[0] == ServiceStatus.MOCK

    def test_mock_ranks_between_healthy_and_unknown(self):
        assert _worst([ServiceStatus.HEALTHY, ServiceStatus.MOCK]) == ServiceStatus.MOCK
        assert _worst([ServiceStatus.MOCK, ServiceStatus.UNKNOWN]) == ServiceStatus.UNKNOWN
        assert _is_alert_transition(None, _result('k', ServiceStatus.MOCK)) is False


class TestRefreshAndAlert:
    @pytest.fixture(autouse=True)
    def _reset(self):
        shs.reset_cache()
        yield
        shs.reset_cache()

    async def test_refresh_populates_cache_and_snapshot(self, db, monkeypatch):
        async def ok():
            return ServiceStatus.HEALTHY, 'fine'

        monkeypatch.setattr(shs, '_PROBES', [shs._Probe('demo', 'Demo', 'core', ok)])
        service = ServiceHealthService()
        results = await service.refresh()
        assert [r.status for r in results] == [ServiceStatus.HEALTHY]
        assert [r.status for r in service.snapshot()] == [ServiceStatus.HEALTHY]

    async def test_snapshot_is_unknown_before_probe(self, db, monkeypatch):
        async def ok():
            return ServiceStatus.HEALTHY, 'fine'

        monkeypatch.setattr(shs, '_PROBES', [shs._Probe('demo', 'Demo', 'core', ok)])
        snap = ServiceHealthService().snapshot()
        assert snap[0].status == ServiceStatus.UNKNOWN
        assert snap[0].message == 'Not yet probed'

    async def test_probe_exception_becomes_down(self, db, monkeypatch):
        async def boom():
            raise RuntimeError('kaboom')

        monkeypatch.setattr(shs, '_PROBES', [shs._Probe('demo', 'Demo', 'core', boom)])
        results = await ServiceHealthService().refresh()
        assert results[0].status == ServiceStatus.DOWN
        assert 'kaboom' in results[0].message

    async def test_transition_to_down_publishes_alert(self, db, monkeypatch):
        state = {'status': ServiceStatus.HEALTHY}

        async def flip():
            return state['status'], 'msg'

        monkeypatch.setattr(shs, '_PROBES', [shs._Probe('demo', 'Demo', 'core', flip)])

        captured = []
        token = event_bus.subscribe_sync(
            lambda e: captured.append(e), [EventType.SERVICE_HEALTH_ALERT],
        )
        try:
            service = ServiceHealthService()
            await service.refresh()                 # healthy: no alert
            assert captured == []
            state['status'] = ServiceStatus.DOWN
            await service.refresh()                 # healthy -> down: one alert
            assert len(captured) == 1
            assert captured[0].payload['key'] == 'demo'
            assert captured[0].payload['status'] == 'down'
            assert captured[0].payload['previous_status'] == 'healthy'
            await service.refresh()                 # stayed down: no re-alert
            assert len(captured) == 1
        finally:
            event_bus.unsubscribe(token)


class TestTenantSubset:
    async def test_subset_reports_bot_error_and_expiring_token(self, db):
        tenant = await Tenant.get(id=1)
        su = await User.create(discord_id=9000, username='root')
        await UserRole.create(user=su, role=Role.SUPER_ADMIN, tenant=None)

        bot_service = RacetimeBotService()
        bot = await bot_service.create_bot(
            su, category='alttpr', client_id='cid', client_secret='sec', name='ALTTPR',
        )
        await bot_service.grant_tenant(su, bot.id, tenant.id)
        bot.status = BotStatus.ERROR
        bot.status_message = 'websocket closed'
        await bot.save()

        with tenant_scope(tenant.id):
            await ChallongeConnection.create(
                tenant=tenant, access_token='a', refresh_token='r',
                challonge_username='svc',
                token_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            subset = await ServiceHealthService().tenant_subset(tenant.id, actor=await _platform_admin())

        by_key = {r.key: r for r in subset}
        assert by_key['racetime_bots'].status == ServiceStatus.DOWN
        assert by_key['challonge'].status == ServiceStatus.CREDENTIAL_WARNING

    async def test_subset_empty_without_dependencies(self, db):
        tenant = await Tenant.get(id=1)
        with tenant_scope(tenant.id):
            subset = await ServiceHealthService().tenant_subset(tenant.id, actor=await _platform_admin())
        assert subset == []

    async def test_subset_skips_challonge_when_the_feature_is_off(self, db):
        """The board is read from the (ungated) admin Service Health tab, so a
        community without Challonge must get a report, not a FeatureDisabledError
        from the probe."""
        tenant = await Tenant.get(id=1)
        await TenantFeatureFlag.filter(
            tenant_id=tenant.id, flag=FeatureFlag.CHALLONGE.value,
        ).update(available=False, enabled=False)
        reset_flag_cache()

        with tenant_scope(tenant.id):
            await ChallongeConnection.create(
                tenant=tenant, access_token='a', refresh_token='r',
                challonge_username='svc',
                token_expires_at=datetime.now(timezone.utc) + timedelta(days=90),
            )
            subset = await ServiceHealthService().tenant_subset(tenant.id, actor=await _platform_admin())

        assert [r.key for r in subset] == []



async def test_tenant_subset_refuses_a_non_staff_member(db):
    from models import Tenant, User

    tenant = await Tenant.create(name='Health', slug='health-gate')
    member = await User.create(discord_id=93002, username='member')
    with tenant_scope(tenant.id):
        with pytest.raises(PermissionError):
            await ServiceHealthService().tenant_subset(tenant.id, actor=member)


class TestWorkerStartupGrace:
    async def test_tick_inside_the_startup_window_does_not_probe(self, monkeypatch):
        from application.services import service_health_worker as worker

        calls = []

        async def refresh(self, **kwargs):
            calls.append(1)
            return []

        monkeypatch.setattr(ServiceHealthService, 'refresh', refresh)
        monkeypatch.setattr(worker, '_started_at', worker.time.monotonic())
        await worker._tick()
        assert calls == []

        monkeypatch.setattr(worker, '_started_at', worker.time.monotonic() - worker.STARTUP_GRACE_SECONDS - 1)
        await worker._tick()
        assert calls == [1]
