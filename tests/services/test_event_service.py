"""EventService: the default event, a community's first event, and scoping."""

import pytest

from application.event_context import event_scope
from application.repositories._event import event_scoped
from application.services.event_service import EventService
from application.services.tenant_service import TenantService
from application.tenant_context import tenant_scope
from models import Event, EventStatus, Role, Stage, Tenant, User, UserRole


@pytest.fixture
async def super_admin(db):
    su = await User.create(discord_id=1000, username='root')
    await UserRole.create(user=su, role=Role.SUPER_ADMIN, tenant=None)
    return su


async def test_default_event_id_is_the_baseline_event(db):
    default = await Event.get(tenant_id=1, is_default=True)
    assert await EventService.default_event_id(1) == default.id


async def test_default_event_id_is_none_without_events(bare_tenant_create):
    tenant = await bare_tenant_create(name='Empty', slug='empty')
    assert await EventService.default_event_id(tenant.id) is None


async def test_default_event_id_is_cached_until_an_event_is_written(bare_tenant_create):
    tenant = await bare_tenant_create(name='Later', slug='later')
    assert await EventService.default_event_id(tenant.id) is None
    await Event.create(tenant_id=tenant.id, name='Raw', slug='raw', is_default=True)
    assert await EventService.default_event_id(tenant.id) is not None


async def test_first_event_is_live_default_and_named_after_the_community(bare_tenant_create):
    tenant = await bare_tenant_create(name='Speed Week', slug='speed-week')
    event = await EventService.create_first_event(tenant)
    assert (event.name, event.slug) == ('Speed Week', 'speed-week')
    assert event.status == EventStatus.LIVE
    assert event.is_default
    assert await EventService.default_event_id(tenant.id) == event.id


async def test_first_event_refuses_a_second_time(db):
    tenant = await Tenant.get(id=1)
    with pytest.raises(ValueError, match='already has an event'):
        await EventService.create_first_event(tenant)


async def test_ensure_first_event_is_idempotent(bare_tenant_create):
    tenant = await bare_tenant_create(name='Twice', slug='twice')
    first = await EventService.ensure_first_event(tenant)
    again = await EventService.ensure_first_event(tenant)
    assert first.id == again.id
    assert await Event.filter(tenant_id=tenant.id).count() == 1


async def test_creating_a_community_creates_its_first_event(super_admin, bare_tenant_create, monkeypatch):
    monkeypatch.setattr(Tenant, 'create', bare_tenant_create)
    tenant = await TenantService.create_tenant(super_admin, name='New Crew', slug='new-crew')
    event = await Event.get(tenant_id=tenant.id)
    assert event.is_default and event.status == EventStatus.LIVE
    assert await EventService.default_event_id(tenant.id) == event.id


async def test_list_events_stays_inside_the_community(two_tenants):
    tenant_a, tenant_b = two_tenants
    with tenant_scope(tenant_b.id):
        slugs = [e.slug for e in await EventService.list_events()]
    assert slugs == ['tenant-b']
    with tenant_scope(tenant_a.id):
        assert await EventService.get_by_slug('tenant-b') is None


async def test_event_scoped_filters_on_community_and_event(two_tenants):
    tenant_a, tenant_b = two_tenants
    event_a = await Event.get(tenant_id=tenant_a.id, is_default=True)
    event_b = await Event.get(tenant_id=tenant_b.id, is_default=True)
    other_a = await Event.create(tenant_id=tenant_a.id, name='Other', slug='other')
    await Stage.create(tenant_id=tenant_a.id, event_id=event_a.id, name='Main')
    await Stage.create(tenant_id=tenant_a.id, event_id=other_a.id, name='Side')
    await Stage.create(tenant_id=tenant_b.id, event_id=event_b.id, name='B main')

    with tenant_scope(tenant_a.id), event_scope(event_a.id):
        assert [s.name for s in await event_scoped(Stage.all())] == ['Main']
        assert [s.name for s in await event_scoped(Stage.all(), other_a.id)] == ['Side']
    with tenant_scope(tenant_a.id), event_scope(event_b.id):
        assert await event_scoped(Stage.all()) == []


async def test_event_scoped_raises_without_an_event(db, monkeypatch):
    from application import event_context

    monkeypatch.setattr(event_context, '_fallback_event_id', lambda _tenant_id: None)
    with event_scope(None), pytest.raises(RuntimeError, match='No event in context'):
        event_scoped(Stage.all())
