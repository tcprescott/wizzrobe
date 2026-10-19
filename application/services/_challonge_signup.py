"""Players adding themselves to a linked Challonge bracket, mixed into ``ChallongeService``.

The Wizzrobe service account adds the entrant by the player's linked Challonge
username, so the player's own Challonge login is never needed (we do not keep
their token). Challonge then emails that account an invite to claim the entry.

The window and membership checks stay in ``TournamentSignupMixin``, which calls
in here only once the tournament is known to take its roster from Challonge.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from application.events import EventType
from application.feature_flags import requires_feature
from application.repositories import AuditRepository, ChallongeRepository, TournamentRepository
from application.services.audit_service import AuditActions, AuditService
from application.utils.clients.challonge_client import ChallongeAPIError, ChallongeClient
from models import ChallongeParticipant, FeatureFlag, Tournament, User

logger = logging.getLogger(__name__)

PARTICIPANT_WRITE_SCOPE = 'participants:write'

# Set on the entrants Wizzrobe adds, so a sync can still map one to its player
# while Challonge's invite is unclaimed and the entry carries no account id.
MISC_USER_PREFIX = 'wizzrobe-user:'

# Challonge states in which the roster can still change. Unknown (never synced)
# counts as open: Challonge itself refuses the add if the bracket has started.
_ROSTER_OPEN_STATES = frozenset({'pending', 'checking_in', 'checked_in'})

# Every sign-up and withdrawal spends one request of the community's monthly
# Challonge quota, so a player toggling the button is capped.
ROSTER_CHANGES_PER_HOUR = 6


def roster_locked(challonge_state: Optional[str]) -> bool:
    return bool(challonge_state) and challonge_state not in _ROSTER_OPEN_STATES


def refuse_empty_started_bracket(full: dict, mirrored_participants: int) -> None:
    """A started bracket can't lose every entrant.

    An answer that says it did is Challonge degrading, and pruning the mirror on
    it would unlink every booked match.
    """
    if (not full['participants'] and mirrored_participants
            and roster_locked(full['tournament'].get('state'))):
        raise ValueError(
            "Challonge sent back a started bracket with no entrants, so nothing "
            "was changed. Try Sync again in a few minutes."
        )


def misc_user_id(misc: Optional[str]) -> Optional[int]:
    if not misc or not misc.startswith(MISC_USER_PREFIX):
        return None
    tail = misc[len(MISC_USER_PREFIX):]
    return int(tail) if tail.isdigit() else None


_CHALLONGE_DOWN = "Challonge isn't answering right now. Try again in a few minutes."


def _challonge_down(error: ChallongeAPIError) -> bool:
    """No answer at all, or Challonge's own server error: not a refusal."""
    return error.status is None or error.status >= 500


