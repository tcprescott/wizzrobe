"""Friend Repository - Data Access Layer

Handles database operations for a person's friends list (``UserFriend``).

Deliberately **not** tenant-scoped, like ``TablePreferenceRepository``: the list
belongs to a person, not a community, so every method keys off ``user_id``.
"""

from typing import Iterable

from application.repositories.match_repository import BOARD_USER_FIELDS
from models import User, UserFriend


class FriendRepository:
    """Data access for :class:`UserFriend`."""

    @staticmethod
    async def friend_ids(user_id: int) -> set[int]:
        rows = await UserFriend.filter(user_id=user_id).values('friend_id')
        return {int(r['friend_id']) for r in rows}

    @staticmethod
    async def friends(user_id: int) -> list[User]:
        return await User.filter(friend_of__user_id=user_id).only(*BOARD_USER_FIELDS)

    @staticmethod
    async def existing_user_ids(user_ids: Iterable[int]) -> set[int]:
        ids = list(user_ids)
        if not ids:
            return set()
        rows = await User.filter(id__in=ids, is_active=True).values('id')
        return {int(r['id']) for r in rows}

    @staticmethod
    async def add(user_id: int, friend_ids: Iterable[int]) -> None:
        ids = list(friend_ids)
        if ids:
            await UserFriend.bulk_create([UserFriend(user_id=user_id, friend_id=f) for f in ids])

    @staticmethod
    async def remove(user_id: int, friend_ids: Iterable[int]) -> None:
        ids = list(friend_ids)
        if ids:
            await UserFriend.filter(user_id=user_id, friend_id__in=ids).delete()
