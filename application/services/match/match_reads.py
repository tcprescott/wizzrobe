"""The match lookups every entry surface goes through.

Split out of ``match_service`` the way :class:`MatchReviewMixin` and
:class:`CancellationMixin` are: :class:`MatchReadsMixin` is composed into
:class:`MatchService` and reaches ``repository`` through that composed class.
Reads only — nothing here writes, audits or notifies.

They live on the service rather than being left to callers because
``pages/``, ``api/`` and ``discordbot/`` must never reach through
``match_service.repository`` for a simple read (``enforce_architecture.py``
blocks it), and because two of them are not pass-throughs at all: which
instants make up "that day" is a rule about the display clock, and grouping by
stage is the shape the board wants rather than the shape the table has.
"""

from datetime import date, datetime, timedelta
from typing import Dict, FrozenSet, List, Optional, Tuple

from application.repositories import MatchRepository
from application.utils.timezone import local_day_bounds
from models import Match, MatchPlayers, Stage, User

# The four answers to "what is this match to you?", strongest tie first.
RELATION_PLAYER = 'player'
RELATION_CREW = 'crew'
RELATION_WATCHER = 'watcher'
RELATION_NONE = 'none'


class MatchReadsMixin:
    """Load-or-None lookups, the schedule queries, and the stage grouping."""

    repository: MatchRepository

    async def get_match_by_id(self, match_id: int) -> Optional[Match]:
        return await self.repository.get_by_id(match_id)

    async def get_by_id(
        self, match_id: int, prefetch_relations: bool = True
    ) -> Optional[Match]:
        """Read-only load-or-None lookup for presentation/bot callers.

        Exposed so entry surfaces (pages/, api/, discordbot/) never reach
        through ``match_service.repository`` for a simple read.
        """
        return await self.repository.get_by_id(match_id, prefetch_relations=prefetch_relations)

    async def schedule_page(
        self,
        *,
        first: int,
        after: Optional[Tuple[datetime, int]] = None,
        tournament_ids: Optional[List[int]] = None,
        stage_ids: Optional[List[int]] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Tuple[List[Match], bool]:
        """One page of the current event's scheduled matches, soonest first,
        and whether another page follows. Any member may read the schedule."""
        if first < 0:
            raise ValueError('first must be 0 or more')
        rows = await self.repository.schedule_page(
            first=first, after=after, tournament_ids=tournament_ids, stage_ids=stage_ids, start=start, end=end,
        )
        return rows[:first], len(rows) > first

    @staticmethod
    def approved_crew(match: Match) -> List[Tuple[str, User]]:
        """``(role, user)`` for the match's approved crew. A pending signup is
        between the volunteer and the coordinators, so a schedule never shows it."""
        crew: List[Tuple[str, User]] = []
        for role, rows in (
            ('commentator', match.commentators),  # type: ignore[attr-defined]
            ('tracker', match.trackers),  # type: ignore[attr-defined]
            ('broadcaster', match.broadcasters),  # type: ignore[attr-defined]
        ):
            crew.extend((role, row.user) for row in rows if row.approved)
        return crew

    async def viewer_relation(self, match_id: int, user: Optional[User]) -> str:
        """What ``match_id`` is to ``user``: player, crew, watcher, or none.

        For a deep link that names a match, so the page can say why the match is
        or is not on the board it opened: a DM outlives the slot it was about,
        and a crew member following a stage DM to the player board used to land
        on "No matches to show yet" with no word about why. Crew counts any
        signup, approved or not, because both have a card under Crew you signed
        up for. A missing match (or another community's) is ``none``.
        """
        if user is None:
            return RELATION_NONE
        match = await self.repository.get_by_id(match_id)
        if match is None:
            return RELATION_NONE
        if any(p.user_id == user.id for p in match.players):
            return RELATION_PLAYER
        crew = [*match.commentators, *match.trackers, *match.broadcasters]
        if any(c.user_id == user.id for c in crew):
            return RELATION_CREW
        from application.repositories import MatchWatcherRepository

        if await MatchWatcherRepository.is_watching(match_id, user.id):
            return RELATION_WATCHER
        return RELATION_NONE

    async def bracket_link_candidates(self, tournament_ids: List[int]) -> List[Match]:
        """Matches staff could link to a bracket matchup: unconfirmed and unlinked."""
        return await self.repository.bracket_link_candidates(tournament_ids)

    async def unambiguous_link_candidates(self, tournament_id: int) -> Dict[FrozenSet[int], Match]:
        """Link candidates keyed by their two players, keeping only a pair with exactly one.

        A sync re-attaches a booking to its matchup by this after a bracket reset
        renumbers every match; a pair with two candidates is left for staff.
        """
        by_pair: Dict[FrozenSet[int], List[Match]] = {}
        for match in await self.repository.bracket_link_candidates([tournament_id]):
            players = frozenset(p.user_id for p in match.players)
            if len(players) == 2 and len(match.players) == 2:
                by_pair.setdefault(players, []).append(match)
        return {pair: found[0] for pair, found in by_pair.items() if len(found) == 1}

    async def require_bracket_linkable(
        self, match_id: int, tournament_id: int, player_ids: List[Optional[int]],
    ) -> Match:
        """Load ``match_id`` if it may back a bracket matchup with these players, else raise.

        The players must be exactly the matchup's two: a site's result is read
        off whose rank is whose, so a mismatched link would report nothing.
        """
        candidates = await self.repository.bracket_link_candidates([tournament_id])
        match = next((m for m in candidates if m.id == match_id), None)
        if match is None:
            raise ValueError(
                "That match can't be linked: it's in another tournament, already "
                "linked to a bracket, or already confirmed."
            )
        if {p.user_id for p in match.players} != set(player_ids):
            raise ValueError("That match's players aren't this matchup's two players.")
        return match

    async def get_match_players(self, match: Match) -> List[MatchPlayers]:
        return await self.repository.get_players(match)

    async def get_player_names(self, match_id: int) -> str:
        """Comma-joined preferred names of a match's players (``''`` if none)."""
        players = await self.repository.get_players(match_id)
        return ', '.join(p.user.preferred_name for p in players) if players else ''
    async def get_all_matches_for_schedule(self) -> List[Match]:
        """
        Get all matches for the public schedule view.

        Returns:
            List of matches with all related data prefetched
        """
        return await self.repository.get_all_for_schedule()

    async def get_matches_for_date(
        self,
        target_date: date,
        exclude_finished: bool = True,
        require_stage: bool = True
    ) -> List[Match]:
        """
        Get all matches for a specific date with optional filters.

        "That day" is resolved on the **display clock**, so a schedule board shows
        the day its reader means. The repository takes instants; deciding which
        instants make up a day is the rule that lives here.

        Args:
            target_date: The date to fetch matches for
            exclude_finished: If True, exclude matches that are finished
            require_stage: If True, only include matches with a stage

        Returns:
            List of matches with all related data prefetched
        """
        start, end = local_day_bounds(target_date, target_date)
        return await self.repository.scheduled_between(
            start, end, exclude_finished, require_stage
        )

    async def get_streamed_matches(self, first_day: date, days: int) -> List[Match]:
        """Unfinished matches on a stage across ``days`` days from ``first_day``.

        The Watch page's whole schedule in one read: today's stage columns and
        the days after them. Days resolve on the display clock, as above.
        """
        start, end = local_day_bounds(first_day, first_day + timedelta(days=days - 1))
        return await self.repository.scheduled_between(start, end, True, True)

    async def group_matches_by_stage(
        self,
        matches: List[Match]
    ) -> Dict[int, Tuple[Stage, List[Match]]]:
        """
        Group matches by their stage.

        Args:
            matches: List of matches to group (must have stage prefetched)

        Returns:
            Dict mapping stage_id to tuple of (Stage, list of matches)
        """
        matches_by_stage: Dict[int, Tuple[Stage, List[Match]]] = {}

        for match in matches:
            if match.stage_id not in matches_by_stage:
                matches_by_stage[match.stage_id] = (match.stage, [])
            matches_by_stage[match.stage_id][1].append(match)

        return matches_by_stage

    async def get_matches_for_player(self, discord_id: str) -> List[Match]:
        """
        Get all matches for a specific player by their Discord ID.

        Args:
            discord_id: Discord ID of the player

        Returns:
            List of matches where the player is participating
        """
        return await self.repository.get_for_player(discord_id)
