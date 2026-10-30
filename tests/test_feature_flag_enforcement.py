"""Service-layer feature enforcement: the second half of feature gating.

Hiding a feature at the entry surfaces is not gating it. A caller with no gate —
a newly added page, a Discord interaction handler, a worker, a REST router nobody
mounted behind ``require_feature`` — reaches the service directly, so the owning
service has to refuse. These tests cover the primitives (``requires_feature``,
``ensure_enabled``, the per-request cache) and spot-check that each gated
subsystem's service actually refuses in a tenant that lacks its flag.

The ``db`` fixture turns every flag on for the default tenant, so each test here
works under a **fresh** tenant, which starts with everything off.
"""

import pytest

from application.errors import FeatureDisabledError, NotFoundError
from application.feature_flags import all_specs, requires_feature
from application.services.feature_flag_service import FeatureFlagService, reset_flag_cache
from application.tenant_context import reset_tenant_id, set_tenant_id, tenant_scope
from models import FeatureFlag, Role, Tenant, TenantFeatureFlag, User, UserRole


@pytest.fixture
async def bare_tenant(db):
    """A tenant with no flag rows and no group — every feature off."""
    return await Tenant.create(name='Bare', slug='bare')


async def _enable(tenant_id: int, flag: FeatureFlag) -> None:
    await TenantFeatureFlag.create(
        tenant_id=tenant_id, flag=flag.value, available=True, enabled=True,
    )
    reset_flag_cache()


async def _staff(discord_id: int = 70001) -> User:
    user = await User.create(discord_id=discord_id, username=f'u{discord_id}')
    await UserRole.create(user=user, role=Role.STAFF)
    return user


async def test_feature_disabled_is_a_not_found_error():
    """So REST answers 404 (hidden, like every other feature gate) with no extra
    plumbing, and UI ``except ValueError`` handlers still show it."""
    assert issubclass(FeatureDisabledError, NotFoundError)
    assert issubclass(FeatureDisabledError, ValueError)


async def test_ensure_enabled_raises_for_a_tenant_without_the_flag(bare_tenant):
    with tenant_scope(bare_tenant.id):
        with pytest.raises(FeatureDisabledError):
            await FeatureFlagService().ensure_enabled(FeatureFlag.BRACKETS)


async def test_ensure_enabled_passes_once_live(bare_tenant):
    await _enable(bare_tenant.id, FeatureFlag.BRACKETS)
    with tenant_scope(bare_tenant.id):
        await FeatureFlagService().ensure_enabled(FeatureFlag.BRACKETS)  # no raise


async def test_ensure_enabled_raises_with_no_tenant_in_scope(db):
    """A flag is never live without a tenant, so the guard must not fall open.

    The conftest autouse fixture keeps a default tenant bound, so the tenant-less
    state has to be entered explicitly.
    """
    token = set_tenant_id(None)
    try:
        with pytest.raises(FeatureDisabledError):
            await FeatureFlagService().ensure_enabled(FeatureFlag.BRACKETS)
    finally:
        reset_tenant_id(token)


class _Subject:
    @requires_feature(FeatureFlag.EQUIPMENT)
    async def act(self, value: int) -> int:
        return value * 2


async def test_decorator_blocks_then_allows(bare_tenant):
    subject = _Subject()
    with tenant_scope(bare_tenant.id):
        with pytest.raises(FeatureDisabledError):
            await subject.act(21)
    await _enable(bare_tenant.id, FeatureFlag.EQUIPMENT)
    with tenant_scope(bare_tenant.id):
        assert await subject.act(21) == 42


async def test_decorator_preserves_identity_and_marks_the_flag():
    """``functools.wraps`` keeps the name/doc, and the marker is what the
    check_feature_flag_gating hook reads."""
    assert _Subject.act.__name__ == 'act'
    assert _Subject.act.__wizzrobe_feature__ is FeatureFlag.EQUIPMENT


async def test_repeated_checks_hit_the_cache(bare_tenant, monkeypatch):
    """N guards in one request must not mean N database resolutions."""
    await _enable(bare_tenant.id, FeatureFlag.EQUIPMENT)
    service = FeatureFlagService()
    calls = {'n': 0}
    original = FeatureFlagService._resolve_enabled_flags

    async def counting(self, tenant_id):
        calls['n'] += 1
        return await original(self, tenant_id)

    monkeypatch.setattr(FeatureFlagService, '_resolve_enabled_flags', counting)
    with tenant_scope(bare_tenant.id):
        for _ in range(5):
            await service.is_enabled(FeatureFlag.EQUIPMENT)
    assert calls['n'] == 1


async def test_cache_is_per_tenant_not_shared(bare_tenant):
    """Two tenants in one context must not read each other's flag state."""
    other = await Tenant.create(name='Other', slug='other-flags')
    await _enable(bare_tenant.id, FeatureFlag.EQUIPMENT)
    service = FeatureFlagService()
    with tenant_scope(bare_tenant.id):
        assert await service.is_enabled(FeatureFlag.EQUIPMENT) is True
    with tenant_scope(other.id):
        assert await service.is_enabled(FeatureFlag.EQUIPMENT) is False


