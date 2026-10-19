"""Comments on a match, each signed with its author's name.

Three kinds, one per audience (``MatchCommentKind``):

* a **player** comment goes to staff only. The other player never sees it,
  because a note like "I'll be late" is between the player and the people
  running the event. It DMs the match's alert recipients: the admin on duty
  and the tournament's admins (``StaffAlertService``);
* a **private** staff note stays with staff and proctors;
* a **staff message to players** is read by staff and by the match's players,
  and DMs each player who has the "Staff comments on your matches"
  notification on.

"Staff" here is whoever may run the match on the floor (``can_run_match``):
staff, the tournament's admins and proctors. Authors may delete their own
comments; staff may delete any.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from application.errors import require_found
from application.events import EventType, match_live
from application.repositories import MatchCommentRepository, MatchRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.utils.timezone import format_local_display
from models import Match, MatchComment, MatchCommentKind, User

#: Long enough for a paragraph or two of markdown, short enough to keep the
#: match sheet a sheet.
MAX_COMMENT_LENGTH = 2000

#: A player's comments per match in a rolling minute. Each one refreshes every
#: open staff board, so one player holding Send down can churn the floor's
#: screens mid-event; nobody genuine posts this fast. Staff are not limited.
PLAYER_COMMENTS_PER_MINUTE = 10

logger = logging.getLogger(__name__)

_STAFF_KINDS = (MatchCommentKind.STAFF_PRIVATE, MatchCommentKind.STAFF_TO_PLAYERS)


def _player_ids(match: Match) -> set:
    # Reverse relation: mypy cannot see it on a dynamically built model.
    return {p.user_id for p in match.players}  # type: ignore[attr-defined]


class MatchCommentService:
    """Add, read and delete comments on a match."""

    def __init__(self) -> None:
        self.repository = MatchCommentRepository()
        self.match_repository = MatchRepository()
        self.audit_service = AuditService()

    async def _match(self, match_id: int) -> Match:
        return require_found(await self.match_repository.get_by_id(match_id), f"Match {match_id}")

    @staticmethod
    def _clean(body: str) -> str:
        text = (body or '').strip()
        if not text:
            raise ValueError('Write something first.')
        if len(text) > MAX_COMMENT_LENGTH:
            raise ValueError(f'Comments are limited to {MAX_COMMENT_LENGTH} characters.')
        return text

    async def can_read_staff_comments(self, user: Optional[User], match: Match) -> bool:
        return await AuthService.can_run_match(user, match)

    async def add_player_comment(self, match_id: int, user: User, body: str) -> MatchComment:
        """A player's comment to staff on their own match."""
        match = await self._match(match_id)
        if user.id not in _player_ids(match):
            raise ValueError('Only the players in a match can comment on it here.')
        since = datetime.now(timezone.utc) - timedelta(minutes=1)
        if await self.repository.count_by_author_since(match.id, user.id, since) >= PLAYER_COMMENTS_PER_MINUTE:
            raise ValueError('Slow down, try again in a minute.')
        comment = await self._add(match, user, MatchCommentKind.PLAYER, self._clean(body))
        await self._notify_staff(match, user, comment.body)
        return comment

    async def add_staff_comment(
        self, match_id: int, user: User, body: str, *, to_players: bool,
    ) -> MatchComment:
        """A private staff note, or (``to_players``) a message the players read."""
        match = await self._match(match_id)
        if not await self.can_read_staff_comments(user, match):
            raise ValueError("You don't have permission to comment on this match as staff.")
        kind = MatchCommentKind.STAFF_TO_PLAYERS if to_players else MatchCommentKind.STAFF_PRIVATE
        comment = await self._add(match, user, kind, self._clean(body))
        if to_players:
            self._notify_players(match, user, comment.body)
        return comment

    async def _add(
        self, match: Match, user: User, kind: MatchCommentKind, body: str,
    ) -> MatchComment:
        comment = await self.repository.create(match=match, author=user, kind=kind, body=body)
        await self.audit_service.write_and_publish(
            user,
            AuditActions.MATCH_COMMENT_ADDED,
            {'match_id': match.id, 'comment_id': comment.id, 'kind': kind.value},
            EventType.MATCH_COMMENT_ADDED,
            event_extra={
                'tournament_id': match.tournament_id,  # type: ignore[attr-defined]
                # Only what the players were told leaves the app: a webhook is
                # an outside listener, and the telemetry mirror outlives a
                # delete, so player comments and private notes carry no text.
                **({'body': body} if kind is MatchCommentKind.STAFF_TO_PLAYERS else {}),
            },
        )
        match_live.publish(match.id)
        return comment

    async def _notify_staff(self, match: Match, author: User, body: str) -> None:
        """DM the on-duty admin and the tournament's admins. Never raises."""
        from application.services.discord import discord_queue
        from application.services.match._comment_notifications import notify_staff_of_player_comment
        from application.services.staff_alert_service import StaffAlertService

        try:
            # Resolved here, in the request's tenant scope; the queue worker
            # that sends the DMs runs outside it.
            recipients = await StaffAlertService.tagged_match_alert_recipients(
                match, can_act=AuthService.can_run_match, exclude=(author.id,),
            )
        except Exception:
            logger.exception('player comment on match %s: resolving staff alerts failed', match.id)
            return
        if not recipients:
            return
        players = [p.user for p in match.players]  # type: ignore[attr-defined]
        discord_queue.enqueue(notify_staff_of_player_comment(
            match_id=match.id,
            tournament_name=match.tournament.name if match.tournament else '',
            author_name=author.preferred_name,
            body=body,
            when=match.scheduled_at,
            player_names=[u.preferred_name for u in players],
            recipients=recipients,
        ))

    def _notify_players(self, match: Match, author: User, body: str) -> None:
        from application.services.discord import discord_queue
        from application.services.match._comment_notifications import notify_match_comment

        players = [p.user for p in match.players]  # type: ignore[attr-defined]
        recipients = [
            u for u in players
            if u.id != author.id and u.dm_match_comments and u.discord_id
        ]
        if not recipients:
            return
        discord_queue.enqueue(notify_match_comment(
            match_id=match.id,
            tournament_name=match.tournament.name if match.tournament else '',
            author_name=author.preferred_name,
            body=body,
            when=match.scheduled_at,
            player_names=[u.preferred_name for u in players],
            recipients=recipients,
        ))

    async def delete_comment(self, comment_id: int, user: User) -> None:
        comment = require_found(await self.repository.get_by_id(comment_id), 'Comment')
        match = await self._match(comment.match_id)  # type: ignore[attr-defined]
        own = comment.author_id == user.id  # type: ignore[attr-defined]
        if not own and not await self.can_read_staff_comments(user, match):
            raise ValueError("You can only delete your own comments.")
        kind = comment.kind
        await self.repository.delete(comment)
        await self.audit_service.write_and_publish(
            user,
            AuditActions.MATCH_COMMENT_DELETED,
            {'match_id': match.id, 'comment_id': comment_id, 'kind': kind.value},
            EventType.MATCH_COMMENT_DELETED,
            event_extra={'tournament_id': match.tournament_id},  # type: ignore[attr-defined]
        )
        match_live.publish(match.id)

    async def list_for_viewer(self, match_id: int, user: Optional[User]) -> List[Dict[str, Any]]:
        """The comments ``user`` may read on this match, oldest first.

        Staff read everything. A player reads their own comments and the
        messages staff sent to the players. Anyone else reads nothing.
        """
        if user is None:
            return []
        match = await self._match(match_id)
        staff = await self.can_read_staff_comments(user, match)
        if staff:
            comments = await self.repository.list_for_match(match.id)
        elif user.id in _player_ids(match):
            comments = [
                c for c in await self.repository.list_for_match(
                    match.id, (MatchCommentKind.PLAYER, MatchCommentKind.STAFF_TO_PLAYERS),
                )
                if c.kind == MatchCommentKind.STAFF_TO_PLAYERS
                or c.author_id == user.id  # type: ignore[attr-defined]
            ]
        else:
            return []
        return [self._format(c, user, staff) for c in comments]

    @staticmethod
    def _format(comment: MatchComment, viewer: User, staff: bool) -> Dict[str, Any]:
        author = comment.author
        own = author is not None and author.id == viewer.id
        return {
            'id': comment.id,
            'kind': comment.kind.value,
            'body': comment.body,
            'author': author.preferred_name if author else 'Removed user',
            'own': own,
            'when': format_local_display(comment.created_at),
            'can_delete': own or staff,
        }

    async def counts_by_match(self, match_ids: List[int]) -> Dict[int, Dict[str, int]]:
        """``{match_id: {'player': n, 'staff': n}}`` for a staff board, in one query.

        Callers gate this on the viewer: the counts include private notes.
        """
        raw = await self.repository.counts_by_match(list(match_ids))
        staff_keys = {k.value for k in _STAFF_KINDS}
        return {
            match_id: {
                'player': kinds.get(MatchCommentKind.PLAYER.value, 0),
                'staff': sum(n for k, n in kinds.items() if k in staff_keys),
            }
            for match_id, kinds in raw.items()
        }
