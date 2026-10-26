"""Tenant Join Request Repository.

Requests to join a community. Queried **cross-tenant** on purpose, like
:class:`~models.TenantMembership`: the row is written by someone who is not in
the target tenant at all, and a user's own pending requests span communities. So
these pass tenant ids explicitly rather than through ``scoped(...)``, and
``TenantJoinRequest`` sits in ``check_tenant_scoping``'s ``EXEMPT_MODELS``.
"""

from typing import List, Optional

from application.repositories._save import save_fields
from models import JoinRequestStatus, TenantJoinRequest, User


class TenantJoinRequestRepository:
    """Cross-tenant join-request lookups + writes."""

    @staticmethod
    async def get(user_id: int, tenant_id: int) -> Optional[TenantJoinRequest]:
        return await TenantJoinRequest.get_or_none(user_id=user_id, tenant_id=tenant_id)

    @staticmethod
    async def get_by_id(request_id: int) -> Optional[TenantJoinRequest]:
        return await TenantJoinRequest.get_or_none(id=request_id).prefetch_related('user', 'tenant')

    @staticmethod
    async def upsert_pending(
        user: User, tenant_id: int, message: Optional[str],
    ) -> TenantJoinRequest:
        """Open a request, or re-open a decided one in place.

        One row per ``(user, tenant)``: a denied request is moved back to
        ``PENDING`` rather than appended to, so the table cannot be used as a
        spam log.
        """
        request, created = await TenantJoinRequest.get_or_create(
            user=user, tenant_id=tenant_id,
            defaults={'message': message, 'status': JoinRequestStatus.PENDING},
        )
        if not created:
            request.status = JoinRequestStatus.PENDING
            request.message = message
            request.decided_by = None
            request.decided_at = None
            await save_fields(request, 'status', 'message', 'decided_by', 'decided_at')
        return request

    @staticmethod
    async def list_pending(tenant_id: int) -> List[TenantJoinRequest]:
        return await TenantJoinRequest.filter(
            tenant_id=tenant_id, status=JoinRequestStatus.PENDING,
        ).order_by('created_at').prefetch_related('user')

    @staticmethod
    async def decide(
        request: TenantJoinRequest, status: JoinRequestStatus, actor: Optional[User], decided_at,
    ) -> TenantJoinRequest:
        request.status = status
        request.decided_by = actor
        request.decided_at = decided_at
        await save_fields(request, 'status', 'decided_by', 'decided_at')
        return request
