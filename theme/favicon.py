"""The browser tab and home-screen icons a page declares.

A community with a logo gets it in both places, as PNGs rendered from the logo
(Safari and iOS ignore SVG and WebP icons) plus the SVG itself for browsers
that draw one. Everything else gets Wizzrobe's own icons. Pages declare them
explicitly rather than relying on NiceGUI's ``/favicon.ico`` link, which the
production reverse proxy answers itself with a 404.
"""

from html import escape
from typing import Mapping, Optional

WIZZROBE_ICON = '/static/icons/icon-192.png'
WIZZROBE_TOUCH_ICON = '/static/icons/apple-touch-icon.png'


def icon_links_html(theme: Optional[Mapping[str, str]] = None) -> str:
    """``<link>`` tags for the tab and home-screen icons of ``theme``'s community."""
    theme = theme or {}
    icon, touch, logo = theme.get('logo_icon'), theme.get('logo_touch'), theme.get('logo') or ''
    if not (icon and touch):
        return (
            f'<link rel="icon" type="image/png" href="{WIZZROBE_ICON}">'
            f'<link rel="apple-touch-icon" href="{WIZZROBE_TOUCH_ICON}">'
        )
    links = f'<link rel="icon" type="image/png" sizes="32x32" href="{escape(icon)}">'
    if logo.endswith('.svg'):
        links += f'<link rel="icon" type="image/svg+xml" sizes="any" href="{escape(logo)}">'
    return links + f'<link rel="apple-touch-icon" href="{escape(touch)}">'
