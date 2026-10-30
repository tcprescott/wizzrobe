"""
Match Reschedule Service - Business Logic Layer

Players never move or call off their own matches. They *ask*, and someone who
could already have done it decides — which is the whole point: the alternative
players had was finding a staff member.

Two rules hold the design together.

**Approving performs the change.** ``approve`` calls the existing
``MatchService.update_match`` / ``cancel_match`` rather than writing
``scheduled_at`` itself, so there is exactly one implementation of "move a
match", and the reschedule DM fan-out, acknowledgment reseeding, racetime room
handling and ``match.rescheduled`` event all come with it. A request that were
merely *blessed* would leave staff to go and do the work anyway.

**No new authority.** ``approve`` and ``decline`` gate on ``can_crud_match``,
the same check guarding ``update_match``. Nobody can decide a request who could
not already have made the change by hand.

The opponent's agreement is recorded and never enforced. Staff decide either
way; knowing both players are happy just saves them asking.

The DM fan-out — who is told a request exists, and who hears how it went — lives
in :mod:`application.services._reschedule_notifications`, mixed in here.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from tortoise.exceptions import NoValuesFetched

from application.errors import require_found
from application.events import EventType, match_live
from application.repositories import (
    MatchRepository,
    RescheduleRequestRepository,
    TournamentRepository,
)
from application.services._reschedule_notifications import RescheduleNotificationMixin
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.notification_catalog import MATCH_CANCELLED_ON_REQUEST
from application.services.round_schedule_service import RoundScheduleService
from models import (
    Match,
    MatchRescheduleRequest,
    RescheduleRequestKind,
    RescheduleRequestStatus,
    User,
)

logger = logging.getLogger(__name__)

REASON_MAX_LENGTH = 500

# What an agreement deep link finds (see agreement_link_state).
AGREE_OPEN = 'open'
AGREE_DONE = 'agreed'
AGREE_DECIDED = 'decided'
AGREE_NOT_YOURS = 'not_yours'

# States in which there is still something to reschedule. Once a match is
# seated the players are in the room and the answer is a conversation with the
# proctor, not a queue entry staff will read later.
_OPEN_STATES = ('seated_at', 'started_at', 'finished_at', 'confirmed_at')


def _is_override(chosen: Optional[datetime], proposed: Optional[datetime]) -> bool:
    """Did staff approve a *different* time than the one asked for?

    Compared to the minute, the same tolerance ``assert_sg_fields_unchanged``
    uses, because the decision dialog always submits its pickers — which carry
    only ``HH:MM``. Without the tolerance every plain approval would be recorded
    as staff having overridden the player, which is the opposite of what
    happened.
    """
    if chosen is None or proposed is None:
        return False
    return abs((chosen - proposed).total_seconds()) >= 60


class MatchRescheduleService(RescheduleNotificationMixin):
    """Player-submitted requests to move or call off a match."""

    def __init__(self) -> None:
        self.repository = RescheduleRequestRepository()
        self.match_repository = MatchRepository()
        self.tournament_repository = TournamentRepository()
        self.audit_service = AuditService()

    async def get_by_id(self, request_id: int) -> Optional[MatchRescheduleRequest]:
        """Read-only load-or-None for presentation/bot callers."""
        return await self.repository.get_by_id(request_id)

    async def list_pending(
        self,
        actor: Optional[User] = None,
        *,
        tournament_ids: Optional[List[int]] = None,
        event_id: Optional[int] = None,
    ) -> List[MatchRescheduleRequest]:
        """The staff queue for this community, or for one of its events.

        Gated, unlike :meth:`list_mine`: a request carries the player's own words
        about why they need the change, which is not schedule data and is nobody
        else's to read. ``actor=None`` is accepted only so an internal caller can
        skip the check deliberately; every entry surface passes one.

        ``tournament_ids`` narrows it the way the admin board narrows itself, so
        a tournament admin is never shown a request they would then be refused
        for deciding.
        """
        if actor is not None and not await AuthService.is_staff(actor):
            # Deciding a request is ``can_crud_match``, so outside staff the
            # queue is a tournament admin's own tournaments and nobody else's.
            admin_ids, _ = await AuthService.tournament_scope(actor)
            await AuthService.ensure(
                bool(admin_ids), "Only staff can read the reschedule queue.",
            )
            tournament_ids = sorted(
                admin_ids if tournament_ids is None else admin_ids & set(tournament_ids)
            )
        return await self.repository.list_pending(tournament_ids=tournament_ids, event_id=event_id)

    async def list_mine(self, actor: User, limit: int = 25) -> List[MatchRescheduleRequest]:
        """What ``actor`` has asked for, and what became of it.

        No authorization check beyond being the actor: this reads a person's own
        requests and nobody else's. Without it a player who asked a question has
        no way to learn whether anyone answered.
        """
        return await self.repository.list_for_user(actor, limit)

    async def list_awaiting_agreement(self, actor: User) -> List[MatchRescheduleRequest]:
        """Open requests ``actor`` is the other player on — agreed to or not yet.

        The web counterpart of the opponent DM's Agree button. Same audience as
        that DM: only a match with exactly two players, because one column
        records agreement and in a bigger match it would read as everyone's yes.
        Already-agreed requests stay listed so the player can see their answer
        landed while staff decide.
        """
        rows = await self.repository.list_pending_against(actor.id)
        return [row for row in rows if await self._is_head_to_head(row.match)]

    async def agreement_link_state(self, request_id: int, actor: Optional[User]) -> str:
        """Why a "Open the request" link would or would not offer Agree.

        One of :data:`AGREE_OPEN`, :data:`AGREE_DONE`, :data:`AGREE_DECIDED` or
        :data:`AGREE_NOT_YOURS`. A DM outlives the request it is about: staff
        may have answered, the requester may have withdrawn, or the reader may
        already have agreed from Discord. The page says which, rather than
        opening nothing.
        """
        request = await self.repository.get_by_id(request_id)
        if request is None or actor is None:
            return AGREE_NOT_YOURS
        if request.requested_by_id == actor.id:  # type: ignore[attr-defined]
            return AGREE_NOT_YOURS
        if not await self._is_player(request.match, actor):
            return AGREE_NOT_YOURS
        if not await self._is_head_to_head(request.match):
            return AGREE_NOT_YOURS
        if request.status is not RescheduleRequestStatus.PENDING:
            return AGREE_DECIDED
        if request.opponent_agreed_at is not None:
            return AGREE_DONE
        return AGREE_OPEN

    async def pending_count(self) -> int:
        return await self.repository.pending_count()

    async def pending_match_ids(self) -> List[int]:
        return await self.repository.pending_match_ids()

    async def pending_match_ids_for_user(self, user: User) -> List[int]:
        """Matches where this player already has a request waiting."""
        return await self.repository.pending_match_ids_for_user(user.id)

    async def list_for_match(
        self, match_id: int, actor: Optional[User] = None,
    ) -> List[MatchRescheduleRequest]:
        """Every request raised against one match, newest first.

        Takes an id rather than a ``Match`` so an entry surface never has to load
        the row just to hand it straight back.

        Readable by whoever could decide these, or by a player in the match
        reading about their own. Gated here rather than at the router so every
        surface inherits it.
        """
        if actor is not None:
            match = require_found(
                await self.match_repository.get_by_id(match_id), f"Match {match_id}"
            )
            await AuthService.ensure(
                await AuthService.can_crud_match(actor, match)
                or await self._is_player(match, actor),
                "You can't read the reschedule requests for this match.",
            )
        return await self.repository.list_for_match(match_id)

    async def _is_player(self, match: Match, user: User) -> bool:
        """Whether ``user`` plays in ``match``, prefetched or not.

        :meth:`can_request` is called once per row while a board renders, so the
        prefetched roster is used when the caller has one. It falls back to a
        query rather than raising, because a ``Match`` handed in by a page is
        not guaranteed to carry its players and a missing prefetch must not read
        as "not a player" — that would silently hide the control from the very
        people it is for.
        """
        try:
            # Reverse relation and FK id column: mypy cannot see either on a
            # dynamically built Tortoise model.
            players = match.players  # type: ignore[attr-defined]
            return any(p.user_id == user.id for p in players)
        except NoValuesFetched:
            rows = await self.match_repository.get_players(match.id)
            return any(p.user_id == user.id for p in rows)  # type: ignore[attr-defined]

    async def _is_head_to_head(self, match: Match) -> bool:
        """Exactly two players, the only shape opponent agreement can describe.

        One column records it, so in a bigger match one player's yes would read
        as everyone's. Enforced here rather than only in the listing, because the
        REST route and a forwarded ``?agree=`` link reach the write directly.
        """
        try:
            players = match.players  # type: ignore[attr-defined]
            return len(players) == 2
        except NoValuesFetched:
            return len(await self.match_repository.get_players(match.id)) == 2

    @staticmethod
    def _blocking_state(match: Match) -> Optional[str]:
        """Why this match can no longer be asked about, or ``None``."""
        if match.scheduled_at is None:
            return "This match doesn't have a time yet, so there's nothing to move."
        for field in _OPEN_STATES:
            if getattr(match, field, None) is not None:
                return "This match is already under way — talk to a proctor."
        return None

    async def can_request(self, match: Match, user: Optional[User]) -> bool:
        """Whether ``user`` may raise a request against ``match``.

        Read by the UI so the control is hidden rather than shown and refused —
        a button that exists only to explain that it does nothing should not be
        there. :meth:`submit` re-checks every clause, because the REST route and
        a forwarded link both reach past whatever the UI decided to render.
        """
        if user is None or not await self._is_player(match, user):
            return False
        if self._blocking_state(match) is not None:
            return False
        if getattr(match, 'speedgaming_episode_id', None) is not None:
            return False
        tournament = await self.tournament_repository.get_by_id(match.tournament_id)  # type: ignore[attr-defined]
        if tournament is None or not getattr(
            tournament, 'allow_reschedule_requests', True
        ):
            return False
        return not await self.repository.get_pending_for(match.id, user.id)

    async def list_requestable_match_ids(self, user: Optional[User]) -> set:
        """Every match ``user`` could raise a request against right now.

        The board's own gate, resolved in three queries rather than
        :meth:`can_request` per row — a schedule with thirty rows would
        otherwise run ninety. Same rules, same order; the per-row method stays
        for the single-match callers (the dialog, the tests).

        The player and match-state clauses are left to the row template, which
        already carries the roster and the state and can decide without a query.
        """
        if user is None:
            return set()
        mine = await self.match_repository.get_upcoming_for_user(user.id)
        already_asked = set(
            await self.repository.pending_match_ids_for_user(user.id)
        )
        return {
            match.id for match in mine
            if match.id not in already_asked
            and getattr(match, 'speedgaming_episode_id', None) is None
            and getattr(match.tournament, 'allow_reschedule_requests', True)
        }

    async def submit(
        self,
        match_id: int,
        actor: User,
        *,
        reason: str,
        kind: RescheduleRequestKind = RescheduleRequestKind.RESCHEDULE,
        proposed_at: Optional[datetime] = None,
    ) -> MatchRescheduleRequest:
        """Raise a request against a match ``actor`` is playing in."""
        if actor is None:
            raise PermissionError("Login required to ask for a reschedule.")

        match = require_found(
            await self.match_repository.get_by_id(match_id), f"Match {match_id}"
        )
        if not await self._is_player(match, actor):
            raise PermissionError("Only the players in a match can ask for it to be moved.")

        blocking = self._blocking_state(match)
        if blocking:
            raise ValueError(blocking)

        # A sourced match's time belongs to the next sync (``assert_sg_fields_unchanged``),
        # so nobody here could approve this. Refusing now beats a request that
        # sits in the queue until someone discovers it can never be actioned.
        if getattr(match, 'speedgaming_episode_id', None) is not None:
            raise ValueError(
                "This match is synced from SpeedGaming — its time has to change there."
            )

        tournament = require_found(
            await self.tournament_repository.get_by_id(match.tournament_id),  # type: ignore[attr-defined]
            f"Tournament {match.tournament_id}",  # type: ignore[attr-defined]
        )
        if not getattr(tournament, 'allow_reschedule_requests', True):
            raise PermissionError(
                f"{tournament.name} doesn't take reschedule requests here."
            )

        reason = (reason or '').strip()
        if not reason:
            raise ValueError("Say why you need the change — staff decide on the reason.")
        if len(reason) > REASON_MAX_LENGTH:
            raise ValueError(
                f"That reason is a bit long. Keep it under {REASON_MAX_LENGTH} characters."
            )

        if await self.repository.get_pending_for(match_id, actor.id):
            raise ValueError(
                "You already have a request waiting on this match. Withdraw it "
                "first if you want to ask for something different."
            )

        if kind is RescheduleRequestKind.CANCEL:
            # A cancellation proposes no time by definition; accepting one would
            # put a number on the record that nothing ever reads.
            proposed_at = None
        elif proposed_at is not None:
            if proposed_at <= datetime.now(timezone.utc):
                raise ValueError("Pick a time in the future.")
            await self._assert_schedulable(proposed_at, match.tournament_id)  # type: ignore[attr-defined]
            await RoundScheduleService().check_match_time(match, tournament, proposed_at, actor)

        request = await self.repository.create(
            match=match,
            requested_by=actor,
            kind=kind,
            proposed_at=proposed_at,
            reason=reason,
            original_scheduled_at=match.scheduled_at,
        )

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_RESCHEDULE_REQUESTED,
            {
                'request_id': request.id,
                'match_id': match_id,
                'kind': kind.value,
                'proposed_at': proposed_at.isoformat() if proposed_at else None,
            },
            EventType.MATCH_RESCHEDULE_REQUESTED,
            event_extra={'tournament_id': match.tournament_id},  # type: ignore[attr-defined]
        )

        # Nudge open views: a staff member already sitting on the schedule board
        # should see the queue strip grow rather than discover the request on
        # their next page load.
        match_live.publish(match_id, match_live.CHANGED)

        await self._notify_submitted(request, match, tournament.name, actor)
        return request

    async def _assert_schedulable(
        self, proposed_at: datetime, tournament_id: int,
    ) -> None:
        """Reject a proposal outside the tournament's operating hours, now.

        The same check ``update_match`` runs at approval. Running it here too is
        the difference between the player being told immediately and staff
        discovering it when they press Approve.
        """
        from application.services.match.match_service import MatchService

        await MatchService().assert_within_tournament_hours(proposed_at, tournament_id)

    async def record_opponent_agreement(
        self, request_id: int, actor: User,
    ) -> MatchRescheduleRequest:
        """The match's *other* player saying the change works for them.

        Advisory. It is stamped on the request and shown to staff, and it gates
        nothing: a request with no agreement is still decidable, and one with
        agreement can still be declined.
        """
        request = require_found(
            await self.repository.get_by_id(request_id), "Reschedule request"
        )
        if request.status is not RescheduleRequestStatus.PENDING:
            raise ValueError("That request has already been decided.")
        if request.requested_by_id == actor.id:  # type: ignore[attr-defined]
            raise PermissionError("You can't agree with your own request.")
        if not await self._is_player(request.match, actor):
            raise PermissionError("Only the other player in the match can agree to this.")
        if not await self._is_head_to_head(request.match):
            raise ValueError(
                "Agreement only applies to a two-player match. Staff will decide this one."
            )

        if request.opponent_agreed_at is None:
            await self.repository.update(
                request, opponent_agreed_at=datetime.now(timezone.utc),
            )
            await self.audit_service.write_and_publish(
                actor,
                AuditActions.MATCH_RESCHEDULE_AGREED,
                {'request_id': request.id, 'match_id': request.match_id},
                EventType.MATCH_RESCHEDULE_AGREED,
                event_extra={'tournament_id': request.match.tournament_id},
            )
            # Agreement changes what staff read on the chip they are about to
            # click, so it earns a nudge even though the queue length is the same.
            match_live.publish(request.match_id, match_live.CHANGED)
        return request

    async def withdraw(self, request_id: int, actor: User) -> MatchRescheduleRequest:
        """Take a request back. The requester's alone.

        Distinct from a decline so staff can tell a request they refused from
        one that stopped mattering.
        """
        request = require_found(
            await self.repository.get_by_id(request_id), "Reschedule request"
        )
        if request.requested_by_id != actor.id:  # type: ignore[attr-defined]
            raise PermissionError("Only the person who asked can withdraw a request.")
        if request.status is not RescheduleRequestStatus.PENDING:
            raise ValueError("That request has already been decided.")

        await self.repository.update(
            request,
            status=RescheduleRequestStatus.WITHDRAWN,
            decided_at=datetime.now(timezone.utc),
        )
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_RESCHEDULE_WITHDRAWN,
            {'request_id': request.id, 'match_id': request.match_id},  # type: ignore[attr-defined]
            EventType.MATCH_RESCHEDULE_WITHDRAWN,
            event_extra={'tournament_id': request.match.tournament_id},
        )
        # The strip shrinks for the same reason it grew.
        match_live.publish(request.match_id, match_live.CHANGED)  # type: ignore[attr-defined]
        await self._mark_staff_dms(
            request, 'withdrawn', actor=actor, tournament_name=request.match.tournament.name,
        )
        from application.services import opt_in_notifications
        from application.services.discord import discord_queue
        discord_queue.enqueue(opt_in_notifications.request_withdrawn(
            request.match_id, actor.id, request.kind is RescheduleRequestKind.CANCEL,  # type: ignore[attr-defined]
        ))
        return request

    async def _decidable(self, request_id: int, actor: Optional[User]) -> MatchRescheduleRequest:
        """Load a pending request ``actor`` is allowed to decide."""
        request = require_found(
            await self.repository.get_by_id(request_id), "Reschedule request"
        )
        await AuthService.ensure(
            await AuthService.can_crud_match(actor, request.match),
            "You can't decide reschedule requests for this match.",
        )
        if request.status is not RescheduleRequestStatus.PENDING:
            raise ValueError("That request has already been decided.")
        return request

    async def approve(
        self,
        request_id: int,
        actor: User,
        *,
        scheduled_at: Optional[datetime] = None,
        note: Optional[str] = None,
    ) -> MatchRescheduleRequest:
        """Grant the request, by *making the change*.

        ``scheduled_at`` lets staff counter with a different time than the one
        proposed ("not 14:00, but I can do 20:00"); the requester is told which
        time they got either way.

        Ordering is perform-then-record, so a match that could not actually be
        moved (tournament hours, a SpeedGaming guard) never leaves a request
        marked approved and a schedule that did not budge. The cancellation
        branch has no record step at all: cancelling deletes the match, and the
        request cascades with it, which is why the audit row is written for
        both and is the durable history.
        """
        from application.services.match.match_service import MatchService

        request = await self._decidable(request_id, actor)
        match_id = request.match_id  # type: ignore[attr-defined]
        tournament_id = request.match.tournament_id
        match_service = MatchService()

        superseded_requests: List[MatchRescheduleRequest] = []
        if request.kind is RescheduleRequestKind.CANCEL:
            await match_service.cancel_match(
                match_id, actor=actor, reason=note or request.reason,
                notice=MATCH_CANCELLED_ON_REQUEST,
            )
            final_at = None
        else:
            final_at = scheduled_at or request.proposed_at
            if final_at is None:
                raise ValueError(
                    "This request didn't name a time — pick one before approving it."
                )
            # The player's proposal was held to the stage's window when they
            # made it; a staff counter-offer is staff's call to make.
            await match_service.update_match(
                match_id, scheduled_at=final_at, actor=actor, allow_outside_round=True,
            )
            await self.repository.update(
                request,
                status=RescheduleRequestStatus.APPROVED,
                decided_by=actor,
                decided_at=datetime.now(timezone.utc),
                decision_note=(note or '').strip() or None,
            )
            superseded_requests = await self._supersede_others(request, actor)
            # update_match already published, but *before* the request was
            # marked approved, so an open board re-read it as still pending
            # (no chip, no Ask to change). Publish again now the decision is
            # recorded; the row's last refresh is then the true one.
            match_live.publish(match_id, match_live.CHANGED)

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_RESCHEDULE_APPROVED,
            {
                'request_id': request_id,
                'match_id': match_id,
                'kind': request.kind.value,
                'scheduled_at': final_at.isoformat() if final_at else None,
                'overrode_proposal': _is_override(scheduled_at, request.proposed_at),
                # The player's own words, and staff's. An approved cancellation
                # deletes the match and the request cascades with it, so without
                # these the audit row is the only record and it would hold
                # neither side of the conversation. ``cancel_match`` records
                # only one of the two, whichever was passed to it.
                'reason': request.reason,
                'note': (note or '').strip() or None,
                # Requests this approval closed out without anyone deciding them.
                'superseded_request_ids': [other.id for other in superseded_requests],
            },
            EventType.MATCH_RESCHEDULE_APPROVED,
            event_extra={'tournament_id': tournament_id},
        )

        await self._notify_decided(request, approved=True, new_at=final_at, note=note)
        tournament_name = request.match.tournament.name
        await self._mark_staff_dms(
            request, 'approved', actor=actor, tournament_name=tournament_name,
        )
        for other in superseded_requests:
            await self._mark_staff_dms(
                other, 'superseded', actor=actor, tournament_name=tournament_name,
            )
        return request

    async def decline(
        self, request_id: int, actor: User, note: str,
    ) -> MatchRescheduleRequest:
        """Refuse the request, in staff's own words.

        The note is required. A refusal with no reason is the thing this whole
        feature exists to replace.
        """
        request = await self._decidable(request_id, actor)
        note = (note or '').strip()
        if not note:
            raise ValueError('Say why — the note is all the player sees.')

        await self.repository.update(
            request,
            status=RescheduleRequestStatus.DECLINED,
            decided_by=actor,
            decided_at=datetime.now(timezone.utc),
            decision_note=note,
        )
        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_RESCHEDULE_DECLINED,
            {'request_id': request.id, 'match_id': request.match_id},  # type: ignore[attr-defined]
            EventType.MATCH_RESCHEDULE_DECLINED,
            event_extra={'tournament_id': request.match.tournament_id},
        )
        # A decline touches no match, so it publishes its own (approving
        # publishes once more after recording the decision, see ``approve``).
        match_live.publish(request.match_id, match_live.CHANGED)  # type: ignore[attr-defined]
        await self._notify_decided(request, approved=False, new_at=None, note=note)
        await self._mark_staff_dms(
            request, 'declined', actor=actor, tournament_name=request.match.tournament.name,
        )
        return request

    async def _supersede_others(
        self, approved: MatchRescheduleRequest, actor: User,
    ) -> List[MatchRescheduleRequest]:
        """Close out the match's other open requests once one has settled it.

        Marked ``SUPERSEDED`` rather than declined: staff did not refuse them,
        and a queue entry pointing at a time that has already moved is worse
        than no entry. No DM — everyone involved is a player in the match and
        has just had the reschedule notification. Returns the requests closed,
        whose ids the approval audit records.
        """
        others = [
            r for r in await self.repository.list_pending_for_match(approved.match_id)  # type: ignore[attr-defined]
            if r.id != approved.id
        ]
        for other in others:
            await self.repository.update(
                other,
                status=RescheduleRequestStatus.SUPERSEDED,
                decided_by=actor,
                decided_at=datetime.now(timezone.utc),
            )
        # Returned rather than audited per row: these are a consequence of the
        # one decision, and the approval's own audit entry is where "what did
        # this close?" is answered.
        return others
