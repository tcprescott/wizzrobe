"""What the viewer of a match board may actually do — one field per service gate.

The board used to carry a single ``can_crud`` boolean and render its controls off
callback presence. The services it calls have never been that coarse:
``can_run_match`` admits a proctor, ``can_confirm_match`` does not,
``can_assign_match_stream`` admits a Broadcast Tech (``STREAM_MANAGER``), and ``can_approve_crew``
admits a crew coordinator. Collapsing four audiences into one boolean is what
handed a crew coordinator 37 lifecycle controls that every service refused and
left the Broadcast Tech — the role ``assign_stage`` names in its own docstring —
with no surface that assigns a stage.

Each field below maps to exactly one gate in ``AuthService``:

============  ==========================  =========================================
field         gate                        controls
============  ==========================  =========================================
``edit``      ``can_crud_match``          the id link, the match dialog, Create Match
``run``       ``can_run_match``           Check In, Start, Finish, Generate, Stations
``confirm``   ``can_confirm_match``       Confirm, Record/Change Winner
``assign``    ``can_assign_match_stream`` Assign (stage)
``crew``      ``can_approve_crew``        the crew approval toggle and name link
============  ==========================  =========================================

These are **page-level** approximations of per-match gates: a tournament admin's
board answers "may you do this to *some* match here", and the service re-checks
the actual match on every call. That is deliberate — the board is the cheap
filter, the service is the enforcement — but it is why a scoped board matters
too: see ``admin_schedule_page``'s ``tournament_ids``.

Rows are where the page-level answer gets refined. Each capability carries a
*reach*: everywhere, a set of tournaments, and (for running) any match on a
stage. A Broadcast Tech runs only matches on a stage; a tournament admin who is
also a Broadcast Tech (whose board is unscoped) edits and confirms only their
own tournaments' rows; a tournament admin of A who coordinates crew for B runs
A's matches and approves crew on both. ``can_*_row`` answers per row in Python
and the ``*_js`` methods give the same answer as a Vue expression for slot
templates, so a row never offers a control its service refuses.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

_SCOPE_FIELDS = (
    'edit_scope', 'run_scope', 'confirm_scope', 'stream_scope', 'crew_scope', 'run_on_stage',
)


def _row_ok(enabled: bool, scope: Optional[frozenset[int]], row: dict, on_stage: bool = False) -> bool:
    if not enabled:
        return False
    if scope is None:
        return True
    return row.get('tournament_id') in scope or (on_stage and bool(row.get('stage_id')))


def _row_js(enabled: bool, scope: Optional[frozenset[int]], row: str, on_stage: bool = False) -> str:
    if not enabled:
        return 'false'
    if scope is None:
        return 'true'
    parts = []
    if scope:
        ids = ','.join(str(i) for i in sorted(scope))
        parts.append(f'[{ids}].includes({row}.tournament_id)')
    if on_stage:
        parts.append(f'!!{row}.stage_id')
    return '(' + ' || '.join(parts) + ')' if parts else 'false'


@dataclass(frozen=True)
class MatchBoardAccess:
    """One field per capability the match board offers. Default is a spectator.

    The ``*_scope`` fields narrow a capability to those tournaments' rows; None
    means every row. ``run_on_stage`` adds any match on a stage to ``run``'s reach.
    """

    edit: bool = False
    run: bool = False
    confirm: bool = False
    assign_stream: bool = False
    approve_crew: bool = False
    edit_scope: Optional[frozenset[int]] = None
    run_scope: Optional[frozenset[int]] = None
    confirm_scope: Optional[frozenset[int]] = None
    stream_scope: Optional[frozenset[int]] = None
    crew_scope: Optional[frozenset[int]] = None
    run_on_stage: bool = False
    # Offer "Pin to stream overlay" on the match sheet (Stream Layouts flag).
    pin_overlay: bool = False

    def any(self) -> bool:
        return any(getattr(self, f) for f in self.__dataclass_fields__ if f not in _SCOPE_FIELDS)

    @property
    def run_on_stage_only(self) -> bool:
        return self.run_on_stage and self.run_scope == frozenset()

    def can_edit_row(self, row: dict) -> bool:
        return _row_ok(self.edit, self.edit_scope, row)

    def can_run_row(self, row: dict) -> bool:
        return _row_ok(self.run, self.run_scope, row, self.run_on_stage)

    def can_confirm_row(self, row: dict) -> bool:
        return _row_ok(self.confirm, self.confirm_scope, row)

    def can_assign_stream_row(self, row: dict) -> bool:
        return _row_ok(self.assign_stream, self.stream_scope, row)

    def can_approve_crew_row(self, row: dict) -> bool:
        return _row_ok(self.approve_crew, self.crew_scope, row)

    def edit_js(self, row: str = 'props.row') -> str:
        return _row_js(self.edit, self.edit_scope, row)

    def run_js(self, row: str = 'props.row') -> str:
        """``run`` as a Vue expression over ``row``, for slot templates."""
        return _row_js(self.run, self.run_scope, row, self.run_on_stage)

    def confirm_js(self, row: str = 'props.row') -> str:
        return _row_js(self.confirm, self.confirm_scope, row)

    def stream_js(self, row: str = 'props.row') -> str:
        return _row_js(self.assign_stream, self.stream_scope, row)

    def crew_js(self, row: str = 'props.row') -> str:
        return _row_js(self.approve_crew, self.crew_scope, row)

    def edit_slot_js(self) -> str:
        """``edit_js`` for a slot that already exists only when an edit callback
        is wired: a scoped board narrows it per row, any other leaves it to the
        callback."""
        return self.edit_js() if self.edit_scope is not None else 'true'

    def stream_slot_js(self) -> str:
        """``stream_js`` on the same terms as :meth:`edit_slot_js`."""
        return self.stream_js() if self.stream_scope is not None else 'true'

    @classmethod
    def for_roles(
        cls,
        *,
        is_staff: bool = False,
        is_proctor: bool = False,
        is_tournament_admin: bool = False,
        is_stream_manager: bool = False,
        is_crew_coordinator: bool = False,
        admin_tournament_ids: Optional[Iterable[int]] = None,
        crew_tournament_ids: Optional[Iterable[int]] = None,
    ) -> 'MatchBoardAccess':
        """Resolve the capability set from the viewer's roles in this tenant.

        Mirrors the ``AuthService`` gates named in the module docstring, term for
        term. ``edit`` and ``confirm`` admit the same roles today and are still
        separate fields: they are separate gates, and a board that conflates them
        is the defect this class exists to fix.

        ``admin_tournament_ids`` and ``crew_tournament_ids`` are the tournaments
        a non-staff viewer administers and coordinates. Given, they scope each
        capability that comes only from those relations to those rows; omitted,
        the capability reaches every row, as before.
        """
        staff_or_ta = is_staff or is_tournament_admin
        full_run = staff_or_ta or is_proctor
        access = cls(
            edit=staff_or_ta,
            run=full_run or is_stream_manager,
            run_on_stage=is_stream_manager and not full_run,
            run_scope=frozenset() if is_stream_manager and not full_run else None,
            confirm=staff_or_ta,
            assign_stream=staff_or_ta or is_stream_manager,
            approve_crew=staff_or_ta or is_crew_coordinator,
        )
        if is_staff or admin_tournament_ids is None:
            return access
        ta = frozenset(admin_tournament_ids) if is_tournament_admin else frozenset()
        cc = frozenset(crew_tournament_ids or ()) if is_crew_coordinator else frozenset()
        return cls(
            edit=access.edit,
            run=access.run,
            confirm=access.confirm,
            assign_stream=access.assign_stream,
            approve_crew=access.approve_crew,
            edit_scope=ta,
            confirm_scope=ta,
            run_scope=None if is_proctor else ta,
            run_on_stage=is_stream_manager and not is_proctor,
            stream_scope=None if is_stream_manager else ta,
            crew_scope=ta | cc,
        )
