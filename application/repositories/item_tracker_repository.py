"""
Item Tracker Repository - Data Access Layer

ALTTP item tracker boards and their change log (see ``ItemTrackerService``).
"""

from datetime import datetime
from typing import List, Optional

from tortoise.expressions import F

from application.repositories._base import TenantScopedRepository
from application.repositories._tenant import current_tenant_id, scoped
from models import ItemTrackerBoard, ItemTrackerChange


class ItemTrackerRepository(TenantScopedRepository[ItemTrackerBoard]):
    model = ItemTrackerBoard

    @staticmethod
    async def list_for_match(match_id: int) -> List[ItemTrackerBoard]:
        return await scoped(ItemTrackerBoard.filter(match_id=match_id)).order_by('id')

    @staticmethod
    async def get_for_runner(match_id: int, runner_id: int) -> Optional[ItemTrackerBoard]:
        return await ItemTrackerBoard.get_or_none(
            match_id=match_id, runner_id=runner_id, tenant_id=current_tenant_id(),
        )

    @staticmethod
    async def get_by_token(token: str) -> Optional[ItemTrackerBoard]:
        return await ItemTrackerBoard.get_or_none(overlay_token=token, tenant_id=current_tenant_id())

    @staticmethod
    async def version(board_id: int) -> Optional[int]:
        row = await ItemTrackerBoard.get_or_none(id=board_id, tenant_id=current_tenant_id()).only('id', 'version')
        return row.version if row else None

    @staticmethod
    async def save_state(board: ItemTrackerBoard, state: dict) -> None:
        board.state = state
        await ItemTrackerBoard.filter(id=board.id, tenant_id=current_tenant_id()).update(
            state=state, version=F('version') + 1,
        )
        board.version += 1

    @staticmethod
    async def set_fields(board: ItemTrackerBoard, **fields) -> None:
        board.update_from_dict(fields)
        await ItemTrackerBoard.filter(id=board.id, tenant_id=current_tenant_id()).update(
            version=F('version') + 1, **fields,
        )
        board.version += 1

    @staticmethod
    async def add_change(
        board_id: int, actor_id: Optional[int], key: str, before: int, after: int,
        when: datetime, *, is_undo: bool = False,
    ) -> ItemTrackerChange:
        return await ItemTrackerChange.create(
            tenant_id=current_tenant_id(), board_id=board_id, actor_id=actor_id,
            key=key, before=before, after=after, created_at=when, is_undo=is_undo,
        )

    @staticmethod
    async def get_change(board_id: int, change_id: int) -> Optional[ItemTrackerChange]:
        return await ItemTrackerChange.get_or_none(
            id=change_id, board_id=board_id, tenant_id=current_tenant_id(),
        )

    @staticmethod
    async def latest_change_for_key(board_id: int, key: str) -> Optional[ItemTrackerChange]:
        return await scoped(ItemTrackerChange.filter(board_id=board_id, key=key)) \
            .order_by('-created_at', '-id').first()

    @staticmethod
    async def mark_undone(change: ItemTrackerChange) -> None:
        change.undone = True
        await ItemTrackerChange.filter(id=change.id, tenant_id=current_tenant_id()).update(undone=True)

    @staticmethod
    async def recent_changes(board_id: int, limit: int) -> List[ItemTrackerChange]:
        return await scoped(ItemTrackerChange.filter(board_id=board_id)) \
            .order_by('-created_at', '-id').limit(limit).prefetch_related('actor')

    @staticmethod
    async def changes_since(board_id: int, since: datetime) -> List[ItemTrackerChange]:
        return await scoped(ItemTrackerChange.filter(board_id=board_id, created_at__gt=since)) \
            .order_by('-created_at', '-id')

    @staticmethod
    async def clear_changes(board_id: int) -> None:
        await scoped(ItemTrackerChange.filter(board_id=board_id)).delete()
