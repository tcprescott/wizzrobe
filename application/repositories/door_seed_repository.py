"""Data access for hosted Door Randomizer seeds."""

from typing import Optional

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import scoped
from models import DoorSeed


class DoorSeedRepository(TenantScopedRepository[DoorSeed]):
    model = DoorSeed

    @classmethod
    async def get_by_token(cls, token: str) -> Optional[DoorSeed]:
        return await scoped(DoorSeed.filter(token=token)).first()

