"""``steady_refreshable`` keeps a section on screen until its new content is ready.

``ui.refreshable`` clears, then awaits the builder, so the browser sees an empty
section for the length of the query and the page below it jumps. These run the
real NiceGUI element tree (no browser) and look at it mid-refresh.

The hook half checks that no presentation module brings the old shape back.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
from nicegui import Client, background_tasks, core, ui
from nicegui.page import page

from theme.steady import steady_refreshable

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / '.claude' / 'scripts' / 'check_steady_refresh.py'


def _labels(column) -> list:
    return [e.text for e in column.descendants() if isinstance(e, ui.label)]


async def _board(gates: dict):
    core.loop = asyncio.get_running_loop()
    client = Client(page('/'), request=None)
    version = {'n': 1}
    with client:
        with ui.column() as column:
            ui.label('above')

            @steady_refreshable
            async def section() -> None:
                n = version['n']
                if n in gates:
                    await gates[n].wait()
                ui.label(f'v{n}')

            await section()
            ui.label('below')
    return column, section, version


def _start_refresh(section):
    async def run() -> None:
        await section.refresh()
    return background_tasks.create(run())


async def test_old_content_stays_until_the_new_is_built():
    gates = {2: asyncio.Event()}
    column, section, version = await _board(gates)
    version['n'] = 2
    task = _start_refresh(section)
    await asyncio.sleep(0.02)
    assert _labels(column) == ['above', 'v1', 'below']
    gates[2].set()
    await task
    assert _labels(column) == ['above', 'v2', 'below']


async def test_overlapping_refreshes_keep_only_the_newest():
    gates = {2: asyncio.Event(), 3: asyncio.Event()}
    column, section, version = await _board(gates)
    version['n'] = 2
    first = _start_refresh(section)
    await asyncio.sleep(0.01)
    version['n'] = 3
    second = _start_refresh(section)
    await asyncio.sleep(0.01)
    gates[3].set()
    await second
    gates[2].set()
    await first
    assert _labels(column) == ['above', 'v3', 'below']


async def test_a_second_refresh_after_a_swap_still_lands_in_place():
    column, section, version = await _board({})
    for n in (2, 3):
        version['n'] = n
        await section.refresh()
    assert _labels(column) == ['above', 'v3', 'below']


@pytest.fixture(scope='module')
def hook():
    sys.path.insert(0, str(HOOK.parent))
    try:
        spec = importlib.util.spec_from_file_location('check_steady_refresh', HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(HOOK.parent))


def test_hook_flags_an_async_ui_refreshable(hook):
    src = 'from nicegui import ui\n\n@ui.refreshable\nasync def rows():\n    pass\n'
    assert hook.check(src, 'pages/x.py')


def test_hook_allows_sync_and_steady_and_exempt(hook):
    src = (
        'from nicegui import ui\n'
        '@ui.refreshable\ndef a():\n    pass\n'
        '@steady_refreshable\nasync def b():\n    pass\n'
        '@ui.refreshable  # steady-refresh: exempt - built once\nasync def c():\n    pass\n'
    )
    assert hook.check(src, 'pages/x.py') == []


def test_no_presentation_module_has_an_async_ui_refreshable(hook):
    hits = []
    for root in ('pages', 'theme'):
        for path in (REPO / root).rglob('*.py'):
            hits += hook.check(path.read_text(encoding='utf-8'), str(path.relative_to(REPO)))
    assert hits == []
