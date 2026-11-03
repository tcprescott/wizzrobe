"""Friend Service — a person's friends list for Event > Schedule.

The list behind the schedule's Friends switch: tick the people you came to
watch, and the board can show only their matches, across every tournament.

**No audit row and no event, deliberately**, for the reason
``TablePreferenceService`` gives: who you like watching is your own screen, not
a change to a community's data. A save fires a best-effort telemetry
interaction instead, so adoption shows up in the Engagement report.
"""

import logging
from typing import Iterable

from application.repositories import FriendRepository, MatchRepository
from application.services.telemetry_service import TelemetryService
from models import User

logger = logging.getLogger(__name__)


def _by_name(people: Iterable[User]) -> list[User]:
    return sorted(people, key=lambda u: u.preferred_name.casefold())


class FriendService:
    """Read and replace one person's friends list."""

    MAX_FRIENDS = 100

    def __init__(self) -> None:
        self.repository = FriendRepository()

    async def friend_ids(self, user: User | None) -> set[int]:
        if user is None:
            return set()
        return await self.repository.friend_ids(user.id)

    async def picker_options(self, user: User) -> dict[int, str]:
        """``{user id: name}`` for the Friends field, friends first.

        The viewer's friends lead, then everyone with a match in this event, so a
        friend who isn't playing here stays visible (and removable) on the list.
        The viewer is left out: their own matches are on My Schedule.
        """
        friends = await self.repository.friends(user.id)
        players = await MatchRepository.players_in_event()
        options: dict[int, str] = {u.id: u.preferred_name for u in _by_name(friends)}
        for u in _by_name(players):
            if u.id != user.id:
                options.setdefault(u.id, u.preferred_name)
        return options

    async def set_friends(self, user: User, friend_ids: Iterable) -> set[int]:
        """Replace ``user``'s list with ``friend_ids``; returns the stored set.

        The ids arrive from a browser, so anything that isn't an active account
        is dropped rather than refused, and so is the viewer themselves.
        """
        try:
            wanted = {int(f) for f in friend_ids or []}
        except (TypeError, ValueError) as e:
            raise ValueError("That isn't a list of people.") from e
        wanted.discard(user.id)
        if len(wanted) > self.MAX_FRIENDS:
            raise ValueError(f'You can keep up to {self.MAX_FRIENDS} friends.')
        wanted = await self.repository.existing_user_ids(wanted)
        current = await self.repository.friend_ids(user.id)
        await self.repository.remove(user.id, current - wanted)
        await self.repository.add(user.id, wanted - current)
        await self._track(user, len(wanted))
        return wanted

    @staticmethod
    async def _track(user: User, count: int) -> None:
        try:
            await TelemetryService().track_interaction(
                event_type='schedule.friends_saved',
                discord_id=getattr(user, 'discord_id', None),
                username=getattr(user, 'username', None),
                details={'count': count},
            )
        except Exception:
            logger.debug('friends telemetry failed', exc_info=True)
