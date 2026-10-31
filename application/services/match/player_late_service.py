"""A player telling staff they're running late for their own match.

The player picks how far out they are (10, 20 or 30 minutes, the same choices a
volunteer gets) and can add a short note. It lands on their ``MatchPlayers``
row, so the run sheet, Proctor Station and the "Needs you" inbox can show it on
the match itself, and DMs the match's alert recipients: the admin on duty and
the tournament's admins (``StaffAlertService``), plus the Broadcast Techs on
shift when the match has a stage. The opponent is not told.

The window opens :data:`LATE_WINDOW` before the match (or when a stage match's
reminder goes out, if that's earlier) and stays open until a proctor checks it in. Before that it's a reschedule, not a late arrival.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from application.errors import require_found
from application.events import EventType, match_live
from application.repositories import MatchRepository
from application.repositories._save import save_fields
from application.services.audit_service import AuditActions, AuditService
from application.utils.discord_messages_volunteer import LATE_CHOICES
from application.utils.timezone import to_utc_aware
from models import Match, MatchPlayers, User

LATE_WINDOW = timedelta(minutes=30)

MAX_LATE_NOTE = 280

logger = logging.getLogger(__name__)


def late_window_open(match: Match, now: Optional[datetime] = None) -> bool:
    """Whether a player of ``match`` may say they're running late right now."""
    if match.scheduled_at is None or match.seated_at is not None or match.finished_at is not None:
        return False
    if match.tournament and match.tournament.is_racetime_enabled:
        return False
    now = now or datetime.now(timezone.utc)
    window = LATE_WINDOW
    lead = getattr(match.tournament, 'stage_reminder_minutes', 0) if getattr(match, 'stage_id', None) else 0
    if isinstance(lead, int) and lead > 0:
        window = max(window, timedelta(minutes=lead))
    return now >= to_utc_aware(match.scheduled_at) - window


def late_tag(player: MatchPlayers) -> Optional[str]:
    """'~20 min' for a player who said they're late, else None."""
    if player.late_minutes is None:
        return None
    if player.late_minutes >= LATE_CHOICES[-1]:
        return f'{LATE_CHOICES[-1]}+ min'
    return f'~{player.late_minutes} min'


class PlayerLateService:
    """Set and take back a player's running-late heads-up."""

    def __init__(self) -> None:
        self.repository = MatchRepository()
        self.audit_service = AuditService()

    async def _own_row(self, match_id: int, user: User) -> tuple[Match, MatchPlayers]:
        match = require_found(await self.repository.get_by_id(match_id), f"Match {match_id}")
        for player in match.players:  # type: ignore[attr-defined]
            if player.user_id == user.id:
                return match, player
        raise ValueError('Only the players in a match can say they are running late.')

    async def report_late(
        self, match_id: int, user: User, minutes: int, note: Optional[str] = None,
    ) -> MatchPlayers:
        """Record the heads-up and DM the match's alert recipients."""
        if minutes not in LATE_CHOICES:
            raise ValueError('Pick how late you expect to be.')
        text = (note or '').strip() or None
        if text and len(text) > MAX_LATE_NOTE:
            raise ValueError(f'Keep the note under {MAX_LATE_NOTE} characters.')
        match, player = await self._own_row(match_id, user)
        if match.seated_at is not None:
            raise ValueError("You're already checked in for this match.")
        if not late_window_open(match):
            raise ValueError(
                "You can say you're running late from 30 minutes before your match. "
                "To move it, ask to change it instead."
            )
        if player.late_minutes == minutes and player.late_note == text:
            return player
        player.late_minutes = minutes
        player.late_at = datetime.now(timezone.utc)
        player.late_note = text
        await save_fields(player, 'late_minutes', 'late_at', 'late_note')
        await self.audit_service.write_and_publish(
            user, AuditActions.MATCH_PLAYER_RUNNING_LATE,
            {'match_id': match.id, 'minutes': minutes},
            EventType.MATCH_PLAYER_RUNNING_LATE,
            event_extra={'tournament_id': match.tournament_id},
        )
        match_live.publish(match.id)
        await self._notify_staff(match, user, minutes, text)
        return player

    async def take_back_late(self, match_id: int, user: User) -> MatchPlayers:
        """Take the heads-up back: the player made it, or tapped it by mistake."""
        match, player = await self._own_row(match_id, user)
        if player.late_minutes is None:
            return player
        player.late_minutes = None  # type: ignore[assignment]
        player.late_at = None  # type: ignore[assignment]
        player.late_note = None  # type: ignore[assignment]
        await save_fields(player, 'late_minutes', 'late_at', 'late_note')
        await self.audit_service.write_and_publish(
            user, AuditActions.MATCH_PLAYER_LATE_CLEARED,
            {'match_id': match.id},
            EventType.MATCH_PLAYER_LATE_CLEARED,
            event_extra={'tournament_id': match.tournament_id},  # type: ignore[attr-defined]
        )
        match_live.publish(match.id)
        return player

    async def _notify_staff(
        self, match: Match, author: User, minutes: int, note: Optional[str],
    ) -> None:
        from application.services.auth_service import AuthService
        from application.services.discord import discord_queue
        from application.services.match._comment_notifications import notify_staff_of_player_late
        from application.services.notification_catalog import PLAYER_LATE, PLAYER_LATE_ON_STAGE
        from application.services.staff_alert_service import StaffAlertService

        recipients = []
        try:
            for user, why in await StaffAlertService.tagged_match_alert_recipients(
                match, can_act=AuthService.can_run_match, exclude=(author.id,),
            ):
                recipients.append((user, PLAYER_LATE.to(why)))
        except Exception:
            logger.exception('running late on match %s: resolving staff alerts failed', match.id)
        try:
            told = {user.id for user, _ in recipients}
            for user, why in await StaffAlertService.tagged_broadcast_tech_recipients(
                match, exclude=(author.id,),
            ):
                if user.id not in told:
                    recipients.append((user, PLAYER_LATE_ON_STAGE.to(why)))
        except Exception:
            logger.exception('running late on match %s: resolving Broadcast Techs failed', match.id)
        if not recipients:
            return
        players = [p.user for p in match.players]  # type: ignore[attr-defined]
        discord_queue.enqueue(notify_staff_of_player_late(
            match_id=match.id,
            tournament_name=match.tournament.name if match.tournament else '',
            player_name=author.preferred_name,
            minutes=minutes,
            note=note,
            when=match.scheduled_at,
            player_names=[u.preferred_name for u in players],
            recipients=recipients,
        ))
