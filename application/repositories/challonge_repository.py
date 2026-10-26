"""
Challonge Repository - Data Access Layer

Pure data access for the Challonge service-account connection, the bracket
mirror (participants + matches), and their links to wizzrobe users/matches.
No business logic.
"""

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, cast

from tortoise.expressions import Q
from tortoise.functions import Count

from application.repositories._event import event_scoped, live_event_scoped
from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id, scoped
from models import (
    ChallongeApiUsage,
    ChallongeConnection,
    ChallongeMatch,
    ChallongeMatchState,
    ChallongeParticipant,
    EventStatus,
    Match,
    Tournament,
    User,
)


class ChallongeRepository:
    """Repository for Challonge connection and bracket-mirror data.

    Every Challonge model is tenant-scoped: each tenant links its own service
    account, mirrors its own brackets, and tracks its own API quota. The
    connection is "newest row per tenant is authoritative"; usage is tallied per
    ``(tenant, period)``.
    """

    # ------------------------------------------------------------------
    # Service-account connection (newest row per tenant is authoritative)
    # ------------------------------------------------------------------
    @staticmethod
    async def get_connection() -> Optional[ChallongeConnection]:
        return await scoped(ChallongeConnection.all()).order_by('-id').first()

    @staticmethod
    async def list_all_connections() -> List[ChallongeConnection]:
        """Every tenant's newest Challonge connection — **explicitly unscoped**.

        Platform-level read for the service-health board: the health monitor runs
        with no tenant in scope and needs to survey token expiry across all
        tenants at once. Returns one row per tenant (the authoritative newest),
        mirroring the unscoped ``ApiToken``→tenant lookup pattern; do not use it
        from tenant-scoped request code, which must go through
        :meth:`get_connection`.
        """
        rows = await ChallongeConnection.all().order_by('tenant_id', '-id')
        newest: dict[int, ChallongeConnection] = {}
        for row in rows:
            newest.setdefault(row.tenant_id, row)
        return list(newest.values())

    @staticmethod
    async def save_connection(
        access_token: str,
        refresh_token: Optional[str],
        token_expires_at: Optional[datetime],
        scopes: Optional[str],
        challonge_username: Optional[str],
        connected_by: Optional[User],
    ) -> ChallongeConnection:
        existing = await ChallongeRepository.get_connection()
        if existing:
            existing.access_token = access_token
            existing.refresh_token = refresh_token
            existing.token_expires_at = token_expires_at
            existing.scopes = scopes
            existing.challonge_username = challonge_username
            existing.connected_by = connected_by
            await save_fields(
                existing, 'access_token', 'refresh_token', 'token_expires_at', 'scopes',
                'challonge_username', 'connected_by',
            )
            return existing
        return await ChallongeConnection.create(
            tenant_id=current_tenant_id(),
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
            scopes=scopes,
            challonge_username=challonge_username,
            connected_by=connected_by,
        )

    @staticmethod
    async def update_connection_tokens(
        connection: ChallongeConnection,
        access_token: str,
        refresh_token: Optional[str],
        token_expires_at: Optional[datetime],
    ) -> None:
        connection.access_token = access_token
        if refresh_token:
            connection.refresh_token = refresh_token
        connection.token_expires_at = token_expires_at
        await save_fields(connection, 'access_token', 'refresh_token', 'token_expires_at')

    @staticmethod
    async def linked_tournament_communities() -> Dict[str, int]:
        """How many of this tenant's linked tournaments, in any event, live on each Challonge community.

        Cross-event: the Challonge sign-in is the community's.
        """
        rows = await scoped(Tournament.filter(
            challonge_tournament_id__isnull=False, challonge_community__isnull=False,
        )).values_list('challonge_community', flat=True)
        counts: Dict[str, int] = {}
        for community in rows:
            if isinstance(community, str) and community:
                counts[community] = counts.get(community, 0) + 1
        return counts

    @staticmethod
    async def auto_sync_tournaments() -> List[Tournament]:
        """The current event's active linked tournaments whose bracket isn't finished on Challonge."""
        return await event_scoped(Tournament.filter(
            Q(challonge_state__isnull=True) | Q(challonge_state__not='complete'),
            challonge_tournament_id__isnull=False, is_active=True,
        )).order_by('id')

    @staticmethod
    async def live_auto_sync_count() -> int:
        """How many tournaments in any live event :meth:`auto_sync_tournaments` would sync.

        Cross-event: the sync interval is the community's setting.
        """
        return await live_event_scoped(Tournament.filter(
            Q(challonge_state__isnull=True) | Q(challonge_state__not='complete'),
            challonge_tournament_id__isnull=False, is_active=True,
        )).count()

    @staticmethod
    async def linked_tenant_ids() -> Set[int]:
        """Cross-tenant and cross-event: every tenant with an active linked bracket, for the auto-sync worker."""
        rows = await Tournament.filter(
            challonge_tournament_id__isnull=False, is_active=True,
        ).values_list('tenant_id', flat=True)
        return {r for r in rows if isinstance(r, int)}  # type: ignore[misc]

    @staticmethod
    async def clear_connection() -> int:
        return await scoped(ChallongeConnection.all()).delete()

    # ------------------------------------------------------------------
    # Participants
    # ------------------------------------------------------------------
    @staticmethod
    async def upsert_participant(
        tournament: Tournament,
        challonge_participant_id: str,
        name: Optional[str],
        challonge_user_id: Optional[str],
        user: Optional[User],
        active: bool = True,
    ) -> ChallongeParticipant:
        participant, _ = await ChallongeParticipant.get_or_create(
            tenant_id=current_tenant_id(),
            tournament=tournament,
            challonge_participant_id=challonge_participant_id,
            defaults={'name': name, 'challonge_user_id': challonge_user_id, 'user': user, 'active': active},
        )
        participant.name = name
        participant.challonge_user_id = challonge_user_id
        participant.user = user
        participant.active = active
        await save_fields(participant, 'name', 'challonge_user_id', 'user', 'active')
        return participant

    @staticmethod
    async def set_participant_active(participant: ChallongeParticipant, active: bool) -> None:
        participant.active = active
        await save_fields(participant, 'active')

    @staticmethod
    async def resolve_users_by_challonge_ids(challonge_user_ids: List[str]) -> dict:
        """Map ``challonge_user_id -> User`` for the linked ids, in one query.

        Lets bracket sync resolve every participant's wizzrobe user up-front
        instead of a ``User.get_or_none`` round-trip per participant.
        """
        ids = [c for c in challonge_user_ids if c]
        if not ids:
            return {}
        rows = await User.filter(challonge_user_id__in=list(set(ids)))
        return {u.challonge_user_id: u for u in rows}

    @staticmethod
    async def get_participant(
        tournament: Tournament, challonge_participant_id: str,
    ) -> Optional[ChallongeParticipant]:
        return await ChallongeParticipant.get_or_none(
            tournament=tournament, challonge_participant_id=challonge_participant_id,
            tenant_id=current_tenant_id(),
        ).prefetch_related('user')

    @staticmethod
    async def list_participants(tournament: Tournament) -> List[ChallongeParticipant]:
        return await scoped(ChallongeParticipant.filter(tournament=tournament)).prefetch_related('user')

    @staticmethod
    async def count_unmatched_participants(tournament_ids: List[int]) -> Dict[int, int]:
        """Entrants with no Wizzrobe user, per tournament; tournaments with none are left out."""
        if not tournament_ids:
            return {}
        rows = await scoped(ChallongeParticipant.filter(
            tournament_id__in=tournament_ids, user_id__isnull=True,
        )).annotate(n=Count('id')).group_by('tournament_id').values('tournament_id', 'n')
        return {r['tournament_id']: r['n'] for r in rows}

    @staticmethod
    async def get_participant_by_id(participant_pk: int) -> Optional[ChallongeParticipant]:
        return await scoped(ChallongeParticipant.filter(id=participant_pk)).prefetch_related(
            'user', 'tournament',
        ).first()

    @staticmethod
    async def set_participant_user(
        participant: ChallongeParticipant, user: Optional[User], manual: bool,
    ) -> None:
        participant.user = user
        participant.user_assigned_manually = manual
        await participant.save(update_fields=['user_id', 'user_assigned_manually', 'updated_at'])

    @staticmethod
    async def participants_for_challonge_account(challonge_user_id: str) -> List[ChallongeParticipant]:
        """Entrants on this community's brackets that carry the given Challonge account."""
        return await scoped(ChallongeParticipant.filter(
            challonge_user_id=challonge_user_id,
        )).prefetch_related('user', 'tournament')

    @staticmethod
    async def get_participant_for_user(
        tournament: Tournament, user: User,
    ) -> Optional[ChallongeParticipant]:
        return await scoped(
            ChallongeParticipant.filter(tournament=tournament, user=user)
        ).first()

    @staticmethod
    async def participants_by_tournament(
        tournament_ids: List[int],
    ) -> Dict[int, List[ChallongeParticipant]]:
        rows = await scoped(
            ChallongeParticipant.filter(tournament_id__in=tournament_ids)
        ).prefetch_related('user')
        grouped: Dict[int, List[ChallongeParticipant]] = {tid: [] for tid in tournament_ids}
        for row in rows:
            grouped.setdefault(row.tournament_id, []).append(row)
        return grouped

    @staticmethod
    async def delete_participant(participant: ChallongeParticipant) -> None:
        await participant.delete()

    @staticmethod
    async def participant_tournament_ids_for_user(user: User) -> Set[int]:
        rows = await scoped(ChallongeParticipant.filter(user=user)).values_list('tournament_id', flat=True)
        return set(rows)

    # ------------------------------------------------------------------
    # Matches
    # ------------------------------------------------------------------
    @staticmethod
    async def upsert_match(
        tournament: Tournament,
        challonge_match_id: str,
        round_: Optional[int],
        state: ChallongeMatchState,
        participant1: Optional[ChallongeParticipant],
        participant2: Optional[ChallongeParticipant],
        winner_participant: Optional[ChallongeParticipant],
        group_stage: bool = False,
        slots: Optional[Dict[str, Optional[str]]] = None,
    ) -> ChallongeMatch:
        match, _ = await ChallongeMatch.get_or_create(
            tenant_id=current_tenant_id(),
            tournament=tournament,
            challonge_match_id=challonge_match_id,
            defaults={'round': round_, 'state': state},
        )
        match.round = round_
        match.state = state
        match.participant1 = participant1
        match.participant2 = participant2
        match.winner_participant = winner_participant
        if group_stage:
            match.group_stage = True
        # identifier and slot{1,2}_prereq / slot{1,2}_label, when the caller knows them.
        for key, value in (slots or {}).items():
            setattr(match, key, value)
        await save_fields(
            match, 'round', 'state', 'participant1', 'participant2', 'winner_participant', 'group_stage',
            *(slots or {}),
        )
        return match

    @staticmethod
    async def unfinished_match_ids(tournament: Tournament) -> Set[str]:
        """Remote ids of the mirrored matches not yet complete, to tell what a sync just finished."""
        rows = await scoped(ChallongeMatch.filter(tournament=tournament)).exclude(
            state=ChallongeMatchState.COMPLETE,
        ).values_list('challonge_match_id', flat=True)
        return set(cast(Iterable[str], rows))

    @staticmethod
    async def matches_by_remote_ids(tournament: Tournament, remote_ids: Iterable[str]) -> List[ChallongeMatch]:
        """Mirrored matches with their entrants, winner and any booked match's players loaded."""
        return await scoped(ChallongeMatch.filter(
            tournament=tournament, challonge_match_id__in=list(remote_ids),
        )).prefetch_related(
            'tournament', 'participant1__user', 'participant2__user', 'winner_participant', 'match__players',
        )

    @staticmethod
    async def booked_matches_for_participant(participant: ChallongeParticipant) -> List[ChallongeMatch]:
        """Bracket matches this entrant plays that have a Wizzrobe match attached."""
        return await scoped(ChallongeMatch.filter(
            Q(participant1=participant) | Q(participant2=participant), match_id__isnull=False,
        )).prefetch_related('participant1', 'participant2', 'match')

    @staticmethod
    async def list_placeholder_bookings(tournament: Tournament) -> List[ChallongeMatch]:
        """Matchups booked before their players were known, with the booking attached."""
        rows = await scoped(ChallongeMatch.filter(
            tournament=tournament, match_id__isnull=False,
        )).prefetch_related('tournament', 'match', 'participant1__user', 'participant2__user')
        return [r for r in rows if r.match.bracket_placeholders is not None]

    @staticmethod
    async def unscheduled_pending_matches(tournament_ids: Optional[List[int]] = None) -> List[ChallongeMatch]:
        """Unbooked elimination matchups whose players aren't both decided yet."""
        qs = scoped(ChallongeMatch.filter(
            state=ChallongeMatchState.PENDING, match_id__isnull=True, tournament__is_active=True,
            group_stage=False,
        ))
        if tournament_ids is not None:
            qs = qs.filter(tournament_id__in=tournament_ids)
        return await qs.prefetch_related(
            'tournament', 'participant1__user', 'participant2__user',
        ).order_by('tournament_id', 'round', 'id')

    @staticmethod
    async def list_matches(tournament: Tournament) -> List[ChallongeMatch]:
        return await scoped(ChallongeMatch.filter(tournament=tournament)).prefetch_related(
            'tournament', 'participant1__user', 'participant2__user',
        )

    @staticmethod
    async def get_match(challonge_match_id_pk: int) -> Optional[ChallongeMatch]:
        return await ChallongeMatch.get_or_none(id=challonge_match_id_pk, tenant_id=current_tenant_id()).prefetch_related(
            'tournament', 'participant1__user', 'participant2__user', 'match',
        )

    @staticmethod
    async def get_challonge_match_for_match(match: Match) -> Optional[ChallongeMatch]:
        return await ChallongeMatch.get_or_none(match=match, tenant_id=current_tenant_id()).prefetch_related(
            'tournament', 'participant1__user', 'participant2__user',
        )

    @staticmethod
    async def completed_for_matches(match_ids: List[int]) -> List[ChallongeMatch]:
        """Booked bracket matches Challonge already has a result for."""
        if not match_ids:
            return []
        return await scoped(ChallongeMatch.filter(
            match_id__in=match_ids, state=ChallongeMatchState.COMPLETE,
        )).prefetch_related('winner_participant', 'participant1', 'participant2')

    @staticmethod
    async def link_match(challonge_match: ChallongeMatch, match: Match) -> None:
        challonge_match.match = match
        await save_fields(challonge_match, 'match')

    @staticmethod
    async def count_participants(tournament: Tournament) -> int:
        return await scoped(ChallongeParticipant.filter(tournament=tournament)).count()

    @staticmethod
    async def count_matches(tournament: Tournament) -> int:
        return await scoped(ChallongeMatch.filter(tournament=tournament)).count()

    @staticmethod
    async def delete_mirror(tournament: Tournament) -> tuple:
        """Drop a tournament's mirrored participants and matches. Returns counts.

        The scheduled ``Match`` rows a ``ChallongeMatch`` pointed at are NOT
        touched — they are the community's own schedule and outlive the mirror.
        """
        matches = await scoped(ChallongeMatch.filter(tournament=tournament)).delete()
        participants = await scoped(
            ChallongeParticipant.filter(tournament=tournament)
        ).delete()
        return participants, matches

    @staticmethod
    async def prune_mirror(
        tournament: Tournament, keep_participant_ids: Set[str], keep_match_ids: Set[str],
    ) -> tuple:
        """Drop mirrored participants and matches Challonge no longer has. Returns counts.

        As with ``delete_mirror``, a scheduled ``Match`` a dropped
        ``ChallongeMatch`` pointed at stays on the schedule.
        """
        matches = await scoped(
            ChallongeMatch.filter(tournament=tournament)
            .exclude(challonge_match_id__in=list(keep_match_ids))
        ).delete()
        participants = await scoped(
            ChallongeParticipant.filter(tournament=tournament)
            .exclude(challonge_participant_id__in=list(keep_participant_ids))
        ).delete()
        return participants, matches

    @staticmethod
    async def record_sync(
        tournament: Tournament, when: datetime, state: Optional[str], group_stage: bool,
        tournament_type: Optional[str] = None,
    ) -> None:
        tournament.challonge_last_synced_at = when
        tournament.challonge_state = state  # type: ignore[assignment]
        tournament.challonge_group_stage = group_stage
        tournament.challonge_tournament_type = (tournament_type or '')[:32] or None  # type: ignore[assignment]
        await tournament.save(update_fields=[
            'challonge_last_synced_at', 'challonge_state', 'challonge_group_stage',
            'challonge_tournament_type',
        ])

    # ------------------------------------------------------------------
    # API-usage tally (per UTC calendar month)
    # ------------------------------------------------------------------
    @staticmethod
    def _current_period() -> str:
        return datetime.now(timezone.utc).strftime('%Y-%m')

    @staticmethod
    async def increment_api_usage(count: int = 1) -> None:
        period = ChallongeRepository._current_period()
        usage, _ = await ChallongeApiUsage.get_or_create(
            tenant_id=current_tenant_id(), period=period, defaults={'request_count': 0},
        )
        usage.request_count += count
        await usage.save(update_fields=['request_count', 'updated_at'])

    @staticmethod
    async def get_monthly_usage(period: Optional[str] = None) -> int:
        period = period or ChallongeRepository._current_period()
        usage = await ChallongeApiUsage.get_or_none(period=period, tenant_id=current_tenant_id())
        return usage.request_count if usage else 0

    @staticmethod
    async def unscheduled_open_matches_for_user(user: User) -> List[ChallongeMatch]:
        """Open bracket matches the user is in that aren't yet scheduled in wizzrobe.

        Across every live event (cross-event: it feeds the player's own schedule).
        """
        return await scoped(ChallongeMatch.filter(
            Q(participant1__user=user) | Q(participant2__user=user),
            state=ChallongeMatchState.OPEN,
            match_id__isnull=True,
            tournament__event__status=EventStatus.LIVE,
        )).prefetch_related(
            'tournament', 'participant1__user', 'participant2__user',
        )

    @staticmethod
    async def open_set_matches_for_user(user: User) -> List[ChallongeMatch]:
        """Open bracket matches the user is in whose best-of set has some games booked.

        Across every live event (cross-event: it feeds the player's own schedule).
        """
        return await scoped(ChallongeMatch.filter(
            Q(participant1__user=user) | Q(participant2__user=user),
            state=ChallongeMatchState.OPEN, match__set_best_of__gt=1,
            tournament__event__status=EventStatus.LIVE,
        )).prefetch_related(
            'tournament', 'participant1__user', 'participant2__user',
        )

    @staticmethod
    async def open_set_matches(tournament_ids: Optional[List[int]] = None) -> List[ChallongeMatch]:
        """Every open bracket match whose best-of set has some games booked; ``None`` means all."""
        qs = scoped(ChallongeMatch.filter(
            state=ChallongeMatchState.OPEN, match__set_best_of__gt=1, tournament__is_active=True,
        ))
        if tournament_ids is not None:
            qs = qs.filter(tournament_id__in=tournament_ids)
        return await qs.prefetch_related(
            'tournament', 'participant1__user', 'participant2__user',
        ).order_by('tournament_id', 'round', 'id')

    @staticmethod
    async def move_match_link(old_match_id: int, new_match_id: int) -> None:
        await scoped(ChallongeMatch.filter(match_id=old_match_id)).update(match_id=new_match_id)

    @staticmethod
    async def unscheduled_open_matches(tournament_ids: Optional[List[int]] = None) -> List[ChallongeMatch]:
        """Every open, unbooked bracket match in active tournaments; ``None`` means all of them."""
        qs = scoped(ChallongeMatch.filter(
            state=ChallongeMatchState.OPEN, match_id__isnull=True, tournament__is_active=True,
            participant1_id__isnull=False, participant2_id__isnull=False,
        ))
        if tournament_ids is not None:
            qs = qs.filter(tournament_id__in=tournament_ids)
        return await qs.prefetch_related(
            'tournament', 'participant1__user', 'participant2__user',
        ).order_by('tournament_id', 'round', 'id')
