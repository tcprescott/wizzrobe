"""Data access for hosted ROM patches."""

from typing import List, Optional

from tortoise.expressions import F

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import scoped
from models import RomPatch


class RomPatchRepository(TenantScopedRepository[RomPatch]):
    model = RomPatch

    @classmethod
    async def list_all(cls) -> List[RomPatch]:
        return await scoped(RomPatch.all()).prefetch_related('tournament', 'uploaded_by').order_by('title', 'id')

    @classmethod
    async def get_by_token(cls, token: str) -> Optional[RomPatch]:
        return await scoped(RomPatch.filter(token=token)).prefetch_related('tournament', 'uploaded_by').first()


    @classmethod
    async def count_download(cls, patch_id: int) -> None:
        await scoped(RomPatch.filter(id=patch_id)).update(download_count=F('download_count') + 1)
