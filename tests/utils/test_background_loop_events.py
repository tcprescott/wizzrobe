"""Workers run for live events only, each item inside its own event."""

import pytest

from application.event_context import get_current_event_id
from application.tenant_context import get_current_tenant_id
from application.utils.background_loop import for_each_tenant_scoped
from models import Event, EventStatus, Tenant


async def _event(tenant_id: int, slug: str, status: EventStatus) -> Event:
    return await Event.create(tenant_id=tenant_id, name=slug, slug=slug, status=status, is_default=False)


async def test_an_item_runs_in_its_own_event_and_skips_draft_and_archived(db):
    live = await _event(1, 'sgl27', EventStatus.LIVE)
    draft = await _event(1, 'sgl28', EventStatus.DRAFT)
    archived = await _event(1, 'sgl25', EventStatus.ARCHIVED)
    seen: list = []

    async def handle(event_id: int) -> None:
        seen.append(get_current_event_id())

    await for_each_tenant_scoped(
        [live.id, draft.id, archived.id], handle,
        tenant_id_of=lambda _: 1, event_id_of=lambda e: e,
    )

    assert seen == [live.id]


async def test_a_community_runs_once_per_live_event(db):
    second = await _event(1, 'sgl27', EventStatus.LIVE)
    await _event(1, 'sgl25', EventStatus.ARCHIVED)
    other = await Tenant.create(name='Other', slug='other')
    seen: list = []

    async def handle(tenant_id: int) -> None:
        seen.append((get_current_tenant_id(), get_current_event_id()))

    await for_each_tenant_scoped([1, other.id], handle, tenant_id_of=lambda t: t, each_live_event=True)

    other_live = await Event.filter(tenant_id=other.id, status=EventStatus.LIVE).values_list('id', flat=True)
    assert seen == [(1, 1), (1, second.id), *[(other.id, e) for e in other_live]]


async def test_a_community_with_no_live_event_gets_no_run(db):
    await Event.filter(tenant_id=1).update(status=EventStatus.ARCHIVED)
    seen: list = []

    async def handle(tenant_id: int) -> None:
        seen.append(tenant_id)

    await for_each_tenant_scoped([1], handle, tenant_id_of=lambda t: t, each_live_event=True)

    assert seen == []


async def test_the_two_event_options_are_exclusive(db):
    async def handle(_: int) -> None:
        pass

    with pytest.raises(ValueError):
        await for_each_tenant_scoped(
            [1], handle, tenant_id_of=lambda t: t, event_id_of=lambda t: t, each_live_event=True,
        )
