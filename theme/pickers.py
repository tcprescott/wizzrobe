"""A type-to-filter multi-select whose options can carry a colour dot and a caption.

Built for pickers with dozens of entries (a Discord server's roles, every grant
an app role mapping can hand out): typing narrows the list, chips show what is
picked, and each option can say something about itself ("already grants Staff")
without that text leaking into the filter match.
"""

from typing import Any, Mapping, Optional

from nicegui import ui

# Props for every chip multi-select. On a phone Quasar opens a select as a
# pop-up that repeats the whole field, chips and all, above the options: with
# twenty players picked the chips filled the screen, the options and the way
# out sat below the keyboard, and the dialog's Save could not be reached. As a
# menu it drops down from the field as it does on desktop.
CHIP_PROPS = 'use-chips behavior=menu'

_OPTION_SLOT = '''
<q-item v-bind="props.itemProps">
    <q-item-section side v-if="props.opt.color !== undefined" class="q-pr-sm">
        <q-icon name="circle" size="xs" :style="{color: props.opt.color}" />
    </q-item-section>
    <q-item-section>
        <q-item-label>{{ props.opt.label }}</q-item-label>
        <q-item-label caption v-if="props.opt.caption">{{ props.opt.caption }}</q-item-label>
    </q-item-section>
    <q-item-section side>
        <q-icon :name="props.selected ? 'check_box' : 'check_box_outline_blank'"
                :color="props.selected ? 'primary' : 'grey'" />
    </q-item-section>
</q-item>
'''

_CHIP_SLOT = '''
<q-chip dense removable :tabindex="props.tabindex" @remove="props.removeAtIndex(props.index)">
    <q-icon v-if="props.opt.color !== undefined" name="circle" size="xs"
            class="q-mr-xs" :style="{color: props.opt.color}" />
    {{ props.opt.label }}
</q-chip>
'''


class FilterMultiSelect(ui.select):
    """``ui.select(multiple=True, with_input=True)`` with per-option colour and caption.

    ``extras`` maps an option *key* to ``{'color': css, 'caption': str}``; either
    may be omitted. NiceGUI regenerates the option dicts on every ``update()``
    and rebuilds the selected values from bare ``{value, label}`` pairs, so both
    hooks are overridden to carry the extras through; patching ``props`` once
    would be wiped by the next selection.
    """

    def __init__(
        self,
        options: Mapping[Any, str],
        *,
        label: str,
        extras: Optional[Mapping[Any, Mapping[str, str]]] = None,
    ) -> None:
        self._extras = dict(extras or {})
        super().__init__(
            options=dict(options), label=label, multiple=True, with_input=True, value=[],
        )
        self.props(CHIP_PROPS).props('stack-label options-dense')
        self.add_slot('option', _OPTION_SLOT)
        self.add_slot('selected-item', _CHIP_SLOT)

    def _decorate(self, option: dict) -> dict:
        extra = self._extras.get(self._values[option['value']])
        return {**option, **extra} if extra else option

    def _update_options(self) -> None:
        super()._update_options()
        self._props['options'] = [self._decorate(o) for o in self._props['options']]

    def _value_to_model_value(self, value: Any) -> Any:
        return [self._decorate(o) for o in super()._value_to_model_value(value)]


_SINGLE_OPTION_SLOT = '''
<q-item v-bind="props.itemProps">
    <q-item-section side v-if="props.opt.icon" class="q-pr-sm">
        <q-icon :name="props.opt.icon" size="sm" class="wiz-select-icon"
                :style="props.opt.color !== undefined ? {color: props.opt.color} : {}" />
    </q-item-section>
    <q-item-section side v-else-if="props.opt.color !== undefined" class="q-pr-sm">
        <q-icon name="circle" size="xs" :style="{color: props.opt.color}" />
    </q-item-section>
    <q-item-section>
        <q-item-label>{{ props.opt.label }}</q-item-label>
        <q-item-label caption v-if="props.opt.caption">{{ props.opt.caption }}</q-item-label>
    </q-item-section>
</q-item>
'''

