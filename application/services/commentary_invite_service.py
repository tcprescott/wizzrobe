"""
Commentary Invite Service - Business Logic Layer

Pair commentary: a commentator who has signed up for a match invites a partner,
and the partner accepts or declines. Accepting signs them up through the
ordinary crew signup, so staff approve each of the two on their own and every
rule of :meth:`CrewService.signup_crew` applies to the partner too.

If the inviter withdraws, an accepted partner stays signed up; an invite still
waiting is cancelled and its DM edited to say so (Thomas's pick).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from application.errors import require_found
from application.events import match_live
from application.repositories import CommentaryInviteRepository, CommentatorRepository, MatchRepository
from application.repositories._save import save_fields
from application.services import notification_links
from application.services._crew_signup_dms import crew_dm_parts
from application.services.audit_service import AuditActions, AuditService
from application.services.discord import discord_queue
from application.services.discord.discord_service import DiscordService
from application.services.notification_catalog import CREW, CREW_PARTNER_ANSWERED, CREW_PARTNER_INVITE
from application.services.tenant_service import TenantService
from application.utils import discord_messages_crew as copy
from application.utils.discord_embeds import (
    COLOR_CANCELLED,
    COLOR_CONFIRMED,
    COLOR_CREW,
    COLOR_FINISHED,
    notification_embed,
)
from application.utils.discord_messages_community_events import literal
from models import CommentaryInvite, CommentaryInviteStatus, Match, User

logger = logging.getLogger(__name__)

#: Invites one person can send in an hour, across every match. Each one DMs
#: somebody, and players are untrusted.
INVITES_PER_HOUR = 10


def pairs_commentary(match: Match) -> bool:
    """Whether this match's tournament wants two or more commentators."""
    tournament = getattr(match, 'tournament', None)
    return bool(tournament and (tournament.required_commentators or 0) >= 2)


