"""Station Service - Business Logic Layer

CRUD for the venue's station pool. Staff-only; the pool is community
configuration, not per-match data.

A community with no stations keeps the historical free-text station behaviour —
``MatchService.assign_stations`` only validates against the pool once it exists.
That is what lets this ship without a per-tenant feature flag.
"""

from typing import List, Optional

from application.errors import require_found
from application.repositories import StationRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from models import Station, StationSide, User


def _validate_position(position: Optional[int]) -> None:
    """A seat index is a counting number or nothing at all.

    Zero and negatives would still compare correctly for adjacency, but they
    read as a mistake in the admin table and there is no venue that numbers a
    row from -3.
    """
    if position is not None and position < 1:
        raise ValueError("Station position must be 1 or greater.")


class StationService:
    """Manage the community's station pool."""

    def __init__(self) -> None:
        self.repository = StationRepository()
        self.audit_service = AuditService()

    async def list_stations(self, active_only: bool = False) -> List[Station]:
        """The pool in display order; ``active_only`` drops retired stations."""
        return await (
            self.repository.get_active() if active_only else self.repository.get_all()
        )

    async def get_station(self, station_id: int) -> Station:
        return require_found(await self.repository.get_by_id(station_id), 'Station')

    async def create_station(
        self,
        name: str,
        actor: Optional[User] = None,
        *,
        section: Optional[str] = None,
        side: Optional[StationSide] = None,
        position: Optional[int] = None,
        sort_order: int = 0,
    ) -> Station:
        await AuthService.ensure(
            await AuthService.is_staff(actor), "Only Staff can manage stations",
        )
        name = (name or '').strip()
        if not name:
            raise ValueError("Station name is required.")
        if await self._name_taken(name):
            raise ValueError(f"Station '{name}' already exists.")
        _validate_position(position)

        station = await self.repository.create(
            name=name,
            section=(section or '').strip() or None,
            side=side,
            position=position,
            sort_order=sort_order,
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.STATION_CREATED,
            {'station_id': station.id, 'name': name},
        )
        return station

    async def update_station(
        self,
        station_id: int,
        actor: Optional[User] = None,
        *,
        name: Optional[str] = None,
        section: Optional[str] = None,
        side: Optional[StationSide] = None,
        position: Optional[int] = None,
        sort_order: Optional[int] = None,
        is_active: Optional[bool] = None,
        clear_side: bool = False,
        clear_position: bool = False,
    ) -> Station:
        await AuthService.ensure(
            await AuthService.is_staff(actor), "Only Staff can manage stations",
        )
        station = await self.get_station(station_id)

        fields: dict = {}
        if name is not None:
            clean = name.strip()
            if not clean:
                raise ValueError("Station name is required.")
            if clean != station.name and await self._name_taken(clean):
                raise ValueError(f"Station '{clean}' already exists.")
            fields['name'] = clean
        if section is not None:
            fields['section'] = section.strip() or None
        if side is not None:
            fields['side'] = side
        elif clear_side:
            fields['side'] = None
        if position is not None:
            _validate_position(position)
            fields['position'] = position
        elif clear_position:
            fields['position'] = None
        if sort_order is not None:
            fields['sort_order'] = sort_order
        if is_active is not None:
            fields['is_active'] = is_active

        result = await self.repository.update(station, **fields)
        await self.audit_service.write_log(
            actor,
            AuditActions.STATION_UPDATED,
            {'station_id': station.id, 'changed_fields': list(fields.keys())},
        )
        return result

    async def delete_station(self, station_id: int, actor: Optional[User] = None) -> None:
        """Remove a station from the pool.

        Historical ``MatchPlayers.assigned_station`` rows are untouched — the
        assignment stores the *label*, not an FK, so past matches keep showing
        where they were played. Prefer deactivating over deleting.
        """
        await AuthService.ensure(
            await AuthService.is_staff(actor), "Only Staff can manage stations",
        )
        station = await self.get_station(station_id)
        name = station.name
        await self.repository.delete(station)
        await self.audit_service.write_log(
            actor,
            AuditActions.STATION_DELETED,
            {'station_id': station_id, 'name': name},
        )

    async def _name_taken(self, name: str) -> bool:
        """``name`` collides with an existing station in this community.

        Checks the whole pool, not just the active half: the unique constraint is
        ``(tenant, name)``, so a retired station still owns its label.
        """
        return any(s.name == name for s in await self.repository.get_all())