_SINGLE_SELECTED_SLOT = '''
<span class="row items-center no-wrap">
    <q-icon v-if="props.opt.icon" :name="props.opt.icon" size="sm"
            class="wiz-select-icon q-mr-sm"
            :style="props.opt.color !== undefined ? {color: props.opt.color} : {}" />
    <q-icon v-else-if="props.opt.color !== undefined" name="circle" size="xs"
            class="q-mr-xs" :style="{color: props.opt.color}" />
    <span class="ellipsis">{{ props.opt.label }}</span>
</span>
'''


class ColorSelect(ui.select):
    """A single-choice ``ui.select`` whose options can carry a colour dot or icon, a caption, and be disabled.

    ``extras`` works as on :class:`FilterMultiSelect`, plus ``'disable': True``
    greys an option out (its caption should say why) so it can be read but not
    picked, and ``'icon'`` draws that icon (muted, or in ``'color'``) in place of
    the dot, both in the list and beside the chosen value.
    """

    def __init__(
        self,
        options: Mapping[Any, str],
        *,
        label: str,
        value: Any = None,
        extras: Optional[Mapping[Any, Mapping[str, Any]]] = None,
    ) -> None:
        self._extras = dict(extras or {})
        super().__init__(options=dict(options), label=label, value=value)
        self.props('options-dense option-disable=disable popup-content-class=wiz-color-select-menu')
        self.add_slot('option', _SINGLE_OPTION_SLOT)
        self.add_slot('selected-item', _SINGLE_SELECTED_SLOT)

    def _decorate(self, option: dict) -> dict:
        extra = self._extras.get(self._values[option['value']])
        return {**option, **extra} if extra else option

    def _update_options(self) -> None:
        super()._update_options()
        self._props['options'] = [self._decorate(o) for o in self._props['options']]

    def _value_to_model_value(self, value: Any) -> Any:
        model = super()._value_to_model_value(value)
        return self._decorate(model) if isinstance(model, dict) else model


def bulk_add_summary(new: int, existing: int, *, empty_hint: str) -> str:
    """The "what Add will do" line under a many-to-many add dialog."""
    if not new and not existing:
        return empty_hint
    if not new:
        return 'All of these already exist.' if existing > 1 else 'That mapping already exists.'
    text = f"Adds {new} mapping{'' if new == 1 else 's'}"
    if existing:
        text += f"; {existing} already {'exists' if existing == 1 else 'exist'} and will be skipped"
    return text + '.'


MEMBER_MATCH_LIMIT = 25


def member_matches(members: Mapping[int, str], text: str, keep: Optional[int] = None,
                   limit: int = MEMBER_MATCH_LIMIT) -> dict[int, str]:
    """The first ``limit`` members whose name contains ``text``, plus ``keep`` (the current pick)."""
    needle = (text or '').strip().casefold()
    found: dict[int, str] = {keep: members[keep]} if keep is not None and keep in members else {}
    if len(needle) < 2:
        return found
    for user_id, name in members.items():
        if len(found) > limit:
            break
        if needle in name.casefold():
            found.setdefault(user_id, name)
    return found


def member_select(members: Mapping[int, str], *, value: Optional[int], label: str) -> ui.select:
    """A single-member picker that ships only matches for what's typed, not the whole community.

    A select per row with every member as an option sent 2,000 options per
    row and held the event loop for five to nine seconds on a big community.
    """
    picker = ui.select(options=member_matches(members, '', value), label=label,
                       value=value, with_input=True)
    picker.props('input-debounce=200')
    picker.add_slot('no-option', '''<q-item><q-item-section class="text-grey-7">
        Type at least two letters of a name</q-item-section></q-item>''')

    def search(e) -> None:
        picker.set_options(member_matches(members, e.args, picker.value), value=picker.value)

    picker.on('input-value', search)
    return picker
