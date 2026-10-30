"""The page lists the drawer's section switcher swaps in.

Each hub page (Volunteer, Admin) registers how to list its pages for a viewer,
so the drawer can show another section's pages without loading that section.
Home's four sections are fixed and need no provider.
"""

from collections.abc import Awaitable, Callable

from models import User

SectionMenu = Callable[[User], Awaitable[list[dict]]]

_PROVIDERS: dict[str, SectionMenu] = {}


def register_section_menu(url: str, provider: SectionMenu) -> None:
    """Register the page list for the section at ``url`` (``/volunteer``, ``/admin``)."""
    _PROVIDERS[url] = provider


def section_menu_provider(url: str) -> SectionMenu | None:
    return _PROVIDERS.get(url)


def registered_section_urls() -> set[str]:
    return set(_PROVIDERS)
