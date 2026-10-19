"""
Member Details Repository - Data Access Layer

The reads behind Admin → Members' member sheet: one person's footprint in the
current community. Every query is scoped to the ambient tenant except the
person's push devices, which belong to the account rather than a community.
"""

from datetime import datetime
from typing import List, Optional

from application.repositories._tenant import current_tenant_id, scoped
from models import (
    CheckInEntrant,
    EquipmentLoan,
    Match,
    SentMessage,
    SentMessageStatus,
    TenantMembership,
    Tournament,
    TournamentPlayers,
    VolunteerAssignment,
    WebPushSubscription,
)


class MemberDetailsRepository:
    @staticmethod
    async def membership(user_id: int) -> Optional[TenantMembership]:
        return await TenantMembership.get_or_none(user_id=user_id, tenant_id=current_tenant_id())

    @staticmethod
    async def admin_tournament_names(user_id: int) -> List[str]:
        return await scoped(Tournament.filter(admins__id=user_id)).order_by('name').values_list('name', flat=True)  # type: ignore[return-value]

    @staticmethod
    async def coordinated_tournament_names(user_id: int) -> List[str]:
        return await scoped(
            Tournament.filter(crew_coordinators__id=user_id),
        ).order_by('name').values_list('name', flat=True)  # type: ignore[return-value]

    @staticmethod
    async def entered_tournament_names(user_id: int) -> List[str]:
        return await scoped(
            TournamentPlayers.filter(user_id=user_id),
        ).order_by('tournament__name').values_list('tournament__name', flat=True)  # type: ignore[return-value]

    @staticmethod
    async def upcoming_matches(user_id: int, now: datetime, limit: int) -> List[Match]:
        return await scoped(Match.filter(
            players__user_id=user_id, scheduled_at__gte=now, finished_at__isnull=True,
        )).order_by('scheduled_at').limit(limit).prefetch_related('tournament', 'players__user')

    @staticmethod
    async def upcoming_shifts(user_id: int, now: datetime, limit: int) -> List[VolunteerAssignment]:
        return await scoped(VolunteerAssignment.filter(
            user_id=user_id, shift__ends_at__gte=now,
        )).order_by('shift__starts_at').limit(limit).prefetch_related('shift__position')

    @staticmethod
    async def open_loans(user_id: int) -> List[EquipmentLoan]:
        return await scoped(EquipmentLoan.filter(
            borrower_id=user_id, checked_in_at__isnull=True,
        )).order_by('checked_out_at').prefetch_related('equipment')

    @staticmethod
    async def check_in_entries(user_id: int) -> List[CheckInEntrant]:
        return await scoped(CheckInEntrant.filter(
            user_id=user_id, withdrawn_at__isnull=True,
        )).order_by('-event__created_at').prefetch_related('event')

    @staticmethod
    async def recent_messages(user_id: int, limit: int) -> List[SentMessage]:
        return await scoped(SentMessage.filter(recipient_id=user_id)).order_by('-created_at', '-id').limit(limit)

    @staticmethod
    async def last_delivered_at(user_id: int) -> Optional[datetime]:
        row = await scoped(SentMessage.filter(
            recipient_id=user_id, status=SentMessageStatus.DELIVERED,
        )).order_by('-created_at').first().values('created_at')
        return row['created_at'] if row else None

    @staticmethod
    async def push_device_count(user_id: int) -> int:
        # Deliberately unscoped: a device subscription belongs to the account,
        # and it is what every community's DMs are mirrored to.
        return await WebPushSubscription.filter(user_id=user_id).count()
