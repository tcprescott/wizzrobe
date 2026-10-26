"""Hand-kept lists that must name every component of their kind.

Each list below is maintained by hand beside the components it points at, and a
miss is silent at runtime: a worker the tick script can't drive, a DM whose
buttons never render, MCP tools that simply don't exist. These tests turn a
forgotten entry into a red build. Audit: /mnt/project-files/dry-audit/registries.md.
"""

import ast
import importlib
import pkgutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _worker_tick_table() -> dict[str, str]:
    tree = ast.parse((REPO / 'scripts' / 'run_worker_tick.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == 'WORKERS' for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError('scripts/run_worker_tick.py has no WORKERS table')


def _worker_loop_modules() -> set[str]:
    found = set()
    for path in (REPO / 'application').rglob('*.py'):
        if path.name == 'background_loop.py':
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == 'run_worker_loop'
            ):
                found.add('.'.join(path.relative_to(REPO).with_suffix('').parts))
                break
    return found


def test_tick_script_names_every_worker_loop():
    table = _worker_tick_table()
    loops = _worker_loop_modules()
    assert loops, 'found no run_worker_loop call sites; the scan is broken'
    missing = sorted(loops - set(table.values()))
    assert not missing, f'add these workers to WORKERS in scripts/run_worker_tick.py: {missing}'
    stale = sorted(set(table.values()) - loops)
    assert not stale, f'WORKERS in scripts/run_worker_tick.py names modules with no worker loop: {stale}'


def test_every_dm_view_kind_has_a_factory():
    import discordbot  # noqa: F401  registers the factories and handlers
    from application.services.discord import discord_service

    kinds = {
        value for name, value in vars(discord_service).items()
        if name.startswith('VIEW_') and isinstance(value, str)
    }
    missing = sorted(kinds - set(discord_service._view_factories))
    assert not missing, f'register_view_factory missing in discordbot/__init__.py for: {missing}'


def test_every_button_prefix_has_a_handler():
    import discordbot
    from application.services.discord import discord_service

    prefixes = {}
    for info in pkgutil.iter_modules(discordbot.__path__):
        module = importlib.import_module(f'discordbot.{info.name}')
        for name, value in vars(module).items():
            if name.endswith('_PREFIX') and isinstance(value, str):
                prefixes[f'discordbot.{info.name}.{name}'] = value
    assert prefixes, 'found no button prefixes; the scan is broken'
    missing = sorted(
        where for where, prefix in prefixes.items()
        if prefix not in discord_service._interaction_handlers
    )
    assert not missing, f'register_interaction_handler missing in discordbot/__init__.py for: {missing}'


def test_register_all_calls_every_tool_module(monkeypatch):
    import mcpserver.tools as tools

    called = []
    modules = []
    for info in pkgutil.iter_modules(tools.__path__):
        module = importlib.import_module(f'mcpserver.tools.{info.name}')
        if callable(getattr(module, 'register_tools', None)):
            modules.append(info.name)
            monkeypatch.setattr(
                module, 'register_tools',
                lambda mcp, name=info.name: called.append(name),
            )
    assert modules, 'found no MCP tool modules; the scan is broken'
    tools.register_all(None)
    missing = sorted(set(modules) - set(called))
    assert not missing, f'add these to register_all in mcpserver/tools/__init__.py: {missing}'
