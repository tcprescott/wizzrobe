"""
Tournament Repository - Data Access Layer

Handles database operations for tournaments.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from application.repositories._base import EventScopedRepository
from application.repositories._event import current_event_id, event_scoped, live_event_scoped
from application.repositories._tenant import current_tenant_id, scoped
from models import Bracket, EventStatus, Match, Tournament, TournamentGrant, TournamentPlayers, TriforceText, User


class TournamentRepository(EventScopedRepository[Tournament]):
    """Repository for tournament data access.

    Every read is constrained to the current tenant via ``scoped(...)`` and every
    write stamps ``tenant_id=current_tenant_id()``; both raise if no tenant is in
    context. Lists are of the current event; a lookup by id is not, because a
    row decides its own event. ``TournamentPlayers`` reads that start from a global ``user`` are the
    sharp edge — they must be tenant-filtered explicitly, since the reverse
    relation spans tenants.
    """

    model = Tournament

    @staticmethod
    async def get_by_id(tournament_id: int, prefetch_players: bool = False) -> Optional[Tournament]:
        """
        Get a tournament by ID.
        
        Args:
            tournament_id: The tournament ID
            prefetch_players: Whether to prefetch enrolled players
            
        Returns:
            Tournament object or None
        """
        query = scoped(Tournament.filter(id=tournament_id))
        if prefetch_players:
            query = query.prefetch_related('players', 'players__user')
        return await query.first()
    
    @staticmethod
    async def active_admin_tournaments(user: User) -> List[Tournament]:
        """Active tournaments ``user`` admins in any live event, by name (cross-event)."""
        return await live_event_scoped(
            Tournament.filter(admins__id=user.id, is_active=True)
        ).distinct().order_by('name')

    @staticmethod
    async def get_by_ids(tournament_ids: List[int]) -> List[Tournament]:
        """
        Get tournaments by a list of IDs.
        
        Args:
            tournament_ids: List of tournament IDs
            
        Returns:
            List of Tournament objects
        """
        return await scoped(Tournament.filter(id__in=tournament_ids)).order_by('name')
    
    @staticmethod
    async def get_all(
        active_only: bool = False,
        prefetch_players: bool = False
    ) -> List[Tournament]:
        """
        Get all tournaments with optional filters.
        
        Args:
            active_only: Only return active tournaments
            prefetch_players: Whether to prefetch enrolled players
            
        Returns:
            List of Tournament objects
        """
        query = event_scoped(Tournament.all()).order_by('name')

        if active_only:
            query = query.filter(is_active=True)
        
        if prefetch_players:
            query = query.prefetch_related('players', 'players__user')
        
        return await query

    @staticmethod
    async def active_in_live_events() -> List[Tournament]:
        """Every live event's active tournaments, by name (cross-event: Home › Tournaments)."""
        return await live_event_scoped(Tournament.filter(is_active=True)).order_by('name')

    @staticmethod
    async def enrolment_counts() -> dict[int, int]:
        """``{tournament_id: entrants}`` for this tenant, in one query.

        The admin match dialog's Tournament select needs it: an option whose
        player menu will open empty is not a choice, and finding that out by
        pressing Create and reading "Match must have at least one player" is the
        long way round.
        """
        rows: list[int] = await scoped(
            TournamentPlayers.all()
        ).values_list('tournament_id', flat=True)  # type: ignore[assignment]
        counts: dict[int, int] = {}
        for tournament_id in rows:
            counts[tournament_id] = counts.get(tournament_id, 0) + 1
        return counts

    @staticmethod
    async def dependent_counts(tournament_id: int) -> dict[str, int]:
        """What a permanent delete would take with it, per related table.

        Every one of these rows cascades from the tournament row, so the number
        a staff member sees in the confirmation dialog is the number that
        disappears.
        """
        return {
            'matches': await scoped(Match.filter(tournament_id=tournament_id)).count(),
            'players': await scoped(TournamentPlayers.filter(tournament_id=tournament_id)).count(),
            'brackets': await scoped(Bracket.filter(tournament_id=tournament_id)).count(),
            'triforce_texts': await scoped(TriforceText.filter(tournament_id=tournament_id)).count(),
        }

    @staticmethod
    async def entrants_by_tournament(
        tournament_ids: List[int],
    ) -> dict[int, list[Any]]:
        """``{tournament_id: [User, …]}`` for the given tournaments, in one query.

        The Tournaments tab shows each card's entrant list, so the per-card
        ``get_enrolled_players`` it would otherwise make is one query per card
        plus one per row for the user. Ordered by display name so two cards
        listing the same person put them in the same place.
        """
        rows = await scoped(
            TournamentPlayers.filter(tournament_id__in=tournament_ids)
        ).prefetch_related('user')
        entrants: dict[int, list[Any]] = {}
        for row in rows:
            entrants.setdefault(row.tournament_id, []).append(row.user)
        for players in entrants.values():
            players.sort(key=lambda u: (u.display_name or u.username or '').lower())
        return entrants

    @staticmethod
    async def admins_by_tournament(
        tournament_ids: List[int],
    ) -> dict[int, list[Any]]:
        """``{tournament_id: [User, …]}`` of each tournament's own admins, A to Z."""
        tournaments = await scoped(
            Tournament.filter(id__in=tournament_ids)
        ).prefetch_related('admins')
        return {
            t.id: sorted(t.admins, key=lambda u: (u.display_name or u.username or '').lower())
            for t in tournaments
        }

    @staticmethod
    async def enrolled_tournament_ids(user_id: int) -> set[int]:
        """The tournaments in *this tenant* one user is enrolled in.

        Tenant-filtered explicitly: ``TournamentPlayers`` reached from a global
        ``user`` spans every community that user plays in.
        """
        rows: list[int] = await scoped(
            TournamentPlayers.filter(user_id=user_id)
        ).values_list('tournament_id', flat=True)  # type: ignore[assignment]
        return set(rows)

    @staticmethod
    async def any_exists() -> bool:
        """Whether the current event has any tournament at all."""
        return await event_scoped(Tournament.all()).exists()

    @staticmethod
    async def any_enrolment() -> bool:
        """Whether anyone is enrolled in any of this tenant's tournaments."""
        return await scoped(TournamentPlayers.all()).exists()

    @staticmethod
    async def get_player_requestable(
        user_id: Optional[int] = None,
    ) -> List[Tournament]:
        """Tournaments a player may still request a match in.

        Excludes the bracket-run ones (``allow_player_match_requests`` off) and
        archived ones (``is_active`` off), which ``submit_match_request`` refuses.
        Narrowed to the tournaments ``user_id`` is enrolled in when given, which
        is what the request dialog's default (non-"show all") list needs. The
        dialog lives on My Schedule, so it spans every live event (cross-event).
        """
        query = live_event_scoped(
            Tournament.filter(allow_player_match_requests=True, is_active=True)
        ).order_by('name')
        if user_id is not None:
            query = query.filter(players__user_id=user_id)
        return await query.distinct()

    @staticmethod
    async def get_all_as_dict(
        active_only: bool = False,
    ) -> dict[int, str]:
        """
        Get all tournaments as a dict mapping ID to name.
        Useful for dropdown/select options.
        
        Args:
            active_only: Only return active tournaments
            
        Returns:
            Dict mapping tournament ID to name
        """
        tournaments = await TournamentRepository.get_all(
            active_only=active_only,
            prefetch_players=False
        )
        return {t.id: t.name for t in tournaments}
    
    @staticmethod
    async def create(  # type: ignore[override]
        name: str,
        description: Optional[str] = None,
        short_name: Optional[str] = None,
        seed_generator: Optional[str] = None,
        is_active: bool = True,
        is_test: bool = False,
        players_release_seed: bool = False,
        uses_tournament_room: bool = True,
        players_per_match: int = 2,
        bracket_url: Optional[str] = None,
        rules_url: Optional[str] = None,
        tournament_format: Optional[str] = None,
        triforce_access_message: Optional[str] = None,
        randomizer_notes: Optional[str] = None,
        average_match_duration: Optional[int] = None,
        max_match_duration: Optional[int] = None,
        required_commentators: int = 1,
        required_trackers: int = 1,
        allow_player_match_requests: bool = True,
        config: Optional[Dict[str, Any]] = None,
        preset_id: Optional[int] = None,
        hard_preset_id: Optional[int] = None,
        racetime_bot_id: Optional[int] = None,
        race_room_profile_id: Optional[int] = None,
        racetime_auto_create_rooms: bool = False,
        room_open_minutes_before: int = 30,
        stage_reminder_minutes: int = 30,
        require_racetime_link: bool = False,
        racetime_default_goal: Optional[str] = None,
        event_start_date: Optional[date] = None,
        event_end_date: Optional[date] = None,
        tournament_hours: Optional[Dict[str, Any]] = None,
        signups_open_at: Optional[datetime] = None,
        signups_close_at: Optional[datetime] = None,
    ) -> Tournament:
        """
        Create a new tournament.
        
        Args:
            name: Tournament name
            description: Tournament description
            seed_generator: Name of seed generator to use
            is_active: Whether tournament is active
            is_test: Whether it is a staff-only test tournament
            players_release_seed: Whether players release their own seed by all marking ready
            uses_tournament_room: Whether its off-stream matches take room capacity
            players_per_match: Number of players per match
            bracket_url: URL to tournament bracket
            rules_url: URL to tournament rules
            tournament_format: Format description
            average_match_duration: Average match duration in minutes
            max_match_duration: Maximum match duration in minutes
            required_commentators: Approved commentators a streamed match needs
                to count as covered (0 = the role is not used here)
            required_trackers: Approved trackers a streamed match needs
            
        Returns:
            Created Tournament object
        """
        return await Tournament.create(
            tenant_id=current_tenant_id(),
            event_id=current_event_id(),
            name=name,
            short_name=short_name,
            description=description,
            seed_generator=seed_generator,
            is_active=is_active,
            is_test=is_test,
            players_release_seed=players_release_seed,
            uses_tournament_room=uses_tournament_room,
            players_per_match=players_per_match,
            bracket_url=bracket_url,
            rules_url=rules_url,
            tournament_format=tournament_format,
            triforce_access_message=triforce_access_message,
            randomizer_notes=randomizer_notes,
            average_match_duration=average_match_duration,
            max_match_duration=max_match_duration,
            required_commentators=required_commentators,
            required_trackers=required_trackers,
            allow_player_match_requests=allow_player_match_requests,
            config=config,
            preset_id=preset_id,
            hard_preset_id=hard_preset_id,
            racetime_bot_id=racetime_bot_id,
            race_room_profile_id=race_room_profile_id,
            racetime_auto_create_rooms=racetime_auto_create_rooms,
            room_open_minutes_before=room_open_minutes_before,
            stage_reminder_minutes=stage_reminder_minutes,
            require_racetime_link=require_racetime_link,
            racetime_default_goal=racetime_default_goal,
            event_start_date=event_start_date,
            event_end_date=event_end_date,
            tournament_hours=tournament_hours,
            signups_open_at=signups_open_at,
            signups_close_at=signups_close_at,
        )

    @staticmethod
    async def enroll_player(tournament: Tournament, user) -> TournamentPlayers:
        """
        Enroll a user in a tournament.
        
        Args:
            tournament: Tournament to enroll in
            user: User to enroll
            
        Returns:
            Created TournamentPlayers object
        """
        return await TournamentPlayers.create(tenant_id=current_tenant_id(), tournament=tournament, user=user)
    
    @staticmethod
    async def unenroll_player(tournament: Tournament, user) -> None:
        """
        Remove a user from a tournament.
        
        Args:
            tournament: Tournament to unenroll from
            user: User to unenroll
        """
        await scoped(TournamentPlayers.filter(tournament=tournament, user=user)).delete()
    
    @staticmethod
    async def get_enrolled_players(tournament: Tournament) -> List:
        """
        Get all players enrolled in a tournament.
        
        Args:
            tournament: Tournament to get players for
            
        Returns:
            List of TournamentPlayers objects
        """
        return await scoped(TournamentPlayers.filter(tournament=tournament)).prefetch_related('user')
    
    @staticmethod
    async def set_tournament_grant(
        tournament: Tournament, user, grant: TournamentGrant, granted: bool
    ) -> None:
        """Add or remove one ``admins`` / ``crew_coordinators`` row.

        The relation is chosen by ``grant`` so callers reconciling a set of
        grants (the Discord role sync) don't branch on it themselves. Both
        Tortoise operations are idempotent.
        """
        relation = (
            tournament.admins if grant == TournamentGrant.TOURNAMENT_ADMIN
            else tournament.crew_coordinators
        )
        await (relation.add(user) if granted else relation.remove(user))

    @staticmethod
    async def has_tournament_grant(
        tournament_id: int, user, grant: TournamentGrant
    ) -> bool:
        """Whether ``user`` currently holds ``grant`` on this tenant's tournament."""
        field = (
            'admins__id' if grant == TournamentGrant.TOURNAMENT_ADMIN
            else 'crew_coordinators__id'
        )
        return await scoped(Tournament.filter(id=tournament_id, **{field: user.id})).exists()

    @staticmethod
    async def get_crew_owners(tournament_id: int) -> List:
        """The people responsible for a tournament's crew: its admins and its
        crew coordinators, deduplicated.

        The same two groups ``AuthService.can_approve_crew`` admits (minus
        community STAFF, who are not per-tournament) — so a notification
        addressed to them reaches exactly the people who could act on it.
        """
        tournament = await scoped(
            Tournament.filter(id=tournament_id)
        ).prefetch_related('admins', 'crew_coordinators').first()
        if not tournament:
            return []
        by_id = {u.id: u for u in tournament.admins}
        by_id.update({u.id: u for u in tournament.crew_coordinators})
        return list(by_id.values())

    @staticmethod
    async def get_enrolled_players_by_user(user) -> List:
        """This user's enrolments in every live event's tournaments (cross-event: their own schedule)."""
        return await scoped(TournamentPlayers.filter(
            user=user, tournament__event__status=EventStatus.LIVE,
        )).prefetch_related('tournament')
    
    @staticmethod
    async def get_enrolled_players_by_tournament_id(tournament_id: int) -> List:
        """
        Get all enrolled players for a specific tournament by ID.
        
        Args:
            tournament_id: Tournament ID
            
        Returns:
            List of TournamentPlayers objects
        """
        return await scoped(TournamentPlayers.filter(tournament_id=tournament_id)).prefetch_related('user')
    
    @staticmethod
    async def get_enrolled_user_ids(tournament_id: int) -> set[int]:
        """Return the set of user ids already enrolled in a tournament.

        Lets callers resolve enrollment for a whole player list in one query
        instead of an ``is_player_enrolled`` round-trip per player.
        """
        rows = await scoped(TournamentPlayers.filter(
            tournament_id=tournament_id
        )).values_list('user_id', flat=True)
        return set(rows)

    @staticmethod
    async def is_player_enrolled(tournament: Tournament, user) -> bool:
        """
        Check if a user is enrolled in a tournament.
        
        Args:
            tournament: Tournament to check
            user: User to check
            
        Returns:
            True if enrolled, False otherwise
        """
        return await scoped(TournamentPlayers.filter(tournament=tournament, user=user)).exists()
    
    @staticmethod
    async def is_player_enrolled_by_id(tournament_id: int, user) -> bool:
        """
        Check if a user is enrolled in a tournament by tournament ID.
        
        Args:
            tournament_id: Tournament ID to check
            user: User to check
            
        Returns:
            True if enrolled, False otherwise
        """
        return await scoped(TournamentPlayers.filter(tournament_id=tournament_id, user=user)).exists()
    
    @staticmethod
    async def enroll_player_by_id(tournament_id: int, user) -> TournamentPlayers:
        """
        Enroll a user in a tournament by tournament ID.
        
        Args:
            tournament_id: Tournament ID to enroll in
            user: User to enroll
            
        Returns:
            Created TournamentPlayers object
        """
        return await TournamentPlayers.create(tenant_id=current_tenant_id(), tournament_id=tournament_id, user=user)
