"""Generic mobile grid (card) support for admin / report ``ui.table``s.

Below Quasar's ``lt.md`` breakpoint a wide HTML table overflows a phone: columns
and every row-action button slide off the right edge behind a horizontal scroll.
The app's fix is to flip the table into Quasar *grid* mode
(``:grid="Quasar.Screen.lt.md"``) and register an ``item`` slot that stacks each
row as a card. The four purpose-built family tables (match, user, tournament,
equipment) ship bespoke cards (see ``match_grid.py``); **every other**
admin/report/player table should get its responsive card from this one helper so
the pattern stays uniform — and so the ``check_table_grid`` guardrail can prove a
new table has one.

The same ``columns`` list also decides what the *desktop* table paints:
``apply_column_visibility`` turns this app's ``{'hidden': True}`` convention into
Quasar's ``visible-columns`` prop, which is the only thing QTable actually reads.
``enable_mobile_grid`` applies it for you; a bespoke table that wires its own
``:grid`` prop calls it directly.

``enable_mobile_grid`` is column-driven (the same technique
``match_grid.render_grid_slot`` uses): it walks the ``columns`` list the desktop
table already uses, skips ``hidden`` and the ``actions`` column, and emits a
``.wiz-grid-card`` with a ``label / value`` row per remaining column plus an
optional right-aligned actions footer. A field that renders as a badge / chip /
icon on the desktop (via a ``body-cell-<field>`` slot) can pass a matching card
snippet through ``field_slots`` — Vue that reads ``props.row.<field>`` (note:
``props.row``, not the ``props.value`` the desktop cell slot sees). Anything
omitted falls back to the plain ``{{ props.row.<field> }}`` value.

Presentation-only: builds NiceGUI/Vue slot strings, no business logic.
"""

import html
from typing import AbstractSet, Mapping, Optional, Sequence

from nicegui import ui

__all__ = ['MOBILE_GRID_BREAKPOINT', 'apply_column_visibility', 'enable_mobile_grid',
           'visible_column_names']

# Quasar Screen breakpoint below which the table switches to card/grid mode.
# ``lt.md`` (<1024px) is the app default; the match table lets callers widen it
# to ``lt.lg`` on its densest pages.
MOBILE_GRID_BREAKPOINT = 'lt.md'


def visible_column_names(columns: Sequence[Mapping]) -> list[str]:
    """The column names a ``hidden``-aware table should actually paint."""
    return [c.get('name', '') for c in columns if not c.get('hidden')]


def apply_column_visibility(table: ui.table, columns: Sequence[Mapping]) -> ui.table:
    """Make ``{'hidden': True}`` real on the desktop table, not just on the card.

    ``hidden`` is this app's own convention, and for a long time only half of it
    worked: the card body above skips a hidden column, while Quasar's ``QTable``
    has no such column property at all and painted it regardless. Six admin
    tables carried an id column marked hidden purely so the row dict would keep
    the primary key for the row-action handlers — and every one of them printed
    that key as the first column on desktop while correctly hiding it on a
    phone.

    Quasar's actual mechanism is the ``visible-columns`` prop, so that is what
    this sets. The row dicts are untouched, which is the point: ``props.row.id``
    stays available to every slot template and ``row_key='id'`` keeps working.
    """
    table._props['visible-columns'] = visible_column_names(columns)
    table.update()
    return table


def _value_html(name: str, field: str, field_slots: Mapping[str, str]) -> str:
    """The card cell body for one column: a caller override or the raw value.

    ``field_slots`` may be keyed by column ``name`` or by ``field`` (they usually
    match); ``name`` wins when both are present.
    """
    if name in field_slots:
        return field_slots[name]
    if field in field_slots:
        return field_slots[field]
    return '{{ props.row.' + field + ' }}'


