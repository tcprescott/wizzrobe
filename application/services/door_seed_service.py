"""
Door Seed Service - Business Logic Layer

Reads the Door Randomizer seeds Wizzrobe hosts (``SeedGenerationService`` rolls
them) for the patcher page and its downloads. Who may read a spoiler is the
rule here: ``public`` is anyone with the link, ``staff`` is this community's
staff (and super-admins), ``none`` is nobody, because it was never kept.
"""

import logging
import time
from typing import List, Optional

from application.errors import NotFoundError, require_found
from application.repositories.door_seed_repository import DoorSeedRepository
from application.services.auth_service import AuthService
from application.utils.clients import alttpr_sprites_client
from application.utils.door_seed_store import DoorSeedStore
from models import DoorSeed, User

logger = logging.getLogger(__name__)

SPRITE_LIST_TTL = 6 * 3600.0
SPRITE_LIST_RETRY = 300.0
# One copy for the whole process: it's the same public list for everyone.
_sprite_cache: dict = {'sprites': None, 'expires': 0.0}


class DoorSeedService:
    def __init__(self, store: Optional[DoorSeedStore] = None) -> None:
        self.store = store or DoorSeedStore()

    async def get_seed(self, token: str) -> DoorSeed:
        return require_found(await DoorSeedRepository.get_by_token(token), 'Seed')

    @staticmethod
    async def can_read_spoiler(seed: DoorSeed, user: Optional[User]) -> bool:
        if seed.spoiler_access == 'public':
            return True
        if seed.spoiler_access == 'staff':
            return await AuthService.is_staff(user)
        return False

    async def spoiler_for(self, seed: DoorSeed, user: Optional[User]) -> bytes:
        if not await self.can_read_spoiler(seed, user):
            raise NotFoundError('Spoiler not found.')
        return require_found(await self.store.get_spoiler(seed.token), 'Spoiler')

    async def spoiler_by_token(self, token: str, user: Optional[User]) -> bytes:
        return await self.spoiler_for(await self.get_seed(token), user)

    async def patch(self, token: str) -> bytes:
        seed = await self.get_seed(token)
        return require_found(await self.store.get_patch(seed.token), 'Seed patch')

    async def base_patch(self, version: str) -> bytes:
        try:
            data = await self.store.get_base_patch(version)
        except ValueError:
            data = None
        return require_found(data, 'Base patch')

    @staticmethod
    async def sprite_list() -> List[dict]:
        """The alttpr.com sprites, cached; a stale copy (or none) if alttpr.com is down."""
        now = time.monotonic()
        if _sprite_cache['sprites'] is not None and now < _sprite_cache['expires']:
            return _sprite_cache['sprites']
        try:
            sprites = await alttpr_sprites_client.fetch_sprite_list()
        except alttpr_sprites_client.SpriteListError as e:
            logger.warning('Could not refresh the alttpr.com sprite list: %s', e)
            _sprite_cache['expires'] = now + SPRITE_LIST_RETRY
            return _sprite_cache['sprites'] or []
        _sprite_cache.update(sprites=sprites, expires=now + SPRITE_LIST_TTL)
        return sprites
