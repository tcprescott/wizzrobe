"""Match participant orchestration — roster & acknowledgment row syncing.

Pure repository orchestration lifted out of :class:`MatchService` so that module
stays focused on business rules, audit, and events. This helper owns the "who is
in the match" bookkeeping: resolving user ids, enrolling players in the
tournament, syncing the player and crew rows to a target set, and (re)seeding the
per-player acknowledgment rows. It performs no audit writes and publishes no
events — those stay with the service methods that call it.
"""

from typing import Any, List, Optional, Tuple

from application.errors import require_found
from application.repositories import (
    MatchAcknowledgmentRepository,
    MatchRepository,
    TournamentRepository,
    UserRepository,
)
from models import Match, User


class MatchParticipants:
    """Sync a match's player, crew, and acknowledgment rows to target sets."""

    def __init__(
        self,
        *,
        match_repository: MatchRepository,
        user_repository: UserRepository,
        tournament_repository: TournamentRepository,
        ack_repository: MatchAcknowledgmentRepository,
    ) -> None:
        self.match_repository = match_repository
        self.user_repository = user_repository
        self.tournament_repository = tournament_repository
        self.ack_repository = ack_repository

    async def resolve_users(self, user_ids: List[int]) -> List[User]:
        """Resolve an id list into User objects (one query), preserving order.

        Raises ValueError for the first id with no matching user so callers can
        validate a whole role list before creating any rows.
        """
        users_by_id = await self.user_repository.get_by_ids(user_ids)
        resolved: List[User] = []
        for uid in user_ids:
            resolved.append(require_found(users_by_id.get(uid), f"User {uid}"))
        return resolved

    async def ensure_enrolled(self, tournament_id: int, users: List[User]) -> List[User]:
        """Enroll any of ``users`` not already in the tournament (one lookup query).

        Returns the users this call actually enrolled, so the caller can *say
        so*: scheduling someone into a tournament they are not in genuinely
        should enrol them, but it used to happen silently.
        """
        if not users:
            return []
        enrolled = await self.tournament_repository.get_enrolled_user_ids(tournament_id)
        newly: List[User] = []
        for user in users:
            if user.id not in enrolled:
                await self.tournament_repository.enroll_player_by_id(
                    tournament_id=tournament_id, user=user,
                )
                enrolled.add(user.id)
                newly.append(user)
        return newly

    async def sync_players(
        self, match: Match, new_player_ids: List[int], tournament_id: int
    ) -> List[User]:
        """Sync match players to new list; returns the players taken off it."""
        current_players = await self.match_repository.get_players(match)
        current_ids = {p.user_id for p in current_players}
        new_ids = set(new_player_ids)

        to_add = new_ids - current_ids
        if to_add:
            add_users = await self.resolve_users(list(to_add))
            await self.ensure_enrolled(tournament_id, add_users)
            for user in add_users:
                await self.match_repository.add_player(match, user)

        to_remove = current_ids - new_ids
        removed: List[User] = []
        if to_remove:
            remove_users = await self.user_repository.get_by_ids(list(to_remove))
            for uid in to_remove:
                user = remove_users.get(uid)
                if user:
                    await self.match_repository.remove_player(match, user)
                    removed.append(user)
        return removed

    async def sync_crew(
        self, match: Match, new_ids: List[int], repository, actor: Optional[User] = None,
    ) -> Tuple[List[Any], List[Any]]:
        """Sync a match's crew (commentators or trackers) to the given user-id list.

        Returns ``(created, removed)`` rows so the caller can announce both: an
        admin assigning crew is an approval, and dropping someone is a removal,
        and each owes the same event and DM as its self-service counterpart.
        """
        existing = await repository.get_by_match(match)
        existing_map = {c.user_id: c for c in existing}
        existing_ids = set(existing_map.keys())
        new_ids_set = set(new_ids)

        to_add = new_ids_set - existing_ids
        created = []
        if to_add:
            for user in await self.resolve_users(list(to_add)):
                created.append(
                    await repository.create(match=match, user=user, approved=True, approved_by=actor)
                )

        removed = []
        for uid in existing_ids - new_ids_set:
            await repository.delete(existing_map[uid])
            removed.append(existing_map[uid])

        return created, removed

    async def reconcile_acknowledgments(
        self, match: Match, player_ids: List[int],
    ) -> tuple[int, int]:
        """Give every current player a row, drop the rest, keep the answers.

        The non-destructive counterpart to :meth:`seed_acknowledgments`, for
        callers that run repeatedly over a match whose players may already have
        replied — the SpeedGaming sync above all. Without a row a player is
        invisible to the whole acknowledgment surface: the board's cell renders
        neither the confirmed nor the pending icon, their own Acknowledge button
        is gated on the row existing, and the admin dialog reports nobody
        assigned.

        Returns ``(created, removed)``.
        """
        users_by_id = await self.user_repository.get_by_ids(player_ids)
        users = [users_by_id[pid] for pid in player_ids if pid in users_by_id]
        return await self.ack_repository.reconcile(match, users)

    async def seed_acknowledgments(
        self, match: Match, player_ids: List[int], actor
    ) -> None:
        """Reset and re-create acknowledgment rows for all current players.

        The actor (if present among players) is auto-acknowledged. Destructive
        by design — an admin rewriting the roster is restarting the question.
        A caller that must not discard existing answers wants
        :meth:`ensure_acknowledgments` instead.
        """
        await self.ack_repository.delete_for_match(match)
        actor_id = actor.id if actor is not None else None
        users_by_id = await self.user_repository.get_by_ids(player_ids)
        for pid in player_ids:
            user = users_by_id.get(pid)
            if not user:
                continue
            is_actor = actor_id is not None and pid == actor_id
            await self.ack_repository.upsert(
                match, user,
                acknowledged=is_actor,
                auto=is_actor,
            )
