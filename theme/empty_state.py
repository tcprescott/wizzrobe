import html

from nicegui import ui

from application.utils.easter_eggs import current_theme, random_fact


def fun_fact() -> None:
    """Render a fact from today's theme, set apart from whatever it sits under."""
    with ui.column().classes('empty-state-fact items-center') as aside:
        aside.props('role=note')
        ui.icon(current_theme().icon).props('size=xs aria-hidden=true').classes('empty-state-fact__icon')
        ui.label(random_fact()).classes('empty-state-fact__text')


def empty_state(message: str) -> None:
    """Render a centered empty-state message with a fun fact beneath it."""
    with ui.column().classes('empty-state items-center'):
        ui.label(message)
        fun_fact()


def empty_panel(message: str, icon: str = 'inbox') -> None:
    """Render a list with nothing in it yet: a dashed panel the width of the list,
    holding an icon, the message and a fun fact."""
    with ui.column().classes('wiz-empty-panel items-center'):
        ui.icon(icon, size='md').classes('wiz-empty-panel__icon')
        ui.label(message)
        fun_fact()


def no_data_slot(message: str = 'Nothing here yet.', icon: str = 'inbox', *,
                 from_table: bool = False) -> str:
    """Vue template for a Quasar table ``no-data`` slot: the branded empty state
    (icon + message + a fun fact) instead of Quasar's bare "No data available".

    The fact is interpolated once at build time. ``message`` is HTML-escaped
    so a caller-supplied string can't inject markup. ``from_table`` shows the
    table's own label instead (Quasar's ``message``, from ``no-results-label``
    while a filter is set), for a board whose empty reason changes with it.
    """
    safe_message = html.escape(message)
    safe_fact = html.escape(random_fact())
    safe_fact_icon = html.escape(current_theme().icon)
    safe_icon = html.escape(icon)
    return (
        '<div class="full-width row flex-center empty-state q-py-lg">'
        '<div class="column items-center text-center">'
        f'<q-icon name="{safe_icon}" size="md" class="q-mb-sm wiz-empty-icon"></q-icon>'
        + ('<div>{{ props.message }}</div>' if from_table else f'<div>{safe_message}</div>') +
        '<div class="empty-state-fact column items-center" role="note">'
        f'<q-icon name="{safe_fact_icon}" size="xs" aria-hidden="true" class="empty-state-fact__icon"></q-icon>'
        f'<div class="empty-state-fact__text">{safe_fact}</div>'
        '</div></div></div>'
    )
