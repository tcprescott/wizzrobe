"""Row copies for a new event's "Copy setup from" (``EventService.create_event``).

Pure data access: each method copies one kind of setup row from one event of the
current community into another and returns the old-id → new-id map, so the
service can re-point rows that refer to what it copied. What to copy, what to
clear and what to skip is the service's call (``event_setup_copy``); this layer
only carries the fields across.
"""

from typing import Any, Dict, Iterable, List, Optional, Type

from tortoise.models import Model

from application.repositories._tenant import current_tenant_id, scoped
from models import (
    CheckInEvent,
    CheckInTier,
    DiscordRoleMapping,
    DiscordRoleRule,
    Stage,
    Station,
    Tournament,
    VolunteerPosition,
    VolunteerQualification,
    VolunteerRoleMapping,
)

_ALWAYS_SKIPPED = frozenset({'id', 'tenant_id', 'event_id', 'created_at', 'updated_at'})


def _fields_of(row: Model, skip: Iterable[str]) -> Dict[str, Any]:
    """The row's own columns, minus ``skip``: a skipped column takes the model's default."""
    dropped = _ALWAYS_SKIPPED | set(skip)
    return {
        name: getattr(row, name)
        for name in row._meta.fields_db_projection
        if name not in dropped
    }


async def _copy_rows(
    model: Type[Model], rows: List[Model], to_event_id: int, *, skip: Iterable[str] = (),
    overrides: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[int, int]:
    tenant_id = current_tenant_id()
    mapping: Dict[int, int] = {}
    for row in rows:
        fields = _fields_of(row, skip)
        fields.update((overrides or {}).get(row.pk, {}))
        copy = await model.create(tenant_id=tenant_id, event_id=to_event_id, **fields)
        mapping[row.pk] = copy.pk
    return mapping


class EventSetupRepository:

    @staticmethod
    async def tournaments(from_event_id: int) -> List[Tournament]:
        return await scoped(Tournament.filter(event_id=from_event_id)).order_by('id')

    @staticmethod
    async def copy_tournaments(
        rows: List[Tournament], to_event_id: int, *, skip: Iterable[str], config: Dict[int, Any],
    ) -> Dict[int, int]:
        return await _copy_rows(
            Tournament, rows, to_event_id, skip=skip,
            overrides={t.id: {'config': config.get(t.id)} for t in rows},
        )

    @staticmethod
    async def copy_tournament_role_mappings(tournament_ids: Dict[int, int]) -> int:
        """Discord role mappings onto a copied tournament, re-pointed at the copy. Community-owned rows."""
        rows = await scoped(DiscordRoleMapping.filter(tournament_id__in=list(tournament_ids)))
        for row in rows:
            await DiscordRoleMapping.create(
                tenant_id=current_tenant_id(), guild_id=row.guild_id,
                discord_role_id=row.discord_role_id, discord_role_name=row.discord_role_name,
                app_role=row.app_role, tournament_grant=row.tournament_grant,
                tournament_id=tournament_ids[row.tournament_id],  # type: ignore[attr-defined]
            )
        return len(rows)

    @staticmethod
    async def copy_positions(from_event_id: int, to_event_id: int) -> Dict[int, int]:
        rows = await scoped(VolunteerPosition.filter(event_id=from_event_id)).order_by('id')
        return await _copy_rows(VolunteerPosition, rows, to_event_id)

    @staticmethod
    async def copy_position_role_mappings(position_ids: Dict[int, int], to_event_id: int) -> int:
        rows = await scoped(VolunteerRoleMapping.filter(position_id__in=list(position_ids)))
        for row in rows:
            await VolunteerRoleMapping.create(
                tenant_id=current_tenant_id(), event_id=to_event_id,
                position_id=position_ids[row.position_id], app_role=row.app_role,  # type: ignore[attr-defined]
            )
        return len(rows)

    @staticmethod
    async def copy_qualifications(position_ids: Dict[int, int]) -> int:
        """Who is trained for each position. These follow their position, no event of their own."""
        rows = await scoped(VolunteerQualification.filter(position_id__in=list(position_ids)))
        for row in rows:
            await VolunteerQualification.create(
                tenant_id=current_tenant_id(), user_id=row.user_id,  # type: ignore[attr-defined]
                position_id=position_ids[row.position_id],  # type: ignore[attr-defined]
            )
        return len(rows)

    @staticmethod
    async def copy_stages(from_event_id: int, to_event_id: int) -> Dict[int, int]:
        rows = await scoped(Stage.filter(event_id=from_event_id)).order_by('id')
        return await _copy_rows(Stage, rows, to_event_id)

    @staticmethod
    async def copy_stations(from_event_id: int, to_event_id: int) -> Dict[int, int]:
        rows = await scoped(Station.filter(event_id=from_event_id)).order_by('id')
        return await _copy_rows(Station, rows, to_event_id)

    @staticmethod
    async def copy_check_in(from_event_id: int, to_event_id: int, *, skip: Iterable[str]) -> tuple[int, int]:
        """Check-in events (without their Matcherino venue) and their tiers. Returns ``(events, tiers)``."""
        events = await scoped(CheckInEvent.filter(event_id=from_event_id)).order_by('id')
        event_ids = await _copy_rows(CheckInEvent, events, to_event_id, skip=skip)
        tiers = 0
        for old_id, new_id in event_ids.items():
            for tier in await scoped(CheckInTier.filter(event_id=old_id)).order_by('id'):
                await CheckInTier.create(
                    tenant_id=current_tenant_id(), event_id=new_id,
                    **_fields_of(tier, ()),
                )
                tiers += 1
        return len(event_ids), tiers

    @staticmethod
    async def role_rules(from_event_id: int) -> List[DiscordRoleRule]:
        return await scoped(DiscordRoleRule.filter(event_id=from_event_id)).order_by('id')

    @staticmethod
    async def copy_role_rule(rule: DiscordRoleRule, to_event_id: int, *, key: str, tournament_id: Optional[int]) -> None:
        await DiscordRoleRule.create(
            tenant_id=current_tenant_id(), event_id=to_event_id,
            key=key, kind=rule.kind, tournament_id=tournament_id, lanyard=rule.lanyard,
            discord_role_id=rule.discord_role_id, discord_role_name=rule.discord_role_name,
        )
