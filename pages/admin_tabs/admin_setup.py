"""Admin Setup tab — the new-community checklist.

Only present while the community is not ready; ``build_admin_tabs`` drops it
once every required step is done, so there is nothing to dismiss.
"""

from typing import AbstractSet, List, Optional

from nicegui import ui

from application.services import SetupStep
from theme.base import tab_slug
from theme.setup_checklist import render_setup_checklist


def admin_setup_page(
    steps: List[SetupStep],
    base_path: str = '/admin',
    reachable_tabs: Optional[AbstractSet[str]] = None,
) -> None:
    def go(tab: str) -> None:
        ui.navigate.to(f'{base_path}/{tab_slug(tab)}')

    render_setup_checklist(steps, on_go=go, reachable_tabs=reachable_tabs)
