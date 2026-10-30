"""The Edit member dialog's roles: community-wide, then roles in one event.

Thomas picked layout A (2026-10-26). Staff and the online admin roles cover
every event and sit in one picker; the on-site roles are held per event, so
their picker follows an event chosen beside its label, the event on screen by
default. A line under it names what the person holds in the other events, so
switching isn't the only way to find out. Edits in several events survive
switching between them and are saved together.
"""

from typing import Dict, Iterable, List, Optional, Set, Tuple

from nicegui import ui

from application.services.event_service import EventRef
from models import Role
from theme.pickers import CHIP_PROPS

Grant = Tuple[Role, Optional[int]]

COMMUNITY_HINT = 'Staff, Preset Manager, Sync Admin and Qualifier Admin cover every event.'


def _status_suffix(event: EventRef) -> str:
    if event.is_draft:
        return ' (Draft)'
    if event.is_archived:
        return ' (Archived)'
    return ''


def also_holds(grants: Iterable[Grant], events: Iterable[EventRef], shown_event_id: Optional[int]) -> str:
    """``Also holds: Proctor in SGL 2025 (Archived); Volunteer Coordinator in SGL 2027 (Draft).``

    The per-event grants outside the event on show, by event in the switcher's
    order; empty when there are none. Pure, for tests.
    """
    by_event: Dict[int, List[str]] = {}
    for role, event_id in grants:
        if event_id is not None and event_id != shown_event_id:
            by_event.setdefault(event_id, []).append(role.label)
    parts = [
        f"{', '.join(sorted(by_event[e.id]))} in {e.name}{_status_suffix(e)}"
        for e in events if e.id in by_event
    ]
    return f"Also holds: {'; '.join(parts)}." if parts else ''


def grant_changes(
    held: Iterable[Grant], community: Iterable[Role], per_event: Dict[int, Set[Role]],
) -> Tuple[List[Grant], List[Grant]]:
    """``(to grant, to revoke)`` from what is held and what the pickers say.

    Only the events someone opened in the picker are compared, so a grant in an
    event nobody looked at is never revoked. Pure, for tests.
    """
    held = set(held)
    wanted: Set[Grant] = {(role, None) for role in community}
    compared: Set[Grant] = {(role, None) for role in Role.community_wide()}
    for event_id, roles in per_event.items():
        wanted |= {(role, event_id) for role in roles}
        compared |= {(role, event_id) for role in Role.per_event()}
    key = _grant_order
    return (
        sorted(wanted - held, key=key),
        sorted((held & compared) - wanted, key=key),
    )


def _grant_order(grant: Grant) -> Tuple[int, str]:
    return grant[1] or 0, grant[0].value


class RoleGrantsEditor:
    """Builds the two pickers inside the dialog's column and reports the diff on save."""

    def __init__(self, held: Iterable[Grant], events: List[EventRef], event_id: Optional[int]) -> None:
        self.held: Set[Grant] = set(held)
        self.events = events
        self.choosable = [e for e in events if not e.is_archived]
        ids = {e.id for e in self.choosable}
        self.event_id = event_id if event_id in ids else (self.choosable[0].id if self.choosable else None)
        self.per_event: Dict[int, Set[Role]] = {}
        self.community_select: Optional[ui.select] = None
        self.event_select: Optional[ui.select] = None
        self.role_select: Optional[ui.select] = None
        self.hint: Optional[ui.label] = None

    def _held_in(self, event_id: Optional[int]) -> List[str]:
        return [role.value for role, eid in self.held if eid == event_id and role.is_per_event]

    def build(self) -> None:
        community = [role.value for role, eid in self.held if eid is None and not role.is_per_event]
        ui.label('Community-wide roles').classes('wiz-role-group__label')
        self.community_select = ui.select(
            options={r.value: r.label for r in Role.community_wide()},
            value=community, multiple=True,
        ).props(f'{CHIP_PROPS} aria-label="Community-wide roles"').classes('input-full-width')
        ui.label(COMMUNITY_HINT).classes('wiz-field-hint')

        if self.event_id is None:
            return
        with ui.row().classes('wiz-role-group__head items-center no-wrap'):
            ui.label('Roles in').classes('wiz-role-group__label')
            if len(self.choosable) > 1:
                self.event_select = ui.select(
                    options={e.id: f'{e.name}{_status_suffix(e)}' for e in self.choosable},
                    value=self.event_id, on_change=self._switch_event,
                ).props('dense outlined options-dense aria-label="Event the roles below are held in"') \
                    .classes('wiz-role-group__event')
            else:
                ui.label(self.choosable[0].name).classes('wiz-role-group__label wiz-role-group__label--event')
        self.role_select = ui.select(
            options={r.value: r.label for r in Role.per_event()},
            value=self._held_in(self.event_id), multiple=True,
        ).props(f'{CHIP_PROPS} aria-label="Roles in the chosen event"').classes('input-full-width')
        self.hint = ui.label('').classes('wiz-field-hint')
        self._refresh_hint()

    def _remember(self) -> None:
        if self.role_select is not None and self.event_id is not None:
            self.per_event[self.event_id] = {Role(v) for v in self.role_select.value or []}

    def _switch_event(self, e) -> None:
        self._remember()
        self.event_id = e.value
        assert self.role_select is not None and self.event_id is not None
        pending = self.per_event.get(self.event_id)
        self.role_select.value = [r.value for r in pending] if pending is not None else self._held_in(self.event_id)
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        if self.hint is None:
            return
        pending = [(role, eid) for eid, roles in self.per_event.items() if eid != self.event_id for role in roles]
        untouched = [(role, eid) for role, eid in self.held if eid not in self.per_event]
        text = also_holds([*untouched, *pending], self.events, self.event_id)
        self.hint.set_text(text)
        self.hint.set_visibility(bool(text))

    def changes(self) -> Tuple[List[Grant], List[Grant]]:
        self._remember()
        assert self.community_select is not None
        community = [Role(v) for v in self.community_select.value or []]
        return grant_changes(self.held, community, self.per_event)
