"""RomPatch: one community's patches never show under another community."""

import pytest

from application.errors import NotFoundError
from application.repositories import RomPatchRepository
from application.services.rom_patch_service import RomPatchService
from application.tenant_context import tenant_scope


def _row(token: str) -> dict:
    return {'token': token, 'title': 'Practice', 'game': 'SM', 'format': 'ips', 'filename': 'p.ips', 'size': 8}


async def test_a_patch_is_only_found_in_its_own_community(two_tenants):
    a, b = two_tenants
    with tenant_scope(a.id):
        await RomPatchRepository.create(**_row('tok-a-patch'))
    with tenant_scope(b.id):
        assert await RomPatchRepository.get_by_token('tok-a-patch') is None
        assert await RomPatchRepository.list_all() == []
        with pytest.raises(NotFoundError):
            await RomPatchService.get_by_token('tok-a-patch')
        with pytest.raises(NotFoundError):
            await RomPatchService.record_download('tok-a-patch')
    with tenant_scope(a.id):
        assert [p.token for p in await RomPatchRepository.list_all()] == ['tok-a-patch']


async def test_another_community_cannot_edit_a_patch_by_id(two_tenants):
    a, b = two_tenants
    from application.services.user_service import UserService
    actor = await UserService().get_system_user()
    with tenant_scope(a.id):
        patch = await RomPatchRepository.create(**_row('tok-b-patch'))
    with tenant_scope(b.id):
        with pytest.raises(NotFoundError):
            await RomPatchService().update_patch(actor, patch.id, title='x', game='y')
        with pytest.raises(NotFoundError):
            await RomPatchService().delete_patch(actor, patch.id)