class CommentaryInviteService:
    """Inviting, answering and cancelling commentary partner invites."""

    def __init__(self) -> None:
        self.repository = CommentaryInviteRepository()
        self.match_repository = MatchRepository()
        self.audit_service = AuditService()

    async def invite(self, match_id: int, inviter: User, invitee_id: int) -> CommentaryInvite:
        """Ask ``invitee_id`` to commentate ``match_id`` with ``inviter``.

        The inviter must already be signed up as commentator, and gets one
        partner per match: a waiting or accepted invite has to be settled first.
        """
        from application.services.tenant_membership_service import TenantMembershipService
        from application.services.user_service import UserService

        members = TenantMembershipService()
        if not await members.is_member(inviter):
            raise ValueError('You are not a member of this community. Ask to join it first.')
        match = require_found(
            await self.match_repository.get_by_id(match_id, prefetch_relations=True), f'Match {match_id}',
        )
        self._check_open(match)
        commentators = list(match.commentators)  # type: ignore[attr-defined]
        if not pairs_commentary(match):
            raise ValueError('This match only needs one commentator, so there is no partner to invite.')
        if not any(c.user_id == inviter.id for c in commentators):
            raise ValueError('Sign up as commentator first, then invite your partner.')
        if await self.repository.pending_for_inviter(match.id, inviter.id) or any(
                c.invited_by_id == inviter.id for c in commentators):
            raise ValueError("You've already invited a partner for this match.")
        if len(commentators) >= match.tournament.required_commentators:
            raise ValueError('Commentary is already full for this match.')

        invitee = await UserService().get_user_by_id(int(invitee_id))
        if invitee is None or not invitee.is_active or not await members.is_member(invitee):
            raise ValueError("That person isn't a member of this community.")
        if invitee.id == inviter.id:
            raise ValueError("You can't invite yourself.")
        if any(c.user_id == invitee.id for c in commentators):
            raise ValueError(f'{invitee.preferred_name} is already signed up to commentate this match.')
        if any(p.user_id == invitee.id for p in await self.match_repository.get_players(match)):  # type: ignore[attr-defined]
            raise ValueError(f"{invitee.preferred_name} is playing in this match.")
        if await self.repository.pending_for_pair(match.id, invitee.id):
            raise ValueError(f'{invitee.preferred_name} already has an invite for this match.')
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        if await self.repository.count_sent_since(inviter.id, since) >= INVITES_PER_HOUR:
            raise ValueError("You've sent a lot of invites in the last hour. Try again later.")

        invite = await self.repository.create(match, inviter, invitee)
        await self.audit_service.write_log(inviter, AuditActions.CREW_PARTNER_INVITED, {
            'match_id': match.id, 'invite_id': invite.id, 'invitee_id': invitee.id,
        })
        match_live.publish(match.id)
        await self._send_invite(invite, match, inviter, invitee)
        return invite

    async def respond(self, invite_id: int, user: User, accept: bool) -> CommentaryInvite:
        """The partner's answer. Accepting signs them up as commentator."""
        from application.services.crew_hold_service import CrewHoldService
        from application.services.crew_service import CrewService

        invite = require_found(await self.repository.get_by_id(invite_id), 'Invite')
        if invite.invitee_id != user.id:
            raise ValueError('That invite was for someone else.')
        if invite.status is not CommentaryInviteStatus.PENDING:
            raise ValueError(_settled_message(invite))
        match = invite.match
        if accept:
            if match.crew_hold_source:
                await CrewHoldService().signup(match.crew_hold_source, match.crew_hold_key, user, 'commentator')
            else:
                await CrewService().signup_crew(match.id, user, 'commentator')
            row = await CommentatorRepository.get_by_match_and_user(match, user)
            if row is not None:
                row.invited_by_id = invite.inviter_id
                await save_fields(row, 'invited_by')
        status = CommentaryInviteStatus.ACCEPTED if accept else CommentaryInviteStatus.DECLINED
        await self.repository.set_status(invite, status)
        await self.audit_service.write_log(user, AuditActions.CREW_PARTNER_INVITE_ANSWERED, {
            'match_id': match.id, 'invite_id': invite.id, 'accepted': accept,
        })
        match_live.publish(match.id)
        self._edit_invite_dm(
            invite.id,
            copy.partner_invite_accepted_card() if accept else copy.partner_invite_declined_card(),
            COLOR_CONFIRMED if accept else COLOR_FINISHED,
            await notification_links.crew_match(match.id) if accept else None,
        )
        await self._tell_inviter(invite, accept)
        return invite

    async def cancel(self, invite_id: int, user: User) -> CommentaryInvite:
        """The inviter taking back an invite that hasn't been answered."""
        invite = require_found(await self.repository.get_by_id(invite_id), 'Invite')
        if invite.inviter_id != user.id:
            raise ValueError('You can only cancel invites you sent.')
        if invite.status is not CommentaryInviteStatus.PENDING:
            raise ValueError(_settled_message(invite))
        await self._cancel(invite, user)
        return invite

    async def cancel_open_for(self, match_id: int, inviter: User, actor: Optional[User] = None) -> int:
        """Cancel the waiting invites ``inviter`` sent on this match. Returns how many.

        For when the inviter stops commentating it: withdrew, or staff took them
        off. An accepted partner is left signed up.
        """
        invites = await self.repository.open_for_inviter(match_id, inviter.id)
        for invite in invites:
            await self._cancel(invite, actor or inviter)
        return len(invites)

    async def pending_for(self, user: User) -> List[CommentaryInvite]:
        """Invites waiting on ``user``'s answer, oldest first."""
        return await self.repository.pending_for_invitee(user.id)

    async def pending_by_match(self, match_ids: List[int]) -> Dict[int, List[CommentaryInvite]]:
        return await self.repository.pending_by_match(match_ids)

    async def partner_choices(self, match_id: int, inviter: User) -> Dict[int, str]:
        """Members the inviter could ask, by id: not themselves, not playing, not on commentary yet."""
        from application.services.tenant_membership_service import TenantMembershipService

        match = require_found(
            await self.match_repository.get_by_id(match_id, prefetch_relations=True), f'Match {match_id}',
        )
        taken = {inviter.id} | {c.user_id for c in match.commentators}  # type: ignore[attr-defined]
        taken |= {p.user_id for p in await self.match_repository.get_players(match)}  # type: ignore[attr-defined]
        members = await TenantMembershipService().list_members()
        return {
            m.id: m.preferred_name
            for m in sorted(members, key=lambda u: (u.preferred_name or '').casefold())
            if m.is_active and m.id not in taken
        }

    @staticmethod
    def _check_open(match: Match) -> None:
        if match.finished_at is not None:
            raise ValueError('This match has already finished. Crew signup is closed.')
        if match.started_at is not None:
            raise ValueError('This match has already started. Crew signup is closed.')

    async def _cancel(self, invite: CommentaryInvite, actor: User) -> None:
        await self.repository.set_status(invite, CommentaryInviteStatus.CANCELLED)
        await self.audit_service.write_log(actor, AuditActions.CREW_PARTNER_INVITE_CANCELLED, {
            'match_id': invite.match_id, 'invite_id': invite.id,
        })
        match_live.publish(invite.match_id)
        await invite.fetch_related('inviter')
        self._edit_invite_dm(
            invite.id, copy.partner_invite_cancelled_card(literal(invite.inviter.preferred_name)),
            COLOR_CANCELLED, None,
        )

    async def _send_invite(self, invite: CommentaryInvite, match: Match, inviter: User, invitee: User) -> None:
        if not invitee.discord_id:
            return
        try:
            message_kwargs, fields = await crew_dm_parts(match)
            message = copy.partner_invite_dm(inviter_name=inviter.preferred_name, **message_kwargs)
            embed = notification_embed(
                title='🎙️ Commentary invite', color=COLOR_CREW,
                community_name=await TenantService.current_community_name(),
                description=f'**{literal(inviter.preferred_name)}** wants you on commentary with them. '
                            "Accept and you're signed up as commentator. Staff still approve each of you.",
                fields=fields,
            )
            link = await notification_links.crew_invites()
            invite_id = invite.id

            async def remember(channel_id: int, message_id: int) -> None:
                await CommentaryInviteRepository.record_dm(invite_id, channel_id, message_id)

            discord_queue.enqueue(DiscordService().send_dm_with_partner_invite_buttons(
                int(invitee.discord_id), message, invite.id, embed=embed, link=link,
                on_sent=remember, notice=CREW_PARTNER_INVITE.to(CREW),
            ))
        except Exception:
            logger.exception('Commentary invite DM failed for invite %s', invite.id)

    async def _tell_inviter(self, invite: CommentaryInvite, accepted: bool) -> None:
        inviter = invite.inviter
        if not inviter.discord_id:
            return
        try:
            message_kwargs, fields = await crew_dm_parts(invite.match)
            name = invite.invitee.preferred_name
            message = copy.partner_answered_dm(partner_name=name, accepted=accepted, **message_kwargs)
            embed = notification_embed(
                title=f'🎙️ {name} accepted' if accepted else f"🎙️ {name} can't make it",
                color=COLOR_CONFIRMED if accepted else COLOR_FINISHED,
                community_name=await TenantService.current_community_name(),
                description=message.split('\n\n')[0], fields=fields,
            )
            link = await notification_links.crew_match(invite.match_id)
            discord_queue.enqueue(DiscordService().send_dm(
                int(inviter.discord_id), message, embed=embed, link=link,
                notice=CREW_PARTNER_ANSWERED.to(CREW),
            ))
        except Exception:
            logger.exception('Commentary invite answer DM failed for invite %s', invite.id)

    @staticmethod
    def _edit_invite_dm(invite_id: int, card: tuple, color: int, link) -> None:
        """Queue the edit of the partner's invite DM, once the send has recorded it."""
        async def edit() -> None:
            invite = await CommentaryInviteRepository.get_by_id(invite_id)
            if invite is None or not invite.dm_message_id:
                return
            title, description = card
            embed = notification_embed(
                title=title, color=color, description=description,
                community_name=await TenantService.current_community_name(),
            )
            ok, reason = await DiscordService().edit_dm(invite.dm_channel_id, invite.dm_message_id, embed, link)
            if not ok:
                logger.info('Commentary invite %s: DM not edited: %s', invite_id, reason)

        discord_queue.enqueue(edit())


def _settled_message(invite: CommentaryInvite) -> str:
    return {
        CommentaryInviteStatus.ACCEPTED: 'That invite has already been accepted.',
        CommentaryInviteStatus.DECLINED: 'That invite has already been declined.',
        CommentaryInviteStatus.CANCELLED: 'That invite was cancelled.',
    }.get(invite.status, 'That invite has already been answered.')
