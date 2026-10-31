"""A script the test suite imports must not push the dev ``.env`` into os.environ.

``scripts/seed_dev.py`` is imported by several tests (they call ``seed_all``
against the in-memory DB). It used to call ``load_dotenv()`` at module import,
which loaded the developer's ``.env`` — including ``MOCK_DISCORD=true`` and
``MOCK_SEEDGEN=true`` — into ``os.environ`` for the whole pytest worker process.

Under ``--dist loadfile`` that poisons every *later* file on the same worker:
``test_async_qualifier_service.py``'s "rolling without a DK64 key must raise"
stopped raising, because mock seedgen skips the credential check. It presented
as an intermittent failure whose identity depended on which worker happened to
pick up a seeding test first.

The flag any test asserts on must come from that test, not from whoever imported
a script earlier.
"""

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / 'scripts'

# Scripts the test suite imports, directly or transitively: these must stay import-pure.
IMPORTED_BY_TESTS = [
    'seed_dev.py', 'seed_fledgling.py', 'backfill_memberships.py', 'import_sgl_tournaments.py',
]


def _module_level_calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


@pytest.mark.parametrize('name', IMPORTED_BY_TESTS)
def test_a_test_imported_script_does_not_load_dotenv_at_import(name):
    path = SCRIPTS / name
    assert path.exists(), f'{name} moved — update IMPORTED_BY_TESTS'
    assert 'load_dotenv' not in _module_level_calls(path), (
        f'scripts/{name} calls load_dotenv() at import time. The suite imports '
        f'it, so this leaks the dev .env into os.environ for the whole worker '
        f'and silently puts later tests in mock mode. Move the call into the '
        f"script's entry point."
    )


def test_importing_the_seed_script_leaves_the_mock_flags_alone():
    """The end-to-end version of the rule, in the terms that actually broke."""
    import os

    before = {k: os.environ.get(k) for k in ('MOCK_SEEDGEN', 'MOCK_DISCORD')}
    import scripts.seed_dev
    import scripts.seed_fledgling  # noqa: F401

    after = {k: os.environ.get(k) for k in ('MOCK_SEEDGEN', 'MOCK_DISCORD')}
    assert after == before
