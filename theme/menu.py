from dataclasses import dataclass
from html import escape
from typing import Optional, Sequence, Union

from nicegui import ui

from theme.links import set_link


def menu_action(icon: str, label: str, on_click, negative: bool = False) -> ui.menu_item:
    """One icon-and-label row inside a ``ui.menu()``; ``negative`` for a destructive one."""
    with ui.menu_item(on_click=on_click).classes('text-negative' if negative else '') as item:
        with ui.item_section().props('avatar'):
            ui.icon(icon)
        ui.item_section(label)
    return item


def menu_link(icon: str, label: str, href: str, *, new_tab: bool = True) -> ui.menu_item:
    """A ``menu_action`` row that is a link, so ``href`` opens straight from the tap.

    Use it instead of ``menu_action(..., lambda: ui.navigate.to(url, new_tab=True))``,
    which iPhone Safari blocks as a pop-up (see ``theme.links``).
    """
    with ui.menu_item() as item:
        with ui.item_section().props('avatar'):
            ui.icon(icon)
        ui.item_section(label)
    set_link(item, href, new_tab=new_tab)
    return item


def more_menu(name: str) -> ui.menu:
    """A row's ⋮ button named "More for <name>"; enter the returned menu to add items."""
    button = ui.button(icon='more_vert', color=None).props('flat round dense').classes('text-grey-7')
    button.props['aria-label'] = f'More for {name}'
    with button:
        menu = ui.menu().props('anchor="bottom right" self="top right"')
    return menu


@dataclass(frozen=True)
class SlotItem:
    """One item of a slot-template ⋮ menu: emits ``event`` with the row."""
    icon: str
    label: str
    event: str
    negative: bool = False
    v_if: Optional[str] = None
    # A row expression; when it's truthy its text shows as a chip at the item's end.
    badge: Optional[str] = None
    # A row expression holding a URL: the item is then a link that opens it in a
    # new tab, and ``event`` is not emitted. Opening from a server round trip is
    # blocked as a pop-up on iPhone (see ``theme.links``).
    href: Optional[str] = None


@dataclass(frozen=True)
class SlotHeader:
    """A small heading that starts a group of ⋮ items, under a separator unless it's first.

    ``label`` may hold ``{{ }}`` row expressions; ``v_if`` should be true whenever
    any item of its group shows, so a heading never sits over nothing.
    """
    label: str
    v_if: Optional[str] = None


def slot_more_menu(name_expr: str, items: Sequence[Union[SlotItem, SlotHeader]], *,
                   extra_class: str = '') -> str:
    """A row's ⋮ menu for a ``ui.table`` slot, named ``'More for ' + <name_expr>``.

    Destructive items go last under one separator, so pass them last; the
    separator shows whenever any of them does.
    """
    def item_html(it: Union[SlotItem, SlotHeader], first: bool) -> str:
        cond = f' v-if="{escape(it.v_if)}"' if it.v_if else ''
        if isinstance(it, SlotHeader):
            sep = '' if first else f'<q-separator{cond}/>'
            return f'{sep}<q-item-label header{cond} class="wiz-menu-header">{escape(it.label)}</q-item-label>'
        cls = ' class="text-negative"' if it.negative else ''
        badge = (
            f'<q-item-section side v-if="{escape(it.badge)}">'
            f'<span class="wiz-chip wiz-chip--pending">{{{{ {escape(it.badge)} }}}}</span></q-item-section>'
            if it.badge else ''
        )
        action = (
            f' tag="a" :href="{escape(it.href)}" target="_blank" rel="noopener noreferrer"' if it.href
            else f' @click="$parent.$emit(\'{it.event}\', props.row)"'
        )
        return (
            f'<q-item{cond} clickable{cls}{action}>'
            f'<q-item-section avatar><q-icon name="{it.icon}"/></q-item-section>'
            f'<q-item-section>{escape(it.label)}</q-item-section>{badge}</q-item>'
        )

    plain = [it for it in items if isinstance(it, SlotHeader) or not it.negative]
    negative = [it for it in items if isinstance(it, SlotItem) and it.negative]
    body = ''.join(item_html(it, i == 0) for i, it in enumerate(plain))
    if negative:
        conds = [it.v_if for it in negative]
        sep_if = '' if not plain or None in conds else ' v-if="' + escape(' || '.join(f'({c})' for c in conds)) + '"'
        body += (f'<q-separator{sep_if}/>' if plain else '') + ''.join(item_html(it, False) for it in negative)
    return (
        f'<q-btn flat round dense icon="more_vert" color="grey-7" class="{extra_class}"\n'
        f'       :aria-label="\'More for \' + {name_expr}">'
        '<q-menu auto-close anchor="bottom right" self="top right">'
        f'<q-list style="min-width: 200px">{body}</q-list></q-menu></q-btn>'
    )


def slot_row_button(label: str, icon: str, event: str, *, color: str = 'primary',
                    v_if: Optional[str] = None, extra_class: str = '') -> str:
    """The one labelled button a ``ui.table`` row shows beside its ⋮."""
    cond = f' v-if="{escape(v_if)}"' if v_if else ''
    return (
        f'<q-btn{cond} flat dense no-caps no-wrap color="{color}" icon="{icon}" label="{escape(label)}"'
        f' class="{extra_class}" @click="$parent.$emit(\'{event}\', props.row)" />'
    )


def slot_name_link(field: str, event: str) -> str:
    """A row's name as a link that opens it (emits ``event`` with the row)."""
    return (
        f'<a href="#" class="table-link" @click.prevent="$parent.$emit(\'{event}\', props.row)">'
        f'{{{{ props.row.{field} }}}}</a>'
    )