def enable_mobile_grid(
    table: ui.table,
    columns: Sequence[Mapping],
    *,
    actions: str = '',
    field_slots: Optional[Mapping[str, str]] = None,
    row_click_event: Optional[str] = None,
    breakpoint: str = MOBILE_GRID_BREAKPOINT,
    table_key: Optional[str] = None,
    required: Optional[AbstractSet[str]] = None,
    wrap: bool = False,
    selectable: bool = False,
    pager: bool = True,
) -> ui.table:
    """Make ``table`` render as stacked cards below ``breakpoint``.

    Adds the ``:grid`` prop and an ``item`` slot generated from ``columns``.
    Returns ``table`` so the call can chain.

    With ``table_key`` it *also* applies the viewer's saved desktop layout, and
    the ordering that makes that safe is owned here rather than left to the
    caller: the card slot is generated from ``columns`` **first**, exactly as it
    always was, and ``customize_table`` runs as the last statement. A table that
    goes through this helper therefore cannot get the order wrong, and the mobile
    card cannot be reached by a column preference.

    Args:
        table: the ``ui.table`` to make responsive.
        columns: the same column dicts passed to the table. ``hidden`` columns
            and the ``actions`` column are skipped in the card body.
        actions: raw inner HTML for the card's actions footer — typically the
            **same** row-action ``q-btn``s used in the ``body-cell-actions``
            slot (they emit ``$parent.$emit('event', props.row)`` and work
            unchanged inside the card). Empty ⇒ no footer.
        field_slots: optional ``{column_name: vue_snippet}`` overrides for
            fields that render as a badge/chip/icon; the snippet reads
            ``props.row.<field>``.
        row_click_event: when a desktop table wires a whole-row drill-down,
            pass its event name so the card is tappable too (emits
            ``$parent.$emit('<event>', props.row)``). Do not combine with
            ``actions`` — a button tap would also bubble the card click.
        breakpoint: Quasar Screen breakpoint (default ``lt.md``).
        table_key: this table's stable preference key (a ``TableKeys`` constant).
            Omit for a table that must not be customizable, and say why with a
            ``# table-prefs: exempt`` comment so the guardrail agrees.
        required: column names this table may never hide, overriding
            ``DEFAULT_REQUIRED`` (the row-action cells).
        wrap: ship "wrap long values" on for this table — for tables carrying a
            prose column that would otherwise run off the right edge. The viewer
            can still turn it off in the preferences modal.
        selectable: put a selection checkbox on each card, for a table built
            with ``selection='multiple'``. QTable's own checkbox column does not
            exist in grid mode, so without it a phone cannot select anything.
        pager: with ``table_key``, whether the footer offers rows per page.
            ``False`` only for a table that pages on the server with its own
            controls.
    """
    field_slots = field_slots or {}
    table.props(f':grid="Quasar.Screen.{breakpoint}"')
    apply_column_visibility(table, columns)

    rows: list[str] = []
    for col in columns:
        name = col.get('name', '')
        if name == 'actions' or col.get('hidden'):
            continue
        field = col.get('field', name)
        if not isinstance(field, str):
            field = name
        label = html.escape(str(col.get('label', name) or ''))
        rows.append(
            '<div class="wiz-grid-field">'
            f'<div class="wiz-grid-field__label text-grey-7 text-caption">{label}</div>'
            '<div class="wiz-grid-field__value">'
            f'{_value_html(name, field, field_slots)}</div>'
            '</div>'
        )

    footer = (
        f'<div class="row items-center justify-end q-gutter-x-sm q-mt-sm">{actions}</div>'
        if actions.strip() else ''
    )

    if selectable:
        first = str(columns[0].get('field', '')) if columns else ''
        row_name = (
            f":aria-label=\"'Select ' + (props.row[{first!r}] ?? 'row')\""
            if first.isidentifier() else 'aria-label="Select row"'
        )
        rows.insert(0, f'<div class="row justify-end"><q-checkbox dense v-model="props.selected" {row_name} /></div>')
        # QTable's own checkboxes carry no name; a screen reader heard
        # "checkbox, not checked" on every row.
        table.add_slot('header-selection',
                       '<q-checkbox v-model="props.selected" dense aria-label="Select all rows" />')
        table.add_slot('body-selection', f'<q-checkbox v-model="props.selected" dense {row_name} />')

    if row_click_event:
        card_open = (
            '<q-card bordered flat class="q-pa-sm wiz-grid-card cursor-pointer" '
            f'@click="$parent.$emit(\'{row_click_event}\', props.row)">'
        )
    else:
        card_open = '<q-card bordered flat class="q-pa-sm wiz-grid-card">'

    table.add_slot(
        'item',
        '<div class="q-pa-xs col-xs-12 col-sm-6">'
        + card_open
        + '<div class="wiz-grid-fields">' + ''.join(rows) + '</div>' + footer +
        '</q-card></div>',
    )

    # Last statement, deliberately: see the note about ordering above. Imported
    # here rather than at module scope because preferences.py imports the
    # service layer, and this module is imported by every table in the app.
    if table_key:
        from theme.tables.preferences import DEFAULT_REQUIRED, customize_table
        customize_table(table, columns, key=table_key,
                        required=required if required is not None else DEFAULT_REQUIRED,
                        wrap=wrap, pager=pager)
    return table
