"""Check-in desk reads: which events you can work and who is on each roster.

Gated at **actor** behind ``EVENT_CHECK_IN``, mirroring
``api/routers/check_in.py``. ``CheckInDeskService`` and ``CheckInService`` decide the rest, as it does for
the desk page: staff and Check-in Desk volunteers may read, and a desk
volunteer sees only open events.
"""

from typing import List, Optional

from mcp.server.mcpserver import MCPServer

from api.schemas.check_in import CheckInEntrantResponse, CheckInEventResponse
from application.api_scopes import ApiScope
from application.services import CheckInDeskService, CheckInService
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import TenantArg
from models import FeatureFlag

# A roster can run to the whole venue. Without a cap one call is every badge
# holder at the event, which buries the one person the desk is looking for.
MAX_ROSTER_ROWS = 200


async def list_check_in_events(tenant: TenantArg = None) -> List[CheckInEventResponse]:
    """The check-in events you can work, newest first, with when each last synced."""
    events = await CheckInDeskService().events_for(current_actor().user)
    return [CheckInEventResponse.from_row(e) for e in events]


async def get_check_in_roster(
    event_id: int,
    tenant: TenantArg = None,
    search: Optional[str] = None,
    not_checked_in_only: bool = False,
) -> List[CheckInEntrantResponse]:
    """People on an event's roster: badges, lanyard, account link and check-in state.

    `event_id` comes from `list_check_in_events`. `search` matches their
    registered name or linked account name. Returns at most 200 rows; narrow
    with `search` to find one person. A row with `withdrawn_at` set no longer
    holds a badge.
    """
    service = CheckInService()
    _, entrants = await CheckInDeskService().roster_for(current_actor().user, event_id)
    volunteers = await service.volunteer_user_ids()
    rows = [CheckInEntrantResponse.from_row(e, volunteers) for e in entrants]
    if not_checked_in_only:
        rows = [r for r in rows if r.checked_in_at is None]
    needle = (search or '').strip().lower()
    if needle:
        rows = [
            r for r in rows
            if needle in r.display_name.lower() or needle in (r.user_name or '').lower()
        ]
    return rows[:MAX_ROSTER_ROWS]


def register_tools(mcp: MCPServer) -> None:
    register(
        mcp, list_check_in_events, gate=Gate.ACTOR, scope=ApiScope.STAFF_READ,
        feature=FeatureFlag.EVENT_CHECK_IN, title='List check-in events',
    )
    register(
        mcp, get_check_in_roster, gate=Gate.ACTOR, scope=ApiScope.STAFF_READ,
        feature=FeatureFlag.EVENT_CHECK_IN, title='Get check-in roster',
    )
