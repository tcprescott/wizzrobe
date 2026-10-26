"""Stream layouts, their art and stage overlays stay inside their community."""

from io import BytesIO

import pytest
from PIL import Image

from application.errors import NotFoundError
from application.event_context import event_scope
from application.repositories import StreamLayoutRepository
from application.services import StreamLayoutService
from application.tenant_context import tenant_scope
from models import Event, Role, Stage, StageOverlay, StreamLayout, StreamLayoutArt, UserRole
from tests.factories import make_user


def _png() -> bytes:
    out = BytesIO()
    Image.new('RGBA', (640, 360), (0, 0, 0, 0)).save(out, 'PNG')
    return out.getvalue()


async def test_stream_layouts_do_not_leak_across_tenants(two_tenants):
    a, b = two_tenants
    staff = await make_user(discord_id=771, username='staff-in-both')
    for tenant in (a, b):
        await UserRole.create(user=staff, role=Role.STAFF, tenant_id=tenant.id)
    service = StreamLayoutService()
    ev_a = await Event.get(tenant_id=a.id, is_default=True)
    ev_b = await Event.get(tenant_id=b.id, is_default=True)

    with tenant_scope(a.id), event_scope(ev_a.id):
        layout = await service.create_layout(staff, 'Race')
        layout, _ = await service.set_art(staff, layout.id, _png())
        stage = await Stage.create(name='Main Stage', tenant_id=a.id, event_id=ev_a.id)
        await service.set_stage_layout(staff, stage.id, layout.id)

    with tenant_scope(b.id), event_scope(ev_b.id):
        assert await service.list_layouts(staff) == []
        assert [row.stage.id for row in await service.stage_overlays(staff)] == []
        with pytest.raises(NotFoundError):
            await service.get_layout(staff, layout.id)
        with pytest.raises((NotFoundError, ValueError)):
            await service.set_stage_layout(staff, stage.id, layout.id)
        assert await StreamLayoutRepository.get_art(b.id, layout.id, layout.art_sha256) is None
        assert await StreamLayoutService.art_bytes(b.id, layout.id, layout.art_sha256) is None

    assert (await StreamLayout.get(id=layout.id)).tenant_id == a.id
    assert (await StreamLayoutArt.get(layout_id=layout.id)).tenant_id == a.id
    assert (await StageOverlay.get(stage_id=stage.id)).tenant_id == a.id
