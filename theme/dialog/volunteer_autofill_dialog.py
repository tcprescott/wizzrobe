"""Dialog collecting the policy for one auto-fill run.

The autoscheduler used to run the instant the button was clicked, with its two
most consequential choices — how many hours one person may be given, and whether
someone who never said they could work this time may be used at all — hardcoded
as tiebreakers. Both are the coordinator's call, so they are asked here.
"""

from typing import List, Optional, Sequence

from nicegui import ui

from application.services.volunteer.volunteer_autoschedule_service import (
    DEFAULT_MAX_HOURS,
    DraftPolicy,
)
from theme.busy import busy_click
from theme.dialog._helpers import dialog_actions, dialog_header, mobile_sheet


def build_policy(max_hours, fill_outside_availability: bool) -> DraftPolicy:
    """Turn the dialog's raw control values into a policy.

    Module-level so the defaults can be asserted without a slot context. A blank
    or zero hours field means "no ceiling", which is what `max_hours=0` disables.
    """
    try:
        hours = float(max_hours) if max_hours not in (None, '') else 0.0
    except (TypeError, ValueError):
        hours = DEFAULT_MAX_HOURS
    return DraftPolicy(
        max_hours=max(0.0, hours),
        fill_outside_availability=bool(fill_outside_availability),
    )


class VolunteerAutofillDialog:
    """Asks for a `DraftPolicy` and a position filter, then hands both back.

    Deliberately does not call the service: the page owns the run so it also owns
    the refreshes and the result panel that follow it.
    """

    def __init__(self, positions: Sequence, on_submit=None):
        self.positions = list(positions)
        self.on_submit = on_submit
        self.dialog = None

    async def open(self) -> None:
        options = {p.id: p.name for p in self.positions}

        with ui.dialog() as dialog, ui.card().classes('dialog-card dialog-card--sm'):
            self.dialog = dialog
            mobile_sheet(dialog)
            dialog_header('Auto-fill from availability', dialog)

            with ui.column().classes('q-pa-md gap-3 full-width'):
                hours_input = ui.number(
                    'Maximum hours per volunteer', value=DEFAULT_MAX_HOURS, min=0, step=1,
                ).classes('full-width')
                hours_input.props('hint="Across this day. Leave blank for no limit."')

                outside_checkbox = ui.checkbox(
                    'Also use volunteers who have not marked this time', value=False,
                )
                ui.label(
                    'They will be listed for you to review. Anyone who marked the '
                    'time unavailable is never used.'
                ).classes('text-caption text-grey')

                position_select = ui.select(
                    options, multiple=True, label='Positions',
                    value=list(options.keys()),
                ).classes('full-width').props('use-chips')

            async def submit() -> None:
                policy = build_policy(hours_input.value, outside_checkbox.value)
                position_ids: Optional[List[int]] = list(position_select.value or [])
                # An empty pick means every position, matching the previous
                # button's behaviour rather than silently filling nothing.
                if not position_ids or len(position_ids) == len(options):
                    position_ids = None
                if self.on_submit:
                    await self.on_submit(policy, position_ids)
                dialog.close()

            with dialog_actions().classes('justify-end'):
                ui.button('Cancel', on_click=dialog.close).props('flat')
                ui.button('Create draft', icon='smart_toy', on_click=busy_click('Creating draft…', submit)) \
                    .props('color=primary')

            dialog.open()
