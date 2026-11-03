"""The new-community setup checklist panel.

Derived state only (:class:`~application.services.TenantSetupService`), so the
panel disappears on its own once the required steps are done. See the
tenant-onboarding plan, design decision 3.
"""

from typing import AbstractSet, Callable, List, Optional

from nicegui import ui

from application.services import SetupStep
from theme.page_header import page_header


def render_setup_checklist(
    steps: List[SetupStep],
    on_go: Optional[Callable[[str], None]] = None,
    reachable_tabs: Optional[AbstractSet[str]] = None,
) -> None:
    """Render the checklist. ``on_go`` receives a tab label to switch to.

    ``reachable_tabs`` is the viewer's own Admin tabs, when known: a step done on
    a tab they can't open says who does it rather than linking to a refusal.
    """
    required = [s for s in steps if s.required]
    advisory = [s for s in steps if not s.required]

    with ui.column().classes('page-container-narrow w-full'):
        page_header(
            'Set up your community',
            'Matches hang off tournaments, and tournaments need players enrolled '
            'in them — so the order is tournament → enrol players → schedule a '
            'match. Work down this list and it will disappear.',
        )

        with ui.card().classes('w-full'):
            with ui.element('div').classes('wiz-tile-grid'):
                for step in required:
                    _render_step(step, on_go, reachable_tabs)

            if advisory:
                ui.separator().classes('q-my-sm')
                ui.label('Recommended, not required').classes('text-caption text-grey')
                with ui.element('div').classes('wiz-tile-grid'):
                    for step in advisory:
                        _render_step(step, on_go, reachable_tabs)


def _render_step(
    step: SetupStep,
    on_go: Optional[Callable[[str], None]],
    reachable_tabs: Optional[AbstractSet[str]] = None,
) -> None:
    with ui.row().classes('items-start no-wrap w-full q-py-xs gap-3'):
        ui.icon(
            'check_circle' if step.done else 'radio_button_unchecked',
        ).props(f"size=sm color={'positive' if step.done else 'grey-6'}")
        with ui.column().classes('gap-0 grow'):
            ui.label(step.label).classes('text-weight-medium' if not step.done else 'text-grey')
            if not step.done:
                ui.label(step.hint).classes('text-caption text-grey')
        if step.done or on_go is None:
            return
        if reachable_tabs is not None and step.tab not in reachable_tabs:
            ui.label('Staff do this').classes('text-caption text-grey text-no-wrap')
        else:
            ui.button(
                step.tab, icon='arrow_forward',
                on_click=lambda _=None, tab=step.tab: on_go(tab),
            ).props('flat dense color=primary')
