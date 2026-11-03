"""
Feedback Service - Business Logic Layer

Records in-app feedback from logged-in attendees, lets staff review it, and lets
the person who sent it see what happened to it.

That last part is the loop closing. For a long time the only thing staff could
do was **Mark reviewed**: it was one-way, and the submitter — who might have
asked an actual question through the form — had no way to learn anything had
happened at all. Review is reversible now (a mis-click is a mis-click), and a
person's own submissions carry their status on their profile.

Gated by :attr:`~models.FeatureFlag.FEEDBACK`: a community can turn the whole
subsystem off, and when they do the service refuses it rather than relying on
the sidebar entry being hidden.
"""

from typing import List

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories.feedback_repository import FeedbackRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from models import FeatureFlag, Feedback, FeedbackCategory, FeedbackStatus, User

PAGE_URL_MAX_LENGTH = 512


class FeedbackService:
    """Service for in-app feedback operations."""

    def __init__(self) -> None:
        self.repository = FeedbackRepository()
        self.audit_service = AuditService()

    @staticmethod
    def _coerce_category(category) -> FeedbackCategory:
        """Resolve an arbitrary category input to a valid enum, defaulting to OTHER."""
        try:
            return FeedbackCategory(category)
        except ValueError:
            return FeedbackCategory.OTHER

    @staticmethod
    async def _ensure_member(actor: User) -> None:
        from application.services.auth_service import AuthService
        from application.services.tenant_membership_service import TenantMembershipService

        await AuthService.ensure(
            await TenantMembershipService().is_member(actor),
            'Only members of this community can send it feedback. Ask to join it first.',
        )

    @requires_feature(FeatureFlag.FEEDBACK)
    async def submit(
        self,
        actor: User,
        category,
        message: str,
        page_url: str,
    ) -> Feedback:
        """Record a feedback submission from ``actor``.

        Members only (a super-admin, who belongs to no community, passes): the
        queue is this community's, and the drawer only offers the dialog to its
        members — but UI-only gating is not gating, and this is reachable over
        REST too. A non-member is an authorization failure (``PermissionError``,
        a 403 over REST), not bad input.
        """
        message = (message or '').strip()
        if not message:
            raise ValueError("Feedback message is required.")
        await self._ensure_member(actor)

        category = self._coerce_category(category)
        page_url = (page_url or '')[:PAGE_URL_MAX_LENGTH]

        feedback = await self.repository.create(
            user=actor,
            category=category,
            message=message,
            page_url=page_url,
        )

        await self.audit_service.write_log(
            actor,
            AuditActions.FEEDBACK_SUBMITTED,
            {'feedback_id': feedback.id, 'category': category.value, 'page_url': page_url},
        )
        return feedback

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can review feedback.",
        )

    @requires_feature(FeatureFlag.FEEDBACK)
    async def list_recent(self, actor: User, limit: int = 200) -> List[Feedback]:
        """The community's feedback queue, newest first. Staff-only: it carries
        every submitter's name and message."""
        await self._ensure_staff(actor)
        return await self.repository.list_recent(limit)

    @requires_feature(FeatureFlag.FEEDBACK)
    async def list_mine(self, actor: User, limit: int = 25) -> List[Feedback]:
        """What ``actor`` has sent, and what has happened to it.

        No authorization check beyond being the actor: this reads a person's own
        submissions and nobody else's, which is the point of it.
        """
        return await self.repository.list_for_user(actor, limit)

    @requires_feature(FeatureFlag.FEEDBACK)
    async def set_reviewed(
        self, actor: User, feedback_id: int, reviewed: bool = True,
    ) -> Feedback:
        """Mark a submission reviewed, or put it back in the queue. Staff-only,
        like the queue itself: the response carries the whole submission.

        Reversible on purpose. Marking reviewed used to be terminal, so a
        mis-click quietly dropped a submission out of the only queue anyone
        looks at, with no way to retrieve it short of the database.
        """
        await self._ensure_staff(actor)
        feedback = require_found(await self.repository.get_by_id(feedback_id), "Feedback")

        status = FeedbackStatus.REVIEWED if reviewed else FeedbackStatus.NEW
        await self.repository.set_status(feedback, status)
        await self.audit_service.write_log(
            actor,
            AuditActions.FEEDBACK_REVIEWED if reviewed else AuditActions.FEEDBACK_REOPENED,
            {'feedback_id': feedback.id},
        )
        return feedback

    async def mark_reviewed(self, actor: User, feedback_id: int) -> Feedback:
        """Back-compat alias for :meth:`set_reviewed`."""
        return await self.set_reviewed(actor, feedback_id, reviewed=True)
