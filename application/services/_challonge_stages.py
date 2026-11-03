"""Advancing a linked bracket's stage on Challonge, mixed into ``ChallongeService``.

Challonge has no webhooks, so a stage change made on Challonge's site reaches
Wizzrobe only on the next sync. Making the change from Wizzrobe closes that gap:
each action calls ``change_state`` and re-syncs in the same step, so newly
created matches (the bracket after the groups) are bookable immediately.

The actions on offer follow the state cached by the last sync
(``Tournament.challonge_state``). Reset is deliberately absent: it wipes results
on Challonge and stays on Challonge's own site.
"""

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from application.feature_flags import requires_feature
from application.repositories import ChallongeRepository
from application.services.audit_service import AuditActions, AuditService
from application.utils.clients.challonge_client import ChallongeAPIError, ChallongeClient
from models import FeatureFlag, Tournament, User

STAGE_WRITE_SCOPE = 'tournaments:write'

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageAction:
    key: str
    label: str
    # Challonge change_state values, applied in order.
    steps: Tuple[str, ...]
    confirm: str


START_GROUP_STAGE = StageAction(
    'start_group_stage', 'Start group stage', ('start_group_stage',),
    'Start the group stage on Challonge? Entrants are locked in and the group '
    'matches open for scheduling.',
)
START_TOURNAMENT = StageAction(
    'start', 'Start tournament', ('start',),
    'Start the tournament on Challonge? Entrants are locked in and the first '
    'round opens for scheduling.',
)
ADVANCE_TO_BRACKET = StageAction(
    'advance_to_bracket', 'Finalize groups and start bracket', ('finalize_group_stage', 'start'),
    'Finalize the group stage and start the bracket on Challonge? Group results '
    'become final, and the bracket matches open for scheduling right away. '
    'Every group match has to be finished first.',
)
START_BRACKET = StageAction(
    'start_bracket', 'Start bracket', ('start',),
    'Start the bracket on Challonge? Its first-round matches open for scheduling.',
)
FINALIZE_TOURNAMENT = StageAction(
    'finalize', 'Finalize tournament', ('finalize',),
    'Finalize the tournament on Challonge? Results become final and the bracket '
    'can no longer be edited there.',
)

_STATE_LABELS = {
    'pending': 'Not started',
    'checking_in': 'Checking in',
    'checked_in': 'Check-in closed',
    'accepting_predictions': 'Accepting predictions',
    'group_stages_underway': 'Group stage underway',
    'group_stages_finalized': 'Groups finalized',
    'underway': 'Underway',
    'awaiting_review': 'Awaiting review',
    'complete': 'Complete',
}


def stage_label(state: Optional[str]) -> Optional[str]:
    if not state:
        return None
    return _STATE_LABELS.get(state, state.replace('_', ' ').capitalize())


def stage_actions(state: Optional[str], group_stage: bool) -> List[StageAction]:
    """The stage actions that make sense from ``state``; empty when none do."""
    if state in ('pending', 'checking_in', 'checked_in', 'accepting_predictions'):
        return [START_GROUP_STAGE if group_stage else START_TOURNAMENT]
    if state == 'group_stages_underway':
        return [ADVANCE_TO_BRACKET]
    if state == 'group_stages_finalized':
        return [START_BRACKET]
    if state == 'awaiting_review':
        return [FINALIZE_TOURNAMENT]
    return []


class ChallongeStageMixin:
    """Collaborators come from ``ChallongeService``; see its ``__init__``."""

    repository: ChallongeRepository
    audit_service: AuditService
    _api_client: Callable[[], ChallongeClient]
    _editable_tournament: Callable[[int, User], Awaitable[Tournament]]
    _sync_tournament: Callable[..., Awaitable[Dict[str, int]]]

    @staticmethod
    def stage_actions_for(tournament: Tournament) -> List[StageAction]:
        return stage_actions(tournament.challonge_state, tournament.challonge_group_stage)

    @staticmethod
    def stage_label_for(tournament: Tournament) -> Optional[str]:
        return stage_label(tournament.challonge_state)

    @requires_feature(FeatureFlag.CHALLONGE)
    async def can_change_stages(self) -> bool:
        """Whether the connection was granted the scope ``change_state`` needs."""
        connection = await self.repository.get_connection()
        return bool(connection and STAGE_WRITE_SCOPE in (connection.scopes or '').split())

    @requires_feature(FeatureFlag.CHALLONGE)
    async def advance_stage(self, tournament_id: int, action_key: str, actor: User) -> Tournament:
        """Run one stage action on Challonge, then re-sync the mirror."""
        tournament = await self._editable_tournament(tournament_id, actor)
        if not tournament.challonge_tournament_id:
            raise ValueError("This tournament is not linked to a Challonge tournament yet.")
        action = next((a for a in self.stage_actions_for(tournament) if a.key == action_key), None)
        if action is None:
            raise ValueError(
                "That isn't the next step for this bracket any more. Sync it to see "
                "where it stands on Challonge."
            )
        if not await self.can_change_stages():
            raise ValueError(
                "The Challonge connection can't change tournament stages yet. "
                "Reconnect Challonge on this page to grant it."
            )

        done: List[str] = []
        failure: Optional[str] = None
        for step in action.steps:
            try:
                await self._api_client().change_state(
                    tournament.challonge_tournament_id, step,
                    community=tournament.challonge_community,
                )
            except ChallongeAPIError as e:
                failure = _challonge_error_detail(e)
                break
            done.append(step)

        if done:
            await self.audit_service.write_log(
                actor, AuditActions.CHALLONGE_STAGE_ADVANCED,
                {'tournament_id': tournament.id, 'action': action.key,
                 'from_state': tournament.challonge_state, 'steps': done},
            )
            # The state moved even if a later step failed, so the cached state
            # (and the next offered action) must catch up before reporting.
            await self._sync_tournament(tournament, actor, force=True)

        if failure is not None:
            if done:
                raise ValueError(
                    f"Challonge finished '{done[-1].replace('_', ' ')}' but refused the "
                    f"next step: {failure} The bracket has been re-synced; try the "
                    f"next step again from here."
                )
            refused = f"Challonge refused to {action.label.lower()}: {failure}"
            try:
                await self._sync_tournament(tournament, actor, force=True)
            except (ValueError, ChallongeAPIError):
                logger.warning(
                    "Challonge re-sync after a refused stage change failed for tournament %s",
                    tournament.id, exc_info=True,
                )
                raise ValueError(refused) from None
            raise ValueError(
                f"{refused} The bracket has been re-synced, so this page now shows "
                f"where it stands on Challonge."
            )
        return tournament


def _challonge_error_detail(error: ChallongeAPIError) -> str:
    """Challonge's own explanation, without the status-code wrapper."""
    import json

    text = str(error)
    body = text.split(': ', 1)[1] if ': ' in text else text
    try:
        errors = json.loads(body).get('errors')
    except (ValueError, AttributeError):
        return text
    if isinstance(errors, dict):
        detail = errors.get('detail')
    elif isinstance(errors, list) and errors:
        first = errors[0]
        detail = first.get('detail') if isinstance(first, dict) else str(first)
    else:
        detail = None
    if isinstance(detail, list):
        detail = ' '.join(str(d) for d in detail)
    return str(detail).rstrip('.') + '.' if detail else text
