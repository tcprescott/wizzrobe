"""How long a page may take to build before NiceGUI gives up on it.

NiceGUI's default is 3 seconds, after which it cancels the build and serves a
bare "The page took longer than the response_timeout…" page that never retries.
A restart trips it every time: every open tab (about 25 on SG Live morning)
reloads within a few seconds of the server coming back, which is past the ~10
pages a second one event loop can build (docs/scaling-roadmap.md), so the later
ones queue. They needed 4 to 5 seconds and were thrown away at 3, and the
work spent on them was wasted on top.

NiceGUI only takes the timeout per page (``@ui.page(response_timeout=...)``),
with no app-wide setting, so this raises the keyword default on ``ui.page``
itself. That covers every page, including the ones registered with a bare
``ui.page`` and NiceGUI's own error-page clients. A page can still pass its own.
"""

from nicegui import ui

PAGE_BUILD_TIMEOUT_SECONDS = 30.0


def install() -> None:
    """Set the default ``response_timeout`` for every ``ui.page`` built after this. Idempotent."""
    defaults = ui.page.__init__.__kwdefaults__
    # tests/test_page_build_timeout.py fails if a NiceGUI upgrade moves this.
    if defaults is not None and 'response_timeout' in defaults:
        defaults['response_timeout'] = PAGE_BUILD_TIMEOUT_SECONDS