class ChallongeSignupMixin:
    """The collaborators are set up by ``ChallongeService.__init__``."""

    repository: ChallongeRepository
    tournament_repository: TournamentRepository
    audit_service: AuditService
    _api_client: Callable[[], ChallongeClient]

    @requires_feature(FeatureFlag.CHALLONGE)
    async def can_add_participants(self) -> bool:
        """Whether the connection was granted the scope adding an entrant needs."""
        connection = await self.repository.get_connection()
        return bool(connection and PARTICIPANT_WRITE_SCOPE in (connection.scopes or '').split())

    @requires_feature(FeatureFlag.CHALLONGE)
    async def signup_rosters(
        self, tournament_ids: List[int],
    ) -> Dict[int, List[ChallongeParticipant]]:
        """Each linked bracket's mirrored entrants, for the Tournaments tab's cards."""
        if not tournament_ids:
            return {}
        return await self.repository.participants_by_tournament(tournament_ids)

    async def _check_roster_change_rate(self, user: User) -> None:
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        recent = 0
        for action in (
            AuditActions.CHALLONGE_PARTICIPANT_SIGNED_UP,
            AuditActions.CHALLONGE_PARTICIPANT_WITHDRAWN,
        ):
            recent += await AuditRepository.count(
                start=since, user_id=user.id, action_contains=action,
            )
        if recent >= ROSTER_CHANGES_PER_HOUR:
            raise ValueError(
                "You've changed your Challonge sign-ups a lot this hour. "
                'Try again later, or ask staff.'
            )

    @requires_feature(FeatureFlag.CHALLONGE)
    async def sign_up_player(self, tournament: Tournament, user: User) -> ChallongeParticipant:
        """Add ``user`` to ``tournament``'s Challonge bracket under their linked name."""
        if not user.challonge_user_id or not user.challonge_username:
            raise ValueError(
                f'{tournament.name} runs on Challonge. Link your Challonge account first.'
            )
        if roster_locked(tournament.challonge_state):
            raise ValueError(f'The {tournament.name} bracket has started, so its roster is locked.')
        if await self.repository.get_participant_for_user(tournament, user):
            raise ValueError(f'You are already signed up for {tournament.name}.')
        if not await self.can_add_participants():
            raise ValueError(
                f"Sign-ups for {tournament.name} aren't open through Wizzrobe yet. Ask staff."
            )
        await self._check_roster_change_rate(user)

        try:
            remote = await self._api_client().create_participant(
                tournament.challonge_tournament_id,
                name=user.challonge_username,
                username=user.challonge_username,
                misc=f'{MISC_USER_PREFIX}{user.id}',
                community=tournament.challonge_community,
            )
        except ChallongeAPIError as e:
            logger.warning(
                'Challonge refused sign-up: tournament=%s user=%s', tournament.id, user.id,
                exc_info=True,
            )
            if _challonge_down(e):
                raise ValueError(_CHALLONGE_DOWN) from None
            raise ValueError(
                f"Challonge wouldn't add you to {tournament.name}. The bracket may have "
                'started or be full. Ask staff if you think it should have worked.'
            ) from None

        participant = await self.repository.upsert_participant(
            tournament=tournament,
            challonge_participant_id=remote['participant_id'],
            name=remote.get('name') or user.challonge_username,
            challonge_user_id=remote.get('challonge_user_id') or user.challonge_user_id,
            user=user,
        )
        await self._record_roster_change(user, tournament, participant, added=True)
        return participant

    @requires_feature(FeatureFlag.CHALLONGE)
    async def withdraw_player(self, tournament: Tournament, user: User) -> None:
        """Remove ``user``'s own entry from the bracket, while the roster is still open."""
        participant = await self.repository.get_participant_for_user(tournament, user)
        if participant is None:
            raise ValueError(f'You are not signed up for {tournament.name}.')
        if roster_locked(tournament.challonge_state):
            raise ValueError(
                f'The {tournament.name} bracket has started. Ask staff if you need to drop out.'
            )
        await self._check_roster_change_rate(user)

        try:
            await self._api_client().delete_participant(
                tournament.challonge_tournament_id,
                participant.challonge_participant_id,
                community=tournament.challonge_community,
            )
        except ChallongeAPIError as e:
            # Already gone on Challonge (staff removed it there): finish the
            # withdrawal locally rather than leave a ghost entry.
            if e.status != 404:
                logger.warning(
                    'Challonge refused withdrawal: tournament=%s user=%s',
                    tournament.id, user.id, exc_info=True,
                )
                if _challonge_down(e):
                    raise ValueError(_CHALLONGE_DOWN) from None
                raise ValueError(
                    f"Challonge wouldn't remove you from {tournament.name}. Ask staff."
                ) from None
        await self.repository.delete_participant(participant)
        await self._record_roster_change(user, tournament, participant, added=False)

    async def _record_roster_change(
        self, user: User, tournament: Tournament, participant: ChallongeParticipant, *, added: bool,
    ) -> None:
        await self.audit_service.write_and_publish(
            user,
            AuditActions.CHALLONGE_PARTICIPANT_SIGNED_UP if added
            else AuditActions.CHALLONGE_PARTICIPANT_WITHDRAWN,
            {
                'tournament_id': tournament.id,
                'challonge_participant_id': participant.challonge_participant_id,
            },
            EventType.TOURNAMENT_ENROLLED if added else EventType.TOURNAMENT_WITHDRAWN,
            event_extra={'user_id': user.id},
        )
        if not added:
            from application.services import opt_in_notifications
            from application.services.discord import discord_queue
            discord_queue.enqueue(opt_in_notifications.enrolment_changed(
                tournament.id, user.id, user.id, added=False,
            ))
