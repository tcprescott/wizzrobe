"""
Check-in Desk Service - Business Logic Layer

The desk's reads for callers that arrive without a page gate in front of them:
the REST API and MCP. The desk page itself is behind ``@protected_page``; these
carry the same rule in the service so every other caller gets it too: staff
and Check-in Desk volunteers may read, and a desk volunteer sees only open
events, the ones the desk page offers them.

Kept beside :class:`CheckInService` rather than inside it, which is at its
file-length limit; the desk's roster writes stay there. The one write here is
re-sending the check-in test DM, which touches only the DM columns.
"""

from typing import List, Tuple

from application.errors import require_found
from application.events import check_in_live
from application.feature_flags import requires_feature
from application.repositories import CheckInEntrantRepository, CheckInEventRepository
from application.services.auth_service import AuthService
from application.services.check_in_accounts import desk_entrant, ensure_desk_sees
from application.services.check_in_dm import start_dm_check
from models import CheckInEntrant, CheckInEvent, CheckInEventStatus, FeatureFlag, User

_DESK_DENIED = 'Only staff and check-in desk volunteers can do that.'


class CheckInDeskService:
    """What the check-in desk reads, gated on who may work it."""

    def __init__(self) -> None:
        self.events = CheckInEventRepository()
        self.entrants = CheckInEntrantRepository()

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def events_for(self, actor: User) -> List[CheckInEvent]:
        """The events this actor can work: every event for staff, open ones for the desk."""
        await AuthService.ensure(await AuthService.can_run_check_in_desk(actor), _DESK_DENIED)
        if await AuthService.can_manage_check_in(actor):
            return await self.events.list_all()
        return await self.events.list_by_status(CheckInEventStatus.OPEN)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def roster_for(self, actor: User, event_id: int) -> Tuple[CheckInEvent, List[CheckInEntrant]]:
        """An event and its roster, for someone working the desk."""
        await AuthService.ensure(await AuthService.can_run_check_in_desk(actor), _DESK_DENIED)
        event = await ensure_desk_sees(
            actor, require_found(await self.events.get_by_id(event_id), 'Check-in event'),
        )
        return event, await self.entrants.list_for_event(event)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def send_test_dm(self, actor: User, entrant_id: int) -> CheckInEntrant:
        """Send the check-in DM again, after the person has opened their DMs.

        Refused for someone not checked in (the DM says they are) and for a row
        with no linked account, since there's nobody to message.
        """
        await AuthService.ensure(await AuthService.can_run_check_in_desk(actor), _DESK_DENIED)
        entrant = await desk_entrant(
            actor, require_found(await self.entrants.get_by_id(entrant_id), 'Roster entry'),
        )
        if entrant.checked_in_at is None:
            raise ValueError(f'Check {entrant.display_name} in first.')
        if entrant.user is None or not entrant.user.discord_id:
            raise ValueError(f"{entrant.display_name} isn't linked to a Discord account, so there's no one to DM.")
        await start_dm_check(entrant, entrant.event.name)
        check_in_live.publish(entrant.event_id, entrant.id, check_in_live.CHANGED)
        return entrant
