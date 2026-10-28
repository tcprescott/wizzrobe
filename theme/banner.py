"""The shared banner: a full-width message about the page or the viewer.

One shape for every "heads up" strip — an incomplete profile, a community that
is not set up yet, a read-only view — so they stop being hand-built from
coloured rows and labels. The look is ``.wiz-banner`` in styles.css.
"""

from contextlib import contextmanager
from typing import Iterator, Literal, Optional

from nicegui import ui

BannerTone = Literal['info', 'ok', 'live', 'warning', 'danger']

_DEFAULT_ICONS: dict[str, str] = {
    'info': 'info',
    'ok': 'check_circle',
    'live': 'place',
    'warning': 'warning',
    'danger': 'error',
}


@contextmanager
def banner(
    message: str,
    *,
    title: Optional[str] = None,
    tone: BannerTone = 'info',
    icon: Optional[str] = None,
    footnote: Optional[str] = None,
) -> Iterator[ui.row]:
    """Render a banner; anything built inside the ``with`` lands in its action slot.

    ``role="alert"`` goes on ``danger`` only: a screen reader interrupts for
    it, which is right for "this is blocking you" and wrong for a routine note.
    """
    with ui.element('div').classes(f'wiz-banner wiz-banner--{tone}') as root:
        if tone == 'danger':
            root.props('role=alert')
        ui.icon(icon or _DEFAULT_ICONS[tone]).classes('wiz-banner__icon')
        with ui.column().classes('wiz-banner__body gap-0'):
            if title:
                ui.label(title).classes('wiz-banner__title')
            ui.label(message)
            if footnote:
                ui.label(footnote).classes('wiz-banner__footnote')
        with ui.row().classes('wiz-banner__actions') as actions:
            yield actions
