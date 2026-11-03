"""Guards against re-introducing a per-test rebuild of an expensive fixture.

The suite's wall time is dominated by fixture *setup*, not by assertions — test
*count* is close to free by comparison. See docs/development.md > "Keeping it
fast" for the numbers.

Five things are therefore built once per process and shared, each of them once
the most expensive item in the run:

* ``build_api_app()`` (``tests/api_helpers.py``) — mounting the API router costs
  ~200ms, because ``include_router`` resolves each route's dependency graph and
  builds a Pydantic response model per endpoint. Exposed as the ``app`` fixture
  in ``tests/conftest.py``.
* ``_schema_sql()`` (``tests/conftest.py``) — the CREATE TABLE script, rendered
  once and replayed instead of re-derived from model metadata.
* **The engine** (``_bootstrap_engine()``) — ``Tortoise.init()`` spends most of
  its ~15ms in ``_build_initial_querysets()``, rebuilding a filter map for all
  ~70 models. Per test that was 60s of a 190s profiled run. Now once per worker,
  with each test restoring a pristine **template** database instead.
* **The MCP catalogue** (``tests/mcp/conftest.py``) — ``build_server()`` derives
  a JSON schema per tool and costs ~230ms; ~106 sessions rebuilt it.
* **The dev seed** (the ``seeded_db`` fixture) — ``seed_all()`` costs ~1.5s and
  eight tests want its result, not its execution.

The checks here are **structural, not timing-based** — a wall-clock budget is
flaky on shared CI runners, and a flaky guard gets deleted. Two kinds: identity
assertions that the caches are still in place, and a single AST pass over
``tests/`` for the shapes that were removed.

``.claude/scripts/check_fixture_cost.py`` blocks the same shapes at write time,
but only for edits Claude Code makes; this module is the layer that binds an IDE
edit or an externally-authored PR, so it is the one that holds the line.
"""

import ast
import functools
import pathlib

import pytest

TESTS_DIR = pathlib.Path(__file__).resolve().parent

APP_BUILDER_FILES = {'api_helpers.py', 'conftest.py'}

ENGINE_INIT_FILES = {'conftest.py'}
MCP_BUILDER_FILES = {'mcp/conftest.py'}
SEED_CALLER_FILES = {'conftest.py', 'test_seed_coverage.py'}

USE_SHARED_APP = (
    'Use the shared `app` fixture in tests/conftest.py (it returns the cached '
    'build_api_app() from tests/api_helpers.py). A throwaway bare FastAPI() for '
    'middleware/error-handler coverage is fine — mounting the API router on it is not.'
)


@functools.cache
def _parsed_test_modules() -> tuple[tuple[pathlib.Path, ast.Module], ...]:
    """Every module under tests/, parsed once and shared by the checks below."""
    return tuple(
        (path, ast.parse(path.read_text(encoding='utf-8'), str(path)))
        for path in sorted(TESTS_DIR.rglob('*.py'))
    )


def _rel(path: pathlib.Path) -> str:
    return str(path.relative_to(TESTS_DIR.parent))


def _is_fixture(node: ast.AST) -> bool:
    """True for a function decorated with pytest.fixture (bare or called)."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == 'fixture':
            return True
        if isinstance(target, ast.Name) and target.id == 'fixture':
            return True
    return False


def _mounts_api_router(call: ast.Call) -> bool:
    """True for ``some_app.include_router(api.router, ...)``."""
    if not isinstance(call.func, ast.Attribute) or call.func.attr != 'include_router':
        return False
    if not call.args:
        return False
    first = call.args[0]
    if isinstance(first, ast.Attribute) and first.attr == 'router':
        return True
    return isinstance(first, ast.Name) and first.id == 'router'


def _is_bare_app_alias(node: ast.AST) -> bool:
    """True for a fixture whose entire body is ``return build_api_app()``.

    Narrow on purpose: a fixture that *assembles* a context (tokens, tenants,
    rows) and happens to include the cached app in its return value is
    legitimate and costs nothing — only the trivial alias is flagged, which is
    the shape a re-pasted local ``app`` fixture takes once it is renamed.
    """
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    value = body[0].value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == 'build_api_app'
    )


def _called_name(call: ast.Call) -> str:
    """``f()`` -> 'f'; ``a.b.f()`` -> 'a.b.f' (best effort, dotted tail only)."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        prefix = func.value.id if isinstance(func.value, ast.Name) else ''
        return f'{prefix}.{func.attr}' if prefix else func.attr
    return ''


