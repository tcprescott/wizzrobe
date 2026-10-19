from typing import List, Optional, Set, cast

from application.repositories._save import save_fields
from application.repositories._tenant import current_tenant_id, scoped
from models import MatchNotificationLevel, Tournament, TournamentNotificationPreference, User


class TournamentNotificationRepository:
    """Repository for TournamentNotificationPreference data access."""

    async def get_by_user_and_tournament(self, user: User, tournament: Tournament) -> Optional[TournamentNotificationPreference]:
        return await TournamentNotificationPreference.get_or_none(user=user, tournament=tournament, tenant_id=current_tenant_id())

    async def get_all_for_user(self, user: User) -> List[TournamentNotificationPreference]:
        return await scoped(TournamentNotificationPreference.filter(user=user)).prefetch_related('tournament').all()

    async def upsert(
        self,
        user: User,
        tournament: Tournament,
        match_notifications: MatchNotificationLevel,
    ) -> TournamentNotificationPreference:
        pref, _ = await TournamentNotificationPreference.get_or_create(
            tenant_id=current_tenant_id(),
            user=user,
            tournament=tournament,
        )
        pref.match_notifications = match_notifications
        await save_fields(pref, 'match_notifications')
        return pref

    async def set_staff_alerts(self, user: User, tournament: Tournament, on: bool) -> TournamentNotificationPreference:
        pref, _ = await TournamentNotificationPreference.get_or_create(
            tenant_id=current_tenant_id(),
            user=user,
            tournament=tournament,
        )
        pref.staff_alerts = on
        await pref.save(update_fields=['staff_alerts', 'updated_at'])
        return pref

    async def staff_alert_opt_out_user_ids(self, tournament_id: int) -> Set[int]:
        """Users who switched off match alerts for this tournament."""
        return set(cast(List[int], await scoped(TournamentNotificationPreference.filter(
            tournament_id=tournament_id, staff_alerts=False,
        )).values_list('user_id', flat=True)))

    async def get_match_notification_subscribers(self, tournament_id: int, has_stage: bool) -> List[User]:
        """
        Return users who qualify for a match-scheduled notification.

        'all' subscribers always qualify.
        'streamed' and 'streamed_and_candidates' subscribers only qualify when the match
        has a stage assigned.
        """
        if has_stage:
            qualifying_levels = [
                MatchNotificationLevel.ALL,
                MatchNotificationLevel.STREAMED,
                MatchNotificationLevel.STREAMED_AND_CANDIDATES,
            ]
        else:
            qualifying_levels = [MatchNotificationLevel.ALL]

        prefs = await scoped(TournamentNotificationPreference.filter(
            tournament_id=tournament_id,
            match_notifications__in=qualifying_levels,
        )).prefetch_related('user').all()

        return [
            p.user for p in prefs
            if p.user.discord_id
        ]

    async def get_stream_candidate_subscribers(self, tournament_id: int) -> List[User]:
        """Return users who opted in to stream candidate alerts."""
        prefs = await scoped(TournamentNotificationPreference.filter(
            tournament_id=tournament_id,
            match_notifications=MatchNotificationLevel.STREAMED_AND_CANDIDATES,
        )).prefetch_related('user').all()

        return [
            p.user for p in prefs
            if p.user.discord_id
        ]

    async def get_followers(self, tournament_id: int) -> List[User]:
        """Users following the tournament at any level."""
        prefs = await scoped(TournamentNotificationPreference.filter(
            tournament_id=tournament_id,
        ).exclude(match_notifications=MatchNotificationLevel.NONE)).prefetch_related('user').all()
        return [p.user for p in prefs if p.user.discord_id]