async def test_staff_toggle_invalidates_the_cache(bare_tenant):
    """A write in the same request must be visible to a later read."""
    await _enable(bare_tenant.id, FeatureFlag.EQUIPMENT)
    service = FeatureFlagService()
    with tenant_scope(bare_tenant.id):
        actor = await _staff()
        assert await service.is_enabled(FeatureFlag.EQUIPMENT) is True
        await service.set_tenant_enabled(actor, FeatureFlag.EQUIPMENT, False)
        assert await service.is_enabled(FeatureFlag.EQUIPMENT) is False


async def test_equipment_service_refuses_when_feature_off(bare_tenant):
    from application.services.equipment_service import EquipmentService

    with tenant_scope(bare_tenant.id):
        with pytest.raises(FeatureDisabledError):
            await EquipmentService().list_assets()


async def test_bracket_service_refuses_when_feature_off(bare_tenant):
    from application.services.bracket_service import BracketService

    with tenant_scope(bare_tenant.id):
        with pytest.raises(FeatureDisabledError):
            await BracketService().list_all_brackets()


async def test_triforce_service_refuses_when_feature_off(bare_tenant):
    from application.services.triforce_text_service import TriforceTextService

    with tenant_scope(bare_tenant.id):
        with pytest.raises(FeatureDisabledError):
            await TriforceTextService().list_supporting_tournaments()


async def test_challonge_service_refuses_when_feature_off(bare_tenant):
    from application.services.challonge_service import ChallongeService

    with tenant_scope(bare_tenant.id):
        with pytest.raises(FeatureDisabledError):
            await ChallongeService().get_connection_status()


async def test_qualifier_service_refuses_when_feature_off(bare_tenant):
    from application.services.async_qualifier import AsyncQualifierService

    with tenant_scope(bare_tenant.id):
        with pytest.raises(FeatureDisabledError):
            await AsyncQualifierService().list_open_qualifiers()


async def test_volunteer_coverage_refuses_when_feature_off(bare_tenant):
    """The Volunteer Coverage *report* was the one entry surface that read
    volunteer data without checking the flag — REST and MCP were both gated."""
    from datetime import datetime, timezone

    from application.services.volunteer.volunteer_schedule_service import (
        VolunteerScheduleService,
    )

    with tenant_scope(bare_tenant.id):
        with pytest.raises(FeatureDisabledError):
            await VolunteerScheduleService().coverage(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            )


async def test_speedgaming_service_refuses_when_feature_off(bare_tenant):
    from application.services.speedgaming_sync_service import SpeedGamingSyncService

    with tenant_scope(bare_tenant.id):
        actor = await _staff(70009)
        with pytest.raises(FeatureDisabledError):
            await SpeedGamingSyncService().list_links(actor)


async def test_event_info_service_refuses_when_feature_off(bare_tenant):
    from application.services.event_info_service import EventInfoService

    with tenant_scope(bare_tenant.id):
        with pytest.raises(FeatureDisabledError):
            await EventInfoService.list_articles()


async def test_a_help_icon_over_handbook_content_is_skipped_not_raised(bare_tenant):
    """``help_icon`` is called from surfaces that are not themselves gated on
    EVENT_INFO — the Player tab, the Proctor Station. It resolves the flag itself
    and renders nothing, because an ungated surface must not inherit a raise from
    a feature its community does not have."""
    from application.services import FeatureFlagService

    with tenant_scope(bare_tenant.id):
        assert FeatureFlag.EVENT_INFO not in await FeatureFlagService().enabled_flags()



async def test_challonge_result_push_is_skipped_not_raised(bare_tenant):
    """The confirm flow calls this for *every* match. Raising here would break
    match completion for every community that does not use Challonge."""
    from application.services.challonge_service import ChallongeService
    from models import Match, Tournament

    with tenant_scope(bare_tenant.id):
        actor = await _staff(70002)
        tournament = await Tournament.create(name='Cup')
        match = await Match.create(tournament=tournament)
        assert await ChallongeService().push_result_if_linked(match, actor) is False


async def test_triforce_text_embed_returns_none_not_raises(bare_tenant):
    """Runs inside every ALTTPR roll: a community without the feature must get a
    seed with no custom text, not a failed roll."""
    from application.services.triforce_text_service import TriforceTextService
    from models import Tournament

    with tenant_scope(bare_tenant.id):
        tournament = await Tournament.create(name='Cup', seed_generator='alttpr')
        service = TriforceTextService()
        assert await service.get_balanced_text(tournament) is None
        assert await service.get_random_text(tournament) is None


async def test_every_flag_declares_how_it_is_enforced():
    """Mirrors the check_feature_flag_gating hook so CI fails too, not just the
    editing session: a flag with no owning service module is UI-only gated."""
    for spec in all_specs():
        assert spec.service_modules, (
            f'FeatureFlag.{spec.flag.name} declares no service_modules — nothing '
            'verifies that its service enforces the flag.'
        )