@functools.cache
def _scan() -> dict:
    """One walk over the parsed tree, collecting every location the checks need.

    Kept to a single pass so this module stays cheap — the whole point of the
    guard is that the suite is fast.
    """
    found = {
        'app_fixtures': [], 'router_mounts': [], 'schema_calls': [], 'app_aliases': [],
        'engine_inits': [], 'mcp_builds': [], 'seed_calls': [],
    }
    for path, tree in _parsed_test_modules():
        is_builder = path.name in APP_BUILDER_FILES
        where = _rel(path)
        under_tests = path.relative_to(TESTS_DIR).as_posix()
        for node in ast.walk(tree):
            if _is_fixture(node) and not is_builder:
                if node.name == 'app':
                    found['app_fixtures'].append(f'{where}:{node.lineno}')
                elif _is_bare_app_alias(node):
                    found['app_aliases'].append(f'{where}:{node.lineno} ({node.name})')
            elif isinstance(node, ast.Call):
                if not is_builder and _mounts_api_router(node):
                    found['router_mounts'].append(f'{where}:{node.lineno}')
                if path.name != 'conftest.py' and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == 'generate_schemas':
                    found['schema_calls'].append(f'{where}:{node.lineno}')
                name = _called_name(node)
                if name == 'Tortoise.init' and under_tests not in ENGINE_INIT_FILES:
                    found['engine_inits'].append(f'{where}:{node.lineno}')
                elif name == 'build_server' and under_tests not in MCP_BUILDER_FILES:
                    found['mcp_builds'].append(f'{where}:{node.lineno}')
                elif name == 'seed_all' and under_tests not in SEED_CALLER_FILES:
                    found['seed_calls'].append(f'{where}:{node.lineno}')
    return {key: tuple(value) for key, value in found.items()}


class TestCachesAreIntact:
    """Identity implies the cache survived — no timing involved."""

    def test_build_api_app_is_cached(self):
        from tests.api_helpers import build_api_app

        assert hasattr(build_api_app, 'cache_info'), (
            'build_api_app() lost its @functools.cache (tests/api_helpers.py). '
            'Rebuilding the API app per test cost more than every DB query in the '
            'suite combined — 135s of the old 207s run.'
        )
        assert build_api_app() is build_api_app(), (
            'build_api_app() returned two different app objects, so it is being '
            'rebuilt per call. Restore the @functools.cache in tests/api_helpers.py.'
        )

    async def test_schema_sql_is_cached(self, db):
        from tests.conftest import ON_POSTGRES, _schema_sql

        assert hasattr(_schema_sql, 'cache_info'), (
            '_schema_sql() lost its @functools.cache (tests/conftest.py). The '
            'CREATE TABLE script is a pure function of the models, so re-deriving '
            'it on each of the ~1450 DB-backed setups is wasted work.'
        )
        # Keyed by dialect: the suite can run against PostgreSQL, whose DDL differs.
        dialect = 'postgres' if ON_POSTGRES else 'sqlite'
        assert _schema_sql(dialect) is _schema_sql(dialect), (
            '_schema_sql() re-rendered the schema instead of returning the cached '
            'script. Restore the @functools.cache in tests/conftest.py.'
        )

    async def test_the_engine_is_built_once_and_the_database_is_a_template(self, db):
        """``Tortoise.init()`` is a per-process cost, not a per-test one."""
        from tortoise import connections

        from tests import conftest

        assert conftest._engine_ready, (
            'the `db` fixture is initialising Tortoise per test again. Most of '
            'init() is _build_initial_querysets() rebuilding a filter map for all '
            '~70 models — 60s of a 190s profiled run when it ran per test.'
        )
        if conftest.ON_POSTGRES:
            return
        assert connections.get('default')._connection is conftest._shared_sqlite, (
            'this test is talking to its own SQLite connection rather than the '
            'shared one, so it opened a second, empty in-memory database.'
        )
        assert conftest.BASELINE_TEMPLATE in conftest._templates, (
            'the baseline template is gone, so each test is rebuilding the schema '
            'instead of restoring a snapshot of it.'
        )

    def test_the_mcp_catalogue_is_cached(self):
        """The MCP tool catalogue is immutable; only its transport is single-use."""
        import mcpserver
        from tests.mcp import conftest as mcp_conftest

        assert mcpserver.build_server is mcp_conftest._cached_build_server, (
            'mcpserver.mount() is building a fresh MCP server per session again. '
            'Deriving a JSON schema for all 77 tools costs ~230ms, and the ~106 '
            'sessions in the suite paid it every time (~24s a run).'
        )
        assert hasattr(mcp_conftest._catalogue, 'cache_info'), (
            '_catalogue() lost its @functools.cache (tests/mcp/conftest.py).'
        )
        first = mcp_conftest._cached_build_server()
        spent = first.session_manager
        second = mcp_conftest._cached_build_server()
        assert first is second, 'the cached MCP server is being rebuilt per call'
        assert second.session_manager is not spent, (
            'the cached server was handed back with the previous session manager, '
            'whose run() can only be entered once; start_transport() has to mint '
            'a new one per session.'
        )


