"""Which side-menu row is the section the viewer is in."""

from nicegui import ui


def nav_section_matches(url: str, path: str) -> bool:
    """Whether the tenant-local ``path`` lies inside the nav section at ``url``.

    Home answers at ``/`` and at its ``/home/<section>`` deep links.
    """
    if url == '/':
        return path == '/' or path == '/home' or path.startswith('/home/')
    return path == url or path.startswith(f'{url}/')


def mark_nav_section(item: ui.item) -> None:
    """Mark a drawer item as the section the viewer is in.

    A class rather than Quasar's ``active`` prop, which already means "this
    page" on the tab list below, so the two read differently.
    """
    item.classes('wiz-nav-section--active').props('aria-current=true')
