"""
Match Repository - Data Access Layer

Handles all database queries related to matches.
Returns domain objects (Match, MatchPlayers, etc.) without business logic.
"""

from datetime import datetime
from typing import List, Optional, Tuple, cast

from tortoise.expressions import Q
from tortoise.query_utils import Prefetch

from application.repositories._event import event_scoped, live_event_scoped, parent_event_id
from application.repositories._tenant import current_tenant_id, scoped
from models import (
    BracketMatchGame,
    ChallongeMatch,
    Commentator,
    Match,
    MatchPlayers,
    StartggSet,
    Tournament,
    Tracker,
    User,
)

#: The person fields a schedule row reads (``MatchDisplayService`` names and DM
#: badges). ``User`` is ~45 columns wide and a 1,300-match board loads ~5,000 of
#: them, so building the whole row for each was a fifth of the board's load.
#: Anything that reads a person off :meth:`MatchRepository.get_all` must stay
#: inside this list or add to it.
BOARD_USER_FIELDS = ('id', 'discord_id', 'display_name', 'username', 'dm_notifications', 'racetime_user_id')


def _board_user() -> Prefetch:
    return Prefetch('user', queryset=User.all().only(*BOARD_USER_FIELDS))


class MatchRepository:
    """Repository for Match-related database operations."""
    
    @staticmethod
    async def get_by_id(match_id: int, prefetch_relations: bool = True) -> Optional[Match]:
        """
        Get a match by ID.
        
        Args:
            match_id: The match ID
            prefetch_relations: Whether to prefetch related objects
            
        Returns:
            Match object or None if not found
        """
        query = scoped(Match.filter(id=match_id))

        if prefetch_relations:
            query = query.prefetch_related(
                'tournament',
                'players',
                'players__user',
                'stage',
                'generated_seed',
                'commentators',
                'commentators__user',
                'trackers',
                'trackers__user',
                # The bracket seam, for the schedule's "part of <bracket>" link.
                # A batched prefetch, so it costs a fixed few queries rather
                # than one per row; most matches have no game row at all. The
                # ``__games`` hop is one more batched query and is what lets the
                # row show the series standing ("Game 2 of 3 · 1-0") without an
                # N+1 (U4).
                'bracket_match_game__bracket_match__bracket',
                'bracket_match_game__bracket_match__games',
                # Each game's match, so "Game 2 of 3" follows the games' times.
                'bracket_match_game__bracket_match__games__match',
            )
        
        return await query.first()
    
    @staticmethod
    async def get_by_speedgaming_episode(episode_id: int) -> Optional[Match]:
        """The Match materialized from an SG episode, or None (tenant-scoped).

        Used by the ETL to find an already-materialized match on re-sync. The
        source FK is a OneToOne, so at most one match matches, and that row
        decides its own event (event-scope: exempt — one row per episode).
        """
        return await scoped(
            Match.filter(speedgaming_episode_id=episode_id)
        ).prefetch_related('tournament', 'players', 'players__user').first()

    @staticmethod
    async def list_sourced_stale(cutoff: datetime, tournament_id: int) -> List[Match]:
        """SG-sourced, unfinished matches for a tournament scheduled before ``cutoff``.

        Feeds the ETL auto-finish guard (matches >4h past that nobody closed).
        Tenant-scoped; the caller filters out room-linked matches.
        """
        return await scoped(Match.filter(
            tournament_id=tournament_id,
            speedgaming_episode_id__isnull=False,
            finished_at__isnull=True,
            scheduled_at__lt=cutoff,
        )).prefetch_related('racetime_room')

    @staticmethod
    async def list_for_discord_sync(
        window_start: datetime, window_end: datetime
    ) -> List[Match]:
        """Scheduled, unfinished matches in opted-in tournaments within a window.

        Feeds the Discord Events reconciler (PR 8): tenant-scoped, so the caller
        already runs inside the tenant whose guild is being reconciled. Only
        matches whose tournament has ``discord_events_enabled`` are returned;
        finished matches are excluded (their mirrored event is cancelled).
        """
        return await scoped(Match.filter(
            tournament__discord_events_enabled=True,
            finished_at__isnull=True,
            scheduled_at__isnull=False,
            scheduled_at__gte=window_start,
            scheduled_at__lte=window_end,
        )).prefetch_related('tournament', 'players', 'players__user').order_by('scheduled_at')

    @staticmethod
    async def due_for_stage_reminder(
        now: datetime, window_end: datetime,
    ) -> List[Match]:
        """Matches whose stage reminder may be due, across every tenant.

        Deliberately unscoped: the worker scans all tenants in one pass and
        re-checks each match against its own tournament's lead. Callers outside
        the worker want the scoped reads.

        There is no cancellation column to filter on — cancelling a match
        deletes the row — so an unfinished match with a stage and a future time
        is live by construction.
        """
        return await (
            Match.filter(
                stage_id__isnull=False,
                scheduled_at__gte=now,
                scheduled_at__lte=window_end,
                stage_reminder_sent_at__isnull=True,
                finished_at__isnull=True,
            )
            .prefetch_related(
                'tournament', 'stage', 'players__user',
                'commentators__user', 'trackers__user', 'watchers__user',
            )
            .order_by('scheduled_at')
        )

    @staticmethod
    async def get_all(
        *,
        tournament_ids: Optional[List[int]] = None,
        stage_ids: Optional[List[int]] = None,
        include_unstaged: bool = False,
        only_upcoming: bool = False,
        user_discord_id: Optional[str] = None,
        crew_discord_id: Optional[str] = None,
        exclude_racetime: bool = False,
        match_ids: Optional[List[int]] = None,
        scheduled_window: Optional[Tuple[datetime, datetime]] = None,
        prefetch_relations: bool = True
    ) -> List[Match]:
        """
        Get matches with optional filters.

        Args:
            tournament_ids: Filter by tournament IDs
            stage_ids: Filter by stage IDs
            include_unstaged: Also keep matches with no stage; on its own
                (no ``stage_ids``) it keeps only those
            only_upcoming: Only return matches that haven't finished
            user_discord_id: Filter matches where user is a player
            crew_discord_id: Filter matches where user signed up to commentate
                or track, approved or not
            exclude_racetime: Drop matches whose tournament runs on racetime.gg
            match_ids: Restrict to these IDs. Still tenant-scoped, so an ID from
                another community narrows the result to nothing rather than
                leaking a row.
            scheduled_window: Half-open ``(start, end)`` on ``scheduled_at``; an
                unscheduled match falls outside every window
            prefetch_relations: Whether to prefetch related objects

        Returns:
            List of Match objects

        The current event's matches, except that a board filtered to one
        person (``user_discord_id`` / ``crew_discord_id``) is a personal page
        and spans every live event (cross-event).
        """
        query = (
            live_event_scoped(Match.all()) if user_discord_id or crew_discord_id
            else event_scoped(Match.all())
        )

        if match_ids:
            query = query.filter(id__in=match_ids)

        if only_upcoming:
            query = query.filter(finished_at__isnull=True)

        if scheduled_window is not None:
            query = query.filter(
                scheduled_at__gte=scheduled_window[0], scheduled_at__lt=scheduled_window[1],
            )

        if exclude_racetime:
            # ``Tournament.is_racetime_enabled`` is a Python property over the
            # ``racetime_bot`` FK, so the DB-side test is the FK being unset.
            query = query.filter(tournament__racetime_bot_id__isnull=True)

        if tournament_ids:
            query = query.filter(tournament_id__in=tournament_ids)
        
        if stage_ids and include_unstaged:
            query = query.filter(Q(stage_id__in=stage_ids) | Q(stage_id__isnull=True))
        elif stage_ids:
            query = query.filter(stage_id__in=stage_ids)
        elif include_unstaged:
            query = query.filter(stage_id__isnull=True)

        if user_discord_id:
            query = query.filter(players__user__discord_id=user_discord_id)

        if crew_discord_id:
            query = query.filter(
                Q(commentators__user__discord_id=crew_discord_id)
                | Q(trackers__user__discord_id=crew_discord_id)
            ).distinct()

        if prefetch_relations:
            query = query.prefetch_related(
                'tournament',
                Prefetch('players', queryset=scoped(MatchPlayers.all()).prefetch_related(_board_user())),
                'stage',
                'generated_seed',
                Prefetch('commentators', queryset=scoped(Commentator.all()).prefetch_related(_board_user())),
                Prefetch('trackers', queryset=scoped(Tracker.all()).prefetch_related(_board_user())),
                # The bracket seam, for the schedule's "part of <bracket>" link.
                # A batched prefetch, so it costs a fixed few queries rather
                # than one per row; most matches have no game row at all. The
                # ``__games`` hop is one more batched query and is what lets the
                # row show the series standing ("Game 2 of 3 · 1-0") without an
                # N+1 (U4).
                'bracket_match_game__bracket_match__bracket',
                'bracket_match_game__bracket_match__games',
                # Each game's match, so "Game 2 of 3" follows the games' times.
                'bracket_match_game__bracket_match__games__match',
            )
        
        return await query.order_by('scheduled_at')
    
    @staticmethod
    async def create(
        tournament_id: int,
        scheduled_at: datetime,
        comment: Optional[str] = None,
        stage_id: Optional[int] = None,
        is_stream_candidate: bool = False,
        title: Optional[str] = None,
        description: Optional[str] = None,
        bracket_placeholders: Optional[list] = None,
    ) -> Match:
        """
        Create a new match.

        Args:
            tournament_id: Tournament ID
            scheduled_at: When the match is scheduled
            comment: Optional comment
            stage_id: Optional stage ID
            is_stream_candidate: Whether this match is a stream candidate
            title: Optional display label (names the Discord event and race room)

        Returns:
            Created Match object
        """
        match = await Match.create(
            tenant_id=current_tenant_id(),
            event_id=await parent_event_id(Tournament, parent_id=tournament_id),
            tournament_id=tournament_id,
            scheduled_at=scheduled_at,
            comment=comment,
            stage_id=stage_id,
            is_stream_candidate=is_stream_candidate,
            title=title,
            description=description,
            bracket_placeholders=bracket_placeholders,
        )
        return match
    
    @staticmethod
    async def create_crew_hold(
        tournament_id: int, source: str, key: int, title: str, placeholders: list,
    ) -> Match:
        """A match with no players and no time, holding crew signups for one bracket matchup."""
        return await Match.create(
            tenant_id=current_tenant_id(), tournament_id=tournament_id, scheduled_at=None,
            event_id=await parent_event_id(Tournament, parent_id=tournament_id),
            title=title, bracket_placeholders=placeholders,
            crew_hold_source=source, crew_hold_key=key,
        )

    @staticmethod
    async def get_crew_hold(source: str, key: int) -> Optional[Match]:
        """The hold for one bracket matchup (event-scope: exempt — one row per matchup)."""
        return await scoped(Match.filter(crew_hold_source=source, crew_hold_key=key)).first()

    @staticmethod
    async def list_crew_holds() -> List[Match]:
        return await event_scoped(Match.filter(crew_hold_source__isnull=False)).prefetch_related(
            'tournament', 'commentators', 'trackers',
        )

    @staticmethod
    async def update(
        match: Match,
        **fields
    ) -> Match:
        """
        Update a match with given fields.
        
        Args:
            match: Match object to update
            **fields: Fields to update
            
        Returns:
            Updated Match object
        """
        for field, value in fields.items():
            setattr(match, field, value)

        # Only the named columns: the caller's row may predate a check-in or a
        # start, and a whole-row save would write those back to NULL. Clearing a
        # lifecycle timestamp therefore has to be asked for by name.
        if fields:
            fk = Match._meta.fk_fields
            await match.save(update_fields=[f'{f}_id' if f in fk else f for f in fields])
        return match
    
    @staticmethod
    async def stamp_if_unset(match: Match, field: str, when: datetime) -> bool:
        """Set one lifecycle timestamp only if it is still empty; True if this call set it.

        The compare-and-set that keeps two taps on the same transition (a second
        proctor, a double-tapped phone) from both passing the precondition and
        both stamping, auditing and notifying. Writes that column alone, so a
        caller holding a stale row cannot overwrite what changed since it loaded.
        Scoped by the row's own tenant: the match was already loaded in scope.
        """
        updated = await scoped(
            Match.filter(id=match.id, **{f'{field}__isnull': True}),
            tenant_id=match.tenant_id,  # type: ignore[attr-defined]
        ).update(**{field: when})
        return updated == 1

    @staticmethod
    async def delete(match: Match) -> None:
        """
        Delete a match.
        
        Args:
            match: Match object to delete
        """
        await match.delete()
    
    @staticmethod
    async def add_player(match: Match, user: User) -> MatchPlayers:
        """
        Add a player to a match.
        
        Args:
            match: Match object
            user: User to add as player
            
        Returns:
            Created MatchPlayers object
        """
        return await MatchPlayers.create(tenant_id=current_tenant_id(), match=match, user=user)
    
    @staticmethod
    async def remove_player(match: Match, user: User) -> None:
        """
        Remove a player from a match.
        
        Args:
            match: Match object
            user: User to remove
        """
        player = await scoped(MatchPlayers.filter(match=match, user=user)).first()
        if player:
            await player.delete()
    
    @staticmethod
    async def clear_seed_ready(match_id: int) -> None:
        """Forget every player's Ready for seed on a match."""
        await scoped(MatchPlayers.filter(match_id=match_id)).update(
            seed_ready_at=None, seed_nudged_at=None,
        )

    @staticmethod
    async def bracket_link_candidates(tournament_ids: List[int]) -> List[Match]:
        """Unconfirmed matches in these tournaments that back no bracket matchup."""
        if not tournament_ids:
            return []
        linked = {
            *await scoped(ChallongeMatch.filter(
                tournament_id__in=tournament_ids, match_id__isnull=False,
            )).values_list('match_id', flat=True),
            *await scoped(StartggSet.filter(
                tournament_id__in=tournament_ids, match_id__isnull=False,
            )).values_list('match_id', flat=True),
            *await scoped(BracketMatchGame.filter(
                bracket_match__bracket__tournament_id__in=tournament_ids, match_id__isnull=False,
            )).values_list('match_id', flat=True),
        }
        return await scoped(Match.filter(
            tournament_id__in=tournament_ids, confirmed_at__isnull=True, set_first_id__isnull=True,
        )).exclude(id__in=list(linked)).prefetch_related('players__user', 'stage').order_by('scheduled_at', 'id')

    @staticmethod
    async def set_games(first_id: int) -> List[Match]:
        """Every game of the best-of set that match ``first_id`` leads, in game order."""
        return await scoped(
            Match.filter(Q(id=first_id) | Q(set_first_id=first_id))
        ).prefetch_related('players').order_by('set_game', 'id')

    @staticmethod
    async def games_of_sets(first_ids: List[int]) -> List[Match]:
        """Every game of the sets these matches lead, for working out game numbers."""
        if not first_ids:
            return []
        return await scoped(Match.filter(Q(id__in=first_ids) | Q(set_first_id__in=first_ids)))

    @staticmethod
    async def games_of_sets_with_stage(first_ids: List[int]) -> List[Match]:
        """:meth:`games_of_sets` with each game's stage, for listing the bookings."""
        if not first_ids:
            return []
        return await scoped(
            Match.filter(Q(id__in=first_ids) | Q(set_first_id__in=first_ids))
        ).prefetch_related('stage')

    @staticmethod
    async def set_first_id_of(match_id: int) -> Optional[int]:
        """What the database says ``match_id``'s set is led by now, which a hand-off can change."""
        rows = await scoped(Match.filter(id=match_id)).values_list('set_first_id', flat=True)
        return cast(Optional[int], rows[0]) if rows else None

    @staticmethod
    async def repoint_set(old_first_id: int, new_first: Match) -> None:
        """Make ``new_first`` lead the set ``old_first_id`` led."""
        await scoped(Match.filter(id=new_first.id)).update(set_first_id=None)
        await scoped(Match.filter(set_first_id=old_first_id)).exclude(id=new_first.id).update(
            set_first_id=new_first.id,
        )
        new_first.set_first_id = None

    @staticmethod
    async def set_followers(first_id: int) -> List[Match]:
        """The games after game 1 of the set match ``first_id`` leads."""
        return await scoped(Match.filter(set_first_id=first_id)).order_by('set_game', 'id')

    @staticmethod
    async def get_players(match: "Match | int") -> List[MatchPlayers]:
        """
        Get all players for a match.

        Args:
            match: Match object or its id

        Returns:
            List of MatchPlayers
        """
        match_id = match.id if isinstance(match, Match) else match
        return await scoped(MatchPlayers.filter(match_id=match_id)).prefetch_related('user')

    @staticmethod
    async def occupied_stations(exclude_match_id: Optional[int] = None) -> dict:
        """``{station label: match id}`` for matches in play right now.

        "In play" is seated-and-not-finished: a station frees up when the match
        at it finishes, not when it is confirmed.
        """
        query = scoped(MatchPlayers.filter(
            assigned_station__isnull=False,
            match__seated_at__isnull=False,
            match__finished_at__isnull=True,
        ))
        if exclude_match_id is not None:
            query = query.exclude(match_id=exclude_match_id)
        rows = await query.values('assigned_station', 'match_id')
        return {r['assigned_station']: r['match_id'] for r in rows if r['assigned_station']}

    @staticmethod
    async def get_by_ids_with_players(match_ids) -> List[Match]:
        """The named matches with the relations a human-readable label needs.

        One query for a set of ids, so a caller labelling several matches at
        once (the station picker's occupied options) does not do it per row.
        """
        ids = list(match_ids)
        if not ids:
            return []
        return await scoped(Match.filter(id__in=ids)).prefetch_related(
            'tournament', 'players', 'players__user'
        )

    @staticmethod
    async def get_by_ids_with_presets(match_ids) -> List[Match]:
        """The named matches with their tournament's two presets attached.

        For the player board's hard-preset column, which needs both preset names
        and the roster for every visible row — one query rather than one per row.
        """
        ids = list(match_ids)
        if not ids:
            return []
        return await scoped(Match.filter(id__in=ids)).prefetch_related(
            'tournament', 'tournament__preset', 'tournament__hard_preset',
            'players', 'players__user',
        )

    @staticmethod
    async def get_all_for_schedule() -> List[Match]:
        """
        Get all matches for the public schedule view, ordered by scheduled time.

        Returns:
            List of matches with tournament/players/stage/seed prefetched
        """
        return await event_scoped(Match.all()).prefetch_related(
            'tournament', 'players', 'stage', 'generated_seed'
        ).order_by('scheduled_at')

    @staticmethod
    async def scheduled_between(
        start: datetime,
        end: datetime,
        exclude_finished: bool = True,
        require_stage: bool = True,
    ) -> List[Match]:
        """
        Get matches scheduled in the half-open window ``[start, end)``.

        Takes instants, not a date: which instants make up "a day" depends on a
        timezone, and choosing one is a business rule the service layer owns.

        Args:
            start: Inclusive lower bound (aware UTC)
            end: Exclusive upper bound (aware UTC)
            exclude_finished: If True, exclude matches that are finished
            require_stage: If True, only include matches with a stage

        Returns:
            List of matches with all related data prefetched
        """
        query = event_scoped(Match.filter(
            scheduled_at__gte=start,
            scheduled_at__lt=end
        ))

        if exclude_finished:
            query = query.filter(finished_at=None)

        if require_stage:
            query = query.exclude(stage=None)

        return await query.prefetch_related(
            'tournament', 'stage', 'players', 'players__user',
            'commentators', 'commentators__user', 'trackers', 'trackers__user'
        ).order_by('scheduled_at')

    @staticmethod
    async def commitments_for_user(
        user_id: int, start: datetime, end: datetime, exclude_match_id: Optional[int] = None,
    ) -> List[Match]:
        """Every match in ``[start, end)`` this user is already committed to.

        Committed means any of the three roles — a commentator cannot cover two
        streams at once *and* cannot commentate a match they are racing in. One
        query per role would be three; a single OR'd filter is one, deduped by id
        because a person could hold two roles on the same match.

        Spans every live event (cross-event): a person can't be in two places
        at once whichever event each match belongs to.
        """
        query = live_event_scoped(Match.filter(
            Q(players__user_id=user_id)
            | Q(commentators__user_id=user_id)
            | Q(trackers__user_id=user_id),
            scheduled_at__gte=start,
            scheduled_at__lt=end,
        ))
        if exclude_match_id is not None:
            query = query.exclude(id=exclude_match_id)
        matches = await query.prefetch_related('tournament', 'players', 'players__user').distinct()
        seen: dict = {}
        for match in matches:
            seen.setdefault(match.id, match)
        return sorted(seen.values(), key=lambda m: m.scheduled_at)

    @staticmethod
    async def get_for_player(discord_id: str) -> List[Match]:
        """
        Get all matches where the given Discord user is a player.

        Args:
            discord_id: Discord ID of the player

        Returns:
            List of matches where the player is participating, in any live
            event (cross-event: it feeds the player's own schedule)
        """
        return await live_event_scoped(Match.filter(players__user__discord_id=discord_id))

    @staticmethod
    async def unconfirmed_for_player(tournament_id: int, user_id: int) -> List[Match]:
        """This player's matches in one tournament that are not confirmed yet."""
        return await scoped(Match.filter(
            tournament_id=tournament_id, players__user_id=user_id, confirmed_at__isnull=True,
        )).distinct().order_by('scheduled_at', 'id').prefetch_related(
            'tournament', 'players', 'players__user', 'stage',
        )

    @staticmethod
    async def get_upcoming_for_user(user_id: int) -> List[Match]:
        """This player's matches that have a time and have not begun.

        Scheduled but not seated, started, finished or confirmed — the set a
        player can still act on. ``tournament`` is prefetched because every
        caller so far branches on one of its per-tournament toggles.

        ``confirmed_at`` is filtered even though a confirmed match is always
        finished too: the redundancy is what keeps this in step with
        ``MatchRescheduleService._blocking_state``, which lists all four, rather
        than agreeing with it only for as long as ``confirm_match`` keeps
        requiring a finish first.

        Across every live event (cross-event): it backs the player's own
        schedule.
        """
        return await live_event_scoped(Match.filter(
            players__user_id=user_id,
            scheduled_at__isnull=False,
            seated_at__isnull=True,
            started_at__isnull=True,
            finished_at__isnull=True,
            confirmed_at__isnull=True,
        )).prefetch_related('tournament')
