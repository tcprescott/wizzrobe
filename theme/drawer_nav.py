"""The drawer's navigation: Watch live, the section strip and the page list.

Home, Volunteer and Admin switch the page list in place, so the menu stays
open while the viewer looks through another section; only picking a page
navigates. Another section's list is loaded once, on first tap, from the
provider its page registered (``theme.section_menu``).
"""

import logging
from typing import TYPE_CHECKING

from nicegui import ui

from application.tenant_context import tenant_scope
from theme.event_switcher import event_status_chip
from theme.nav_badges import drawer_tab_badge, nav_icon
from theme.nav_section import mark_nav_section, nav_section_matches
from theme.section_menu import section_menu_provider

if TYPE_CHECKING:
    from theme.base import BaseLayout

logger = logging.getLogger(__name__)

WATCH_URL = '/live'


def home_menu() -> list[dict]:
    from theme.base import HOME_SECTIONS
    return [{'label': label, 'icon': icon} for label, icon in HOME_SECTIONS]


def section_page_path(section_url: str, tab: dict) -> str:
    """Where a section's tab lives: Home's at ``/home/<slug>``, a hub's under its own path."""
    from theme.base import tab_slug
    slug = tab.get('slug') or tab_slug(tab['label'])
    return f"{'/home' if section_url == '/' else section_url}/{slug}"


def render_drawer_nav(layout: 'BaseLayout', current_path: str) -> None:
    # Watch has no pages of its own, so it is a plain link above the section
    # switcher rather than one of its tabs.
    for item in layout.top_menu:
        if item['url'] != WATCH_URL:
            continue
        with ui.list().props('padding'):
            with ui.item(on_click=lambda u=item['url']: ui.navigate.to(u)) \
                    .props('clickable v-ripple').classes('wiz-drawer-watch') as watch_item:
                with ui.item_section().props('avatar'):
                    ui.icon(nav_icon(item['icon'])).props('size=sm')
                with ui.item_section():
                    ui.item_label('Watch live')
        if nav_section_matches(item['url'], current_path):
            mark_nav_section(watch_item)

    sections = [item for item in layout.top_menu if item['url'] != WATCH_URL]
    current = next(
        (item['url'] for item in sections if nav_section_matches(item['url'], current_path)), '/',
    )
    layout._section_buttons = {}
    layout._section_lists = {}
    if len(sections) > 1:
        with ui.element('div').classes('wiz-drawer-sections') \
                .props('role=tablist aria-label="Menu sections"'):
            for item in sections:
                btn = ui.button(
                    item['label'], icon=nav_icon(item['icon']), color=None,
                    on_click=lambda u=item['url']: show_section(layout, u),
                ).props('flat no-caps stack role=tab').classes('wiz-drawer-section')
                layout._section_buttons[item['url']] = btn
    with ui.element('div').classes('wiz-drawer-pages') as layout._section_host:
        with ui.list().props('padding') as current_list:
            if layout.tabs:
                render_page_items(layout, layout.tabs)
            elif layout._is_member:
                # A tab-less page has no section list of its own, so a member
                # gets home's: the bracket or article they followed a link to
                # is not where they stop being a player.
                render_page_items(layout, home_menu(), section_url='/')
    layout._section_lists[current] = current_list
    mark_section_button(layout, current)


def render_page_items(layout: 'BaseLayout', tabs: list[dict], section_url: str | None = None) -> None:
    """A section's pages as drawer rows: this page's tabs switch in place
    (``section_url`` None), another section's rows navigate to it."""
    current_group = None
    current_half = None
    for tab in tabs:
        # Admin's list has two halves: the event's pages under its name and
        # status, then the community's under a Community-wide divider.
        half = tab.get('half')
        if half and half != current_half:
            current_half = half
            render_half_heading(layout, half)
        # Section headers for grouped drawers (admin passes a 'group' per tab);
        # ungrouped sections render a flat list.
        group = tab.get('group')
        if group and group != current_group:
            current_group = group
            ui.item_label(group).props('header').classes('wiz-drawer-group')
        if section_url is None:
            on_click = lambda t=tab['label']: layout._switch_tab(t)
        else:
            path = section_page_path(section_url, tab)
            on_click = lambda p=path: ui.navigate.to(p)
        with ui.item(on_click=on_click).props('clickable v-ripple') as tab_item:
            with ui.item_section().props('avatar'):
                ui.icon(nav_icon(tab.get('icon', 'circle'))).props('size=sm')
            with ui.item_section():
                ui.item_label(tab['label'])
            if section_url is None:
                drawer_tab_badge(tab)
        if section_url is None:
            layout.mark_tab_push(tab_item, tab['label'])
            if tab['label'] == layout._default_tab:
                tab_item.props(add='active')
            layout._tab_item_refs[tab['label']] = tab_item


def render_half_heading(layout: 'BaseLayout', half: str) -> None:
    """The row that opens a half of the admin list: the event, or Community-wide."""
    event = layout.current_event
    if half == 'event' and event is None:
        return
    with ui.element('div').classes(f'wiz-drawer-half wiz-drawer-half--{half}'):
        if half == 'event':
            ui.icon(nav_icon('event')).props('size=sm')
            ui.label(event.name).classes('wiz-drawer-half__name')
            event_status_chip(event.status)
        else:
            ui.icon(nav_icon('apartment')).props('size=sm')
            ui.label('Community-wide').classes('wiz-drawer-half__name')


def mark_section_button(layout: 'BaseLayout', url: str) -> None:
    for btn_url, btn in layout._section_buttons.items():
        if btn_url == url:
            btn.classes(add='wiz-drawer-section--on').props('aria-selected=true')
        else:
            btn.classes(remove='wiz-drawer-section--on').props('aria-selected=false')


async def show_section(layout: 'BaseLayout', url: str) -> None:
    """Swap the drawer's page list to the section at ``url``, loading it once."""
    if url not in layout._section_lists:
        tabs = await section_tabs(layout, url)
        host = layout._section_host
        assert host is not None, 'the strip only renders inside the drawer that sets the host'
        with host:
            with ui.list().props('padding') as section_list:
                if tabs:
                    render_page_items(layout, tabs, section_url=url)
                else:
                    ui.item_label('Nothing to open here.').props('caption').classes('q-px-md')
        layout._section_lists[url] = section_list
    for list_url, section_list in layout._section_lists.items():
        section_list.set_visibility(list_url == url)
    mark_section_button(layout, url)


async def section_tabs(layout: 'BaseLayout', url: str) -> list[dict]:
    if url == '/':
        return home_menu() if layout._is_member else []
    provider = section_menu_provider(url)
    if provider is None or layout.user is None:
        return []
    try:
        with tenant_scope(layout._tenant_id):
            return await provider(layout.user)
    except Exception:
        logger.exception('Failed to list the %s section for the drawer', url)
        return []
