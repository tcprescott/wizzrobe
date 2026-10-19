
"""Reschedule-request notifications: who is told a request exists, and how it went.

Split out of ``match_reschedule_service`` as pure code motion when that module
approached the file-length budget, following the ``_schedule_notifications``
convention next door: :class:`RescheduleNotificationMixin` is composed into
:class:`MatchRescheduleService` and reaches its sibling helpers through that one
composed class, so every caller keeps using the service unchanged.

Every method here is best-effort — a DM failure is logged and swallowed, because
a request that was submitted or decided stays submitted or decided whether or
not Discord took the message.

Two audience rules live here rather than at the call sites. The deciders are
STAFF ∪ the tournament's own admins, because ``_decidable`` gates on
``can_crud_match`` and DMing only STAFF left a TA-run tournament with nobody
told. And an approval that *moved* the match sends nothing back: ``update_match``
has already fanned the reschedule out to both players.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Sequence, Tuple

from application.services.notification_catalog import CANCEL_APPROVED, PLAYERS, RESCHEDULE_DECLINED, RESCHEDULE_REQUEST
from models import (
    Match,
    MatchRescheduleRequest,
    NotificationRecipient,
    RescheduleRequestKind,
    StaffRequestKind,
    User,
)

logger = logging.getLogger(__name__)


class RescheduleNotificationMixin:
    """The DM fan-out for a request's submission and its decision."""

    async def _notify_submitted(
        self,
        request: MatchRescheduleRequest,
        match: Match,
        tournament_name: str,
        requester: User,
    ) -> None:
        """Tell staff there is something to decide, and the opponent it was asked."""
        try:
            await self._send_submitted(request, match, tournament_name, requester)
        except Exception:
            logger.exception('reschedule request notification failed for %s', request.id)

    async def _send_submitted(
        self,
        request: MatchRescheduleRequest,
        match: Match,
        tournament_name: str,
        requester: User,
    ) -> None:
        from application.services import notification_links, staff_request_dms
        from application.services.discord import DiscordService, discord_queue
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import (
            COLOR_RESCHEDULED,
            notification_embed,
            time_field,
        )
        from application.utils.discord_messages_reschedule import (
            reschedule_opponent_dm,
            reschedule_requested_dm,
        )

        community = await TenantService.current_community_name()
        cancel = request.kind is RescheduleRequestKind.CANCEL
        current_display = time_field(match.scheduled_at)
        proposed_display = time_field(request.proposed_at)
        names = [p.user.preferred_name for p in match.players]  # type: ignore[attr-defined]
        service = DiscordService()

        staff_body = reschedule_requested_dm(
            tournament_name, requester.preferred_name,
            kind_cancel=cancel, current_display=current_display,
            proposed_display=proposed_display, reason=request.reason,
            player_names=names,
        )
        staff_embed = notification_embed(
            title='🔁 Reschedule request',
            color=COLOR_RESCHEDULED,
            community_name=community,
            description=staff_body,
        )
        # The decision dialog itself, not the board: the reason and the proposed
        # time are the whole message and they live in the dialog.
        staff_link = await notification_links.admin_reschedule_request(request.id)
        await staff_request_dms.send(
            StaffRequestKind.RESCHEDULE_REQUEST, request.id, await self._deciders(match),
            staff_body, embed=staff_embed, link=staff_link,
        )

        opponents = self._opponents(match, requester)
        # One column records agreement, so it can only mean "the other player".
        # A match with more than two players skips the signal entirely rather
        # than recording one person's yes as if it were everyone's.
        if len(opponents) != 1:
            return
        opponent = opponents[0]
        if not opponent.discord_id:
            return
        opponent_body = reschedule_opponent_dm(
            tournament_name, requester.preferred_name,
            kind_cancel=cancel, current_display=current_display,
            proposed_display=proposed_display, reason=request.reason,
        )
        opponent_embed = notification_embed(
            title='🔁 Your opponent asked to reschedule',
            color=COLOR_RESCHEDULED,
            community_name=community,
            description=opponent_body,
        )
        # The request itself with its Agree button, not the board: the board has
        # no Agree, and the web-push copy of this DM has no Discord button, so
        # landing anywhere else asked for something the reader could not do.
        discord_queue.enqueue(service.send_dm_with_reschedule_agree_button(
            int(opponent.discord_id), opponent_body, request.id,
            embed=opponent_embed,
            link=await notification_links.player_agree(request.id), notice=RESCHEDULE_REQUEST.to(PLAYERS),
        ))

    async def _mark_staff_dms(
        self,
        request: MatchRescheduleRequest,
        outcome: str,
        *,
        actor: User,
        tournament_name: str,
    ) -> None:
        """Edit the DMs the deciders got to say this request is settled.

        ``outcome`` is ``approved``, ``declined``, ``withdrawn`` (``actor`` is
        the requester) or ``superseded`` (``actor`` approved a different
        request on the same match).
        """
        try:
            from application.services import notification_links, staff_request_dms
            from application.services.tenant_service import TenantService
            from application.utils import discord_messages_staff_requests as copy
            from application.utils.discord_embeds import (
                COLOR_CANCELLED,
                COLOR_CONFIRMED,
                COLOR_FINISHED,
            )

            community = await TenantService.current_community_name()
            cancel = request.kind is RescheduleRequestKind.CANCEL
            requester = request.requested_by.preferred_name
            staff_name = actor.preferred_name
            when = request.decided_at or datetime.now(timezone.utc)
            color = {
                'approved': COLOR_CONFIRMED, 'superseded': COLOR_CONFIRMED,
                'declined': COLOR_CANCELLED,
            }.get(outcome, COLOR_FINISHED)

            def build(you: bool):
                if outcome == 'approved':
                    card = copy.reschedule_approved(you, staff_name, requester, tournament_name, cancel, when)
                elif outcome == 'declined':
                    card = copy.reschedule_declined(you, staff_name, requester, tournament_name, cancel, when)
                elif outcome == 'superseded':
                    card = copy.reschedule_superseded(you, staff_name, requester, tournament_name, cancel, when)
                else:
                    card = copy.reschedule_withdrawn(requester, tournament_name, cancel, when)
                return staff_request_dms.card(card, color=color, community_name=community)

            # An approved cancellation deleted the match, so there's nothing to open.
            link = (
                None if outcome == 'approved' and cancel
                else await notification_links.admin_match(request.match_id)  # type: ignore[attr-defined]
            )
            staff_request_dms.mark_handled(
                StaffRequestKind.RESCHEDULE_REQUEST, request.id, build, link, actor_id=actor.id,
            )
        except Exception:
            logger.exception('reschedule request %s: staff DM edit failed', request.id)

    @staticmethod
    async def _deciders(match: Match) -> Sequence[Tuple[User, NotificationRecipient]]:
        """Whoever could answer this: the admin on duty and the tournament's admins.

        A match alert (see ``StaffAlertService``), so it no longer pings every
        staff member, only the on-duty admin and the tournament's own admins,
        falling back to all staff when nobody is on duty. ``can_crud_match``
        filters both halves, because ``_decidable`` gates on it: an on-duty
        volunteer who cannot decide would be sent to a dialog that refuses.
        """
        from application.services.auth_service import AuthService
        from application.services.staff_alert_service import StaffAlertService

        return await StaffAlertService.tagged_match_alert_recipients(
            match, can_act=AuthService.can_crud_match,
        )

    @staticmethod
    def _opponents(match: Match, requester: User) -> Sequence[User]:
        players = match.players  # type: ignore[attr-defined]
        return [p.user for p in players if p.user_id != requester.id]

    async def _notify_decided(
        self,
        request: MatchRescheduleRequest,
        *,
        approved: bool,
        new_at: Optional[datetime],
        note: Optional[str],
    ) -> None:
        try:
            await self._send_decided(request, approved=approved, new_at=new_at, note=note)
        except Exception:
            logger.exception('reschedule decision notification failed for %s', request.id)

    async def _send_decided(
        self,
        request: MatchRescheduleRequest,
        *,
        approved: bool,
        new_at: Optional[datetime],
        note: Optional[str],
    ) -> None:
        """DM the requester the outcome.

        An approval that *moved* the match sends nothing: ``update_match`` has
        already fanned the reschedule notification out to both players and the
        crew, and a second "your request was approved" would be the same news
        twice. A decline is the one outcome no other notification covers, and
        an approved cancellation is worth confirming because the match DM says
        only that it was called off, not that they were the reason.
        """
        from application.services import notification_links
        from application.services.discord import DiscordService, discord_queue
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import (
            COLOR_CANCELLED,
            COLOR_RESCHEDULED,
            notification_embed,
            time_field,
        )
        from application.utils.discord_messages_reschedule import reschedule_decided_dm

        cancel = request.kind is RescheduleRequestKind.CANCEL
        if approved and not cancel:
            return

        requester = request.requested_by
        if not requester.discord_id:
            return

        community = await TenantService.current_community_name()
        tournament_name = request.match.tournament.name
        body = reschedule_decided_dm(
            tournament_name, approved=approved, kind_cancel=cancel,
            new_display=time_field(new_at), note=(note or '').strip(),
        )
        embed = notification_embed(
            title='🔁 Reschedule request ' + ('approved' if approved else 'declined'),
            color=COLOR_RESCHEDULED if approved else COLOR_CANCELLED,
            community_name=community,
            description=body,
        )
        # Only a decline gets a button. "Ask again with a different time" is a
        # real next step; an approval has nothing left for them to press.
        link = (
            None if approved
            else await notification_links.player_reschedule(request.match_id)  # type: ignore[attr-defined]
        )
        discord_queue.enqueue(
            DiscordService().send_dm(
                int(requester.discord_id), body, embed=embed, link=link, notice=(CANCEL_APPROVED if approved else RESCHEDULE_DECLINED).to(PLAYERS),
            )
        )
