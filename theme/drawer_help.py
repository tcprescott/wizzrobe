"""The drawer's "Info & support" card, at its foot inside a community."""

from typing import TYPE_CHECKING

from nicegui import ui

from theme.nav_badges import nav_icon
from theme.nav_section import mark_nav_section, nav_section_matches

GITHUB_URL = 'https://github.com/tcprescott/wizzrobe'

if TYPE_CHECKING:
    from theme.base import BaseLayout


def render_drawer_help(layout: 'BaseLayout') -> None:
    """The drawer's info card: Event Guide, Qualifiers, Help, Feedback, GitHub.

    Only inside a community — off one there is no /help or /event-info to
    reach, and an item pointing at the page it sits on is a circle.
    """
    from theme.base import current_local_url
    from theme.dialog import FeedbackDialog

    current_path = current_local_url().split('?', 1)[0]
    with ui.list().props('padding').classes('wiz-drawer-info'):
        ui.item_label('Info & support').props('header').classes('wiz-drawer-group')
        if layout._show_event_info:
            with ui.item(
                on_click=lambda: ui.navigate.to('/event-info')
            ).props('clickable v-ripple') as event_item:
                with ui.item_section().props('avatar'):
                    ui.icon(nav_icon('menu_book')).props('size=sm')
                with ui.item_section():
                    ui.item_label('Event Guide')
            if nav_section_matches('/event-info', current_path):
                mark_nav_section(event_item)

        if layout._show_qualifiers:
            with ui.item(
                on_click=lambda: ui.navigate.to('/qualifiers')
            ).props('clickable v-ripple') as qualifiers_item:
                with ui.item_section().props('avatar'):
                    ui.icon(nav_icon('timer')).props('size=sm')
                with ui.item_section():
                    ui.item_label('Qualifiers')
            if nav_section_matches('/qualifiers', current_path):
                mark_nav_section(qualifiers_item)

        with ui.item(
            on_click=lambda: ui.navigate.to('/help')
        ).props('clickable v-ripple') as help_item:
            with ui.item_section().props('avatar'):
                ui.icon(nav_icon('help')).props('size=sm')
            with ui.item_section():
                ui.item_label('Help')
        if nav_section_matches('/help', current_path):
            mark_nav_section(help_item)

        if layout.offers_feedback:
            with ui.item(
                on_click=lambda: FeedbackDialog(layout.user).open()  # type: ignore[arg-type]
            ).props('clickable v-ripple'):
                with ui.item_section().props('avatar'):
                    ui.icon(nav_icon('rate_review')).props('size=sm')
                with ui.item_section():
                    ui.item_label('Send Feedback')

        render_github_item()


def render_github_item() -> None:
    """The source-code link: last in the info card, or alone off a community."""
    with ui.item().props(
        f'clickable v-ripple tag=a href={GITHUB_URL} target=_blank rel=noopener'
    ):
        with ui.item_section().props('avatar'):
            ui.icon(nav_icon('code')).props('size=sm')
        with ui.item_section():
            ui.item_label('GitHub')
