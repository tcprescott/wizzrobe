"""What the app's Proctor Station shows: the room's run sheet, worked one press per match.

The website's Proctor Station is the admin board with the proctor's line
(``pages/volunteer_tabs/proctor_station.py``). This is the same thing for the
app: :class:`AdminScheduleService`'s board and pane, grouped by
:func:`proctor_group`, with no crew, no editing and no "Needs you" inbox, and
racetime matches left off (the room bot runs those).
"""

from typing import Optional

from application.match_board_access import MatchBoardAccess
from application.run_sheet import proctor_group
from application.services.admin_schedule_service import AdminScheduleService
from application.services.auth_service import AuthService
from models import User


class ProctorStationService(AdminScheduleService):
    """Screen-shaped reads for Proctor Station. Stateless."""

    group = staticmethod(proctor_group)
    exclude_racetime = True
    show_crew = False
    show_needs = False

    @staticmethod
    async def board_access(actor: User) -> tuple[MatchBoardAccess, Optional[frozenset[int]]]:
        """``(what the viewer may do, no tournament narrowing)``.

        A Broadcast Tech who isn't a proctor runs only matches on a stage, so
        their board holds only those (``proctor_station_on_stage_only``).
        """
        if not await AuthService.can_open_proctor_station(actor):
            raise PermissionError("You don't have access to Proctor Station.")
        if await AuthService.proctor_station_on_stage_only(actor):
            return MatchBoardAccess(run=True, run_scope=frozenset(), run_on_stage=True), None
        return MatchBoardAccess(run=True), None

    @staticmethod
    def keep_row(access: MatchBoardAccess, row: dict) -> bool:
        return not access.run_on_stage or row.get('stage_id') is not None
