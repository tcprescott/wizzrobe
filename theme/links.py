"""Buttons and menu items that open another page or file as a real ``<a href>``.

``ui.navigate.to(url, new_tab=True)`` in a click handler is the trap these
replace: the tap travels to the server and back before the browser is told to
call ``window.open``, and by then Safari no longer treats it as the reader's
tap. iPhone Safari blocks it as a pop-up unless the round trip is quick, and a
web app added to the Home Screen never opens it at all. A link the browser
follows itself works on the first tap everywhere.
"""

from typing import Optional
from urllib.parse import quote

from nicegui import context, ui


def app_href(path: str) -> str:
    """``path`` as the browser must request it from this page.

    A root-relative path gets the page's prefix (``/t/<slug>`` plus any proxy
    prefix), the same one ``ui.link`` and ``ui.navigate.to`` add client-side;
    anything else is returned unchanged.
    """
    if not path.startswith('/') or path.startswith('//'):
        return path
    request = context.client.request
    if request is None:
        return path
    forwarded = quote(request.headers.get('X-Forwarded-Prefix', ''), safe="/:@!$&'()*+,;=%")
    return f"{forwarded}{request.scope.get('root_path', '')}{path}"


def set_link(element: ui.element, href: str, *, new_tab: bool = True) -> None:
    """Make a Quasar button or item render as a link to ``href``."""
    element.props['href'] = app_href(href)
    if new_tab:
        element.props['target'] = '_blank'
        element.props['rel'] = 'noopener'


def link_button(label: str, href: str, *, icon: Optional[str] = None, new_tab: bool = True) -> ui.button:
    """A button that is a link: ``href`` opens straight from the tap, on any phone."""
    button = ui.button(label, icon=icon)
    set_link(button, href, new_tab=new_tab)
    return button