class TestNoPerTestRebuilds:
    """AST scan of tests/ for the shapes commit f0ceb4b removed."""

    def test_only_conftest_defines_an_app_fixture(self):
        offenders = _scan()['app_fixtures']
        assert not offenders, (
            'A local `app` fixture shadows the shared one: ' + ', '.join(offenders)
            + '. Two modules re-pasted this fixture once already and bypassed the '
              'cache entirely. ' + USE_SHARED_APP
        )

    def test_no_test_mounts_the_api_router(self):
        offenders = _scan()['router_mounts']
        assert not offenders, (
            'A test mounts the API router on its own app: ' + ', '.join(offenders)
            + '. That costs ~200ms per test and the result is immutable. '
            + USE_SHARED_APP
        )

    def test_no_test_regenerates_the_schema(self):
        offenders = _scan()['schema_calls']
        assert not offenders, (
            'A test re-derives the schema from model metadata: ' + ', '.join(offenders)
            + '. Depend on the `db` fixture in tests/conftest.py — it replays the '
              'script rendered once by _schema_sql().'
        )

    def test_no_fixture_is_a_bare_alias_for_the_cached_app(self):
        offenders = _scan()['app_aliases']
        assert not offenders, (
            'A fixture is a bare alias for the cached app: ' + ', '.join(offenders)
            + '. That is the shared `app` fixture from tests/conftest.py under a '
              'different name — request `app` instead.'
        )

    def test_no_test_initialises_tortoise(self):
        offenders = _scan()['engine_inits']
        assert not offenders, (
            'A test calls Tortoise.init(): ' + ', '.join(offenders)
            + '. Most of init() is rebuilding a filter map for every model, which '
              'is why the `db` fixture in tests/conftest.py does it once per worker '
              'and restores a template database per test. Depend on `db`.'
        )

    def test_no_test_builds_its_own_mcp_server(self):
        offenders = _scan()['mcp_builds']
        assert not offenders, (
            'A test builds its own MCP server: ' + ', '.join(offenders)
            + '. Registering the 77 tools costs ~230ms and the catalogue is '
              'immutable. Use mcp_session() from tests/mcp/conftest.py, which '
              'mounts the cached server with a fresh transport.'
        )

    def test_no_test_runs_the_dev_seed_inline(self):
        offenders = _scan()['seed_calls']
        assert not offenders, (
            'A test calls seed_all() directly: ' + ', '.join(offenders)
            + '. It costs ~1.5s. Request the `seeded_db` fixture from '
              'tests/conftest.py, which snapshots the seeded database once per '
              'worker and restores it after that.'
        )


def test_ast_scan_actually_sees_the_test_tree():
    """A broken scan would make every check above pass vacuously."""
    parsed = _parsed_test_modules()
    assert len(parsed) > 50, f'only {len(parsed)} modules parsed under {TESTS_DIR}'
    assert any(path.name == 'conftest.py' for path, _ in parsed)


def test_the_ast_scan_can_still_see_the_shapes_it_forbids():
    """Every rule above passes vacuously if the matcher stopped matching."""
    tree = ast.parse(
        'Tortoise.init(db_url=x)\nbuild_server()\nawait seed_all()\n'
    )
    names = {_called_name(n) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert {'Tortoise.init', 'build_server', 'seed_all'} <= names, names


@pytest.mark.parametrize('name', ['app', 'db', 'seeded_db'])
def test_shared_fixtures_still_live_in_conftest(name):
    """The failure messages above steer people here; fail loudly if one is renamed."""
    conftest = next(
        tree for path, tree in _parsed_test_modules()
        if path == TESTS_DIR / 'conftest.py'
    )
    assert any(
        _is_fixture(node) and node.name == name for node in ast.walk(conftest)
    ), f'tests/conftest.py no longer defines a `{name}` fixture'
