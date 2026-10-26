"""Event Service: a community's events and which one is its default.

A community holds events (one SGL weekend each); bare links resolve to the
community's default event, and every community has at least one event.

The default event id is cached per process, keyed by tenant, because the page
middleware resolves it on every request. Safe under the single-worker
deployment; every write here drops the cache.
"""

from typing import List, Optional

from application.repositories.event_repository import EventRepository
from models import Event, EventStatus, Tenant

_default_id_cache: dict[int, Optional[int]] = {}
_CACHE_MAX = 2048


def _clear_cache() -> None:
    _default_id_cache.clear()


class EventService:

    @staticmethod
    async def default_event_id(tenant_id: int) -> Optional[int]:
        """The community's default event id, or None when it has no events yet."""
        if tenant_id in _default_id_cache:
            return _default_id_cache[tenant_id]
        event_id = await EventRepository.default_id_for_tenant(tenant_id)
        if len(_default_id_cache) >= _CACHE_MAX:
            _default_id_cache.pop(next(iter(_default_id_cache)), None)
        _default_id_cache[tenant_id] = event_id
        return event_id

    @staticmethod
    async def list_events() -> List[Event]:
        """The current community's events, default first."""
        return await EventRepository.list_all()

    @staticmethod
    async def get_by_slug(slug: str) -> Optional[Event]:
        return await EventRepository.get_by_slug(slug)

    @staticmethod
    async def create_first_event(tenant: Tenant) -> Event:
        """Give a new community its first event: live, the default, named after it.

        A community never has zero events, so every tenant-creation path calls
        this. Callers are the platform surface and seed scripts, which run with
        no tenant in context, hence the explicit tenant.
        """
        if await EventRepository.count_for_tenant(tenant.id):
            raise ValueError(f'{tenant.name} already has an event.')
        event = await EventRepository.create_for_tenant(
            tenant.id, name=tenant.name, slug=tenant.slug,
            status=EventStatus.LIVE, is_default=True,
        )
        _clear_cache()
        return event

    @staticmethod
    async def ensure_first_event(tenant: Tenant) -> Event:
        """Idempotent :meth:`create_first_event` for seed and setup scripts."""
        if await EventRepository.count_for_tenant(tenant.id):
            event = await EventRepository.default_for_tenant(tenant.id)
            if event is not None:
                return event
            raise ValueError(f'{tenant.name} has events but no default event.')
        return await EventService.create_first_event(tenant)
