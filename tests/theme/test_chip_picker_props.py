"""Chip multi-selects open as a menu on phones, not Quasar's pop-up.

On a phone Quasar shows a select as a pop-up that repeats the whole field above
the options. With a race's twenty-odd players picked in Edit Match, the chips
filled the screen and Save could not be reached. ``CHIP_PROPS`` carries the fix,
so a chip picker built from a bare ``'use-chips'`` string would bring it back.
"""

from __future__ import annotations

import pathlib

from theme.pickers import CHIP_PROPS

ROOT = pathlib.Path(__file__).resolve().parents[2]
PICKERS = ROOT / 'theme' / 'pickers.py'


def test_chip_props_open_as_a_menu():
    assert 'use-chips' in CHIP_PROPS.split()
    assert 'behavior=menu' in CHIP_PROPS.split()


def test_every_chip_picker_uses_chip_props():
    offenders = []
    for path in sorted([*ROOT.joinpath('pages').rglob('*.py'), *ROOT.joinpath('theme').rglob('*.py')]):
        if path == PICKERS:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if 'use-chips' in line:
                offenders.append(f'{path.relative_to(ROOT)}:{lineno}')
    assert not offenders, f'use CHIP_PROPS from theme.pickers instead of a bare use-chips prop: {offenders}'
