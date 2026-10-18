"""A worker tick must not keep per-request caches from the tick before it."""

from application.services.feature_flag_service import FeatureFlagService
from application.tenant_context import tenant_scope
from application.utils.background_loop import BackgroundLoop
from models import FeatureFlag


async def test_each_tick_re_reads_feature_flags(monkeypatch):
    live = {FeatureFlag.VOLUNTEERS}
    seen: list = []

    async def resolve(_self, _tenant_id):
        return set(live)

    monkeypatch.setattr(FeatureFlagService, '_resolve_enabled_flags', resolve)

    async def tick() -> None:
        with tenant_scope(1):
            seen.append(await FeatureFlagService().is_enabled(FeatureFlag.VOLUNTEERS))

    loop = BackgroundLoop(tick, interval=60, name='test worker')
    await loop._run_tick()
    live.clear()
    await loop._run_tick()

    assert seen == [True, False]
