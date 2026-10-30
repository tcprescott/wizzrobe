"""Counts on navigation items, such as the matches waiting on a player on My Schedule.

A tab declares its starting count with ``'badge': n`` in its BaseLayout tab
dict. BaseLayout draws one badge in the drawer and one on the bottom-nav tab and
registers both here, so a section that changes the count (booking a match takes
it off the list) can update them without a reference to the layout.

Keyed per client in a ``WeakKeyDictionary``, never shared module state: each
browser tab has its own badges, and they go when its client does.
"""

from weakref import WeakKeyDictionary

from nicegui import context, ui
from nicegui.client import Client

_badges: 'WeakKeyDictionary[Client, dict[str, list[ui.badge]]]' = WeakKeyDictionary()


def nav_count_badge(label: str, count: int) -> ui.badge:
    """Draw one count badge for the nav item ``label`` and register it."""
    badge = ui.badge(str(count), color=None).classes('wiz-nav-count')
    badge.set_visibility(count > 0)
    client = context.client
    if client not in _badges:
        _badges[client] = {}
        client.on_delete(lambda: _badges.pop(client, None))
    _badges[client].setdefault(label, []).append(badge)
    return badge


def drawer_tab_badge(tab: dict) -> None:
    """The drawer item's count, as a side section, when the tab declares one."""
    if 'badge' in tab:
        with ui.item_section().props('side'):
            nav_count_badge(tab['label'], tab['badge'])


def nav_icon(name: str) -> str:
    """The navigation set's spelling of a Material icon name: outlined Material Symbols.

    The drawer and the bottom bar draw outlined icons and fill the one you are
    on (``styles.css``). The font is a subset holding only the navigation icons,
    so a name missing from ``NAV_ICON_NAMES`` renders as its ligature text;
    ``tests/theme/test_nav_icons.py`` checks every nav icon is in it. A Quasar
    ``svguse:`` (Triforce Texts) or an already-prefixed name passes through.
    """
    if ':' in name or name.startswith('sym_'):
        return name
    return f'sym_o_{name}'


# Adding a nav icon means re-downloading the font subset (URL and ?v= stamp in styles.css).
NAV_ICON_NAMES: frozenset[str] = frozenset({
    'account_circle', 'account_tree', 'admin_panel_settings', 'analytics', 'apartment', 'assignment_ind',
    'calendar_month',
    'calendar_view_week', 'celebration', 'checklist', 'circle', 'code', 'edit_calendar', 'emoji_events',
    'event', 'event_available', 'fact_check', 'feedback', 'flag', 'groups', 'help', 'home', 'how_to_reg',
    'inventory_2', 'live_tv', 'manage_accounts', 'menu_book', 'monitor_heart', 'notifications', 'payments',
    'rate_review', 'schema', 'settings', 'sports_esports', 'stadium', 'sync_alt', 'timer', 'tune',
    'tv', 'view_timeline', 'volunteer_activism',
})


def bottom_nav_tab(tab: dict) -> ui.tab:
    """One bottom-bar tab, with its count floating on the icon when it declares one."""
    with ui.tab(tab['label'], icon=nav_icon(tab.get('icon', 'circle'))) as nav_tab:
        if 'badge' in tab:
            nav_count_badge(tab['label'], tab['badge']).props('floating')
    return nav_tab


def set_nav_count(label: str, count: int) -> None:
    """Update every badge on the nav item ``label`` for the current client."""
    try:
        client = context.client
    except RuntimeError:
        return
    for badge in _badges.get(client, {}).get(label, []):
        badge.set_text(str(count))
        badge.set_visibility(count > 0)
