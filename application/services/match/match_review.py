"""The dispute signal: a proctor flags a recorded result, an admin resolves it.

Deliberately **just a flag**, not a workflow. There is no review state, no
assignment, no thread, and no note — the proctor raising it is standing in the
room, and writing up what happened is a conversation, not a textarea to fill in
between matches. The match stays ``Finished`` and confirming it *is* the
resolution (see ``MatchScheduleService.confirm_match``). ``clear_review`` is
the escape hatch for "looked at it, nothing to fix, not confirming yet".

Split out of ``match_service`` the way :class:`MatchRequestMixin` and
:class:`CancellationMixin` are: ``MatchReviewMixin`` is composed into
:class:`MatchService` and reaches ``_require_match`` and ``audit_service``
through that composed class.
"""

from application.events import EventType, match_live
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from models import Match, User


class MatchReviewMixin:
    async def flag_for_review(self, match_id: int, actor: User) -> Match:
        """Mark a recorded result as needing an admin's eyes before confirmation.

        Gated on ``can_run_match``: raising the flag is part of the proctor's own
        job — they are the one in the room who saw the disagreement.
        """
        match = await self._require_match(match_id)

        await AuthService.ensure(
            await AuthService.can_run_match(actor, match),
            f"User cannot flag match {match_id} for review",
        )

        if not match.finished_at:
            raise ValueError("Only a finished match can be flagged for review.")
        if match.confirmed_at:
            raise ValueError(
                "This result is already confirmed. Ask staff or the tournament admin to correct it."
            )

        match.needs_review = True
        await match.save(update_fields=['needs_review'])

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_FLAGGED_FOR_REVIEW,
            {'match_id': match.id},
            EventType.MATCH_FLAGGED_FOR_REVIEW,
            event_extra={'tournament_id': match.tournament_id},
        )

        match_live.publish(match.id)
        from application.services import opt_in_notifications
        from application.services.discord import discord_queue
        discord_queue.enqueue(opt_in_notifications.match_flagged(match.id, actor.id, actor.preferred_name))
        return match

    async def clear_review(self, match_id: int, actor: User) -> Match:
        """Drop the review flag without confirming the match.

        Gated on ``can_confirm_match``, which excludes PROCTOR: resolving a
        contested result is the admin's call, exactly as confirming is. The
        audit trail is what survives — a resolved dispute still happened.
        """
        match = await self._require_match(match_id)

        await AuthService.ensure(
            await AuthService.can_confirm_match(actor, match),
            f"User cannot clear the review flag on match {match_id}",
        )

        match.needs_review = False
        await match.save(update_fields=['needs_review'])

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_REVIEW_CLEARED,
            {'match_id': match.id},
            EventType.MATCH_REVIEW_CLEARED,
            event_extra={'tournament_id': match.tournament_id},
        )

        match_live.publish(match.id)
        return match
