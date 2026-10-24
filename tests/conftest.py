import asyncio
import functools
import os
import sqlite3
from typing import Any, Optional

import pytest
from tortoise import Tortoise, connections
from tortoise.models import Model
from tortoise.utils import get_schema_sql

import models as _models
from application.services import tenant_service
from application.tenant_context import require_tenant_id, reset_tenant_id, set_tenant_id

# Every DB-backed test runs inside a single default tenant. Because the default
# Tenant is the first row inserted into the fresh in-memory schema, its id is 1,
# which matches the ambient context set by ``_tenant_context`` below.
DEFAULT_TEST_TENANT_ID = 1


# The suite runs on in-memory SQLite: no service to start, and a fresh schema
# per test. Set WIZZROBE_TEST_DB_URL to a postgres:// URL to run the same tests
# against the real backend — see docs/development.md on what only Postgres can
# prove (row locks are a no-op on SQLite, and the DDL itself is never exercised).
TEST_DB_URL = os.environ.get('WIZZROBE_TEST_DB_URL', 'sqlite://:memory:')

# The mocked DK64 queue simulates a minutes-long roll in real wall clock, which
# is the point of it in a browser but pure dead time in a test. Collapse it to
# zero for the whole suite; the handful of tests that assert on the queued and
# started phases set their own duration.
os.environ.setdefault('MOCK_DK64_SECONDS', '0')
ON_POSTGRES = TEST_DB_URL.startswith(('postgres://', 'postgresql://', 'asyncpg://'))


@functools.cache
def _schema_sql(_dialect: str) -> str:
    """The CREATE TABLE script for every model, rendered once per process.

    ``Tortoise.generate_schemas()`` re-derives this DDL from model metadata on
    every call (~18ms). The output only depends on the models, which do not
    change during a run, so it is rendered once and the script replayed — the
    same tables, roughly a third of the cost. Keyed by dialect because the
    rendered DDL is backend-specific. On SQLite one replay per worker builds the
    template; on PostgreSQL the ``db`` fixture replays it per test, after
    dropping the schema.
    """
    return get_schema_sql(connections.get('default'), safe=True)


@functools.cache
def _scoped_models() -> tuple[type[Model], ...]:
    """Every model carrying a ``tenant`` FK (scoped or nullable-tenant)."""
    return tuple(
        obj for obj in vars(_models).values()
        if isinstance(obj, type) and issubclass(obj, Model) and obj is not Model
        and 'tenant' in obj._meta.fields_map
    )


@pytest.fixture(autouse=True)
def _tenant_context():
    """Bind the default tenant for every test so ``require_tenant_id()`` resolves.

    DB-backed tests get the real default ``Tenant`` (id 1) created by the ``db``
    fixture; pure-mock tests just need *some* ambient id (their repositories are
    mocked, so the value is never used against a real query). Leak/isolation
    tests override this with explicit ``tenant_scope`` blocks.
    """
    token = set_tenant_id(DEFAULT_TEST_TENANT_ID)
    try:
        yield
    finally:
        reset_tenant_id(token)


@pytest.fixture
def app():
    """The full REST API app (all routers under ``/api``).

    Hoisted here from the ~19 API test modules that re-pasted it. The two
    hand-built apps (``test_api_matches.py`` / ``test_api_tokens.py``) keep
    their own bespoke fixtures.
    """
    from tests.api_helpers import build_api_app
    return build_api_app()


@pytest.fixture(autouse=True)
def stub_discord_queue(monkeypatch):
    """Capture coroutines handed to ``discord_queue.enqueue`` without running them.

    Discord notifications/DMs are fire-and-forget via the queue worker, so
    services only enqueue the coroutine — they never await it. Capturing and
    closing the coroutines lets tests assert the call happened while avoiding
    'coroutine was never awaited' warnings. Applied suite-wide; the handful of
    tests that need the *real* enqueue (``tests/services/test_discord_queue.py``)
    restore it explicitly with their own ``monkeypatch``.
    """
    captured = []
    monkeypatch.setattr(
        'application.services.discord.discord_queue.enqueue', captured.append
    )
    yield captured
    for coro in captured:
        coro.close()


@pytest.fixture(autouse=True)
def _24_hour_clock():
    """Run the suite on the 24-hour clock.

    The default with no viewer is 12-hour, but most format assertions were
    written as ``21:30``. tests/test_hour_cycle.py covers the default by
    unbinding it with ``hour_cycle_scope(None)``.
    """
    from application.timezone_context import hour_cycle_scope

    with hour_cycle_scope(False):
        yield


@pytest.fixture(autouse=True)
def _no_external_network(monkeypatch):
    """Fail loudly when a test touches the real network.

    The suite must behave identically offline and in CI (a service-health test
    once POSTed to five third-party production hosts on every CI run). Loopback
    stays allowed so tests may drive a locally-bound server; httpx's
    ASGITransport and the in-memory DB never open sockets at all.
    """
    import socket

    real_connect = socket.socket.connect
    loopback = ('127.0.0.1', '::1', 'localhost', '0.0.0.0')

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, (bytes, bytearray)):
            host = host.decode(errors='replace')
        if self.family == socket.AF_UNIX or host in loopback:
            return real_connect(self, address)
        raise RuntimeError(
            f'External network access blocked in tests: connect to {address!r}. '
            f'Mock the client (MOCK_* helpers) or stub the transport instead.'
        )

    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)


@pytest.fixture(autouse=True)
def racetime_rooms(monkeypatch):
    """Every racetime room the suite opens goes to the mock ``startrace`` client.

    Opening a room is a real racetime.gg call outside ``MOCK_RACETIME``; this
    hands each test one :class:`MockRacetimeRoomsClient` instead, whose
    ``started`` list records what was asked for. A test of the live client builds
    ``RacetimeRoomsClient`` directly.
    """
    from application.utils.clients import racetime_rooms_client

    client = racetime_rooms_client.MockRacetimeRoomsClient()
    monkeypatch.setattr(racetime_rooms_client, 'build_rooms_client', lambda: client)
    return client


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Isolate the process-global API rate-limiter counters per test.

    The limiter keys unauthenticated requests by client IP, which would
    otherwise accumulate across the whole suite and spuriously trip 429s.
    """
    from api.rate_limit import _hits
    _hits.clear()
    yield
    _hits.clear()


@pytest.fixture(autouse=True)
def _reset_crew_signup_notices():
    """Isolate the per-process crew-signup DM throttle; match ids repeat across tests."""
    from application.services.crew_service import _signup_notice_rows, _signup_notices
    _signup_notices.clear()
    _signup_notice_rows.clear()
    yield
    _signup_notices.clear()
    _signup_notice_rows.clear()


@pytest.fixture(autouse=True)
def _reset_notification_settings_cache():
    """Isolate the per-process notification-settings cache; tenant ids repeat across tests."""
    from application.services.notification_preference_service import _cache as personal
    from application.services.notification_settings_service import _cache
    _cache.clear()
    personal.clear()
    yield
    _cache.clear()
    personal.clear()


async def _seed_baseline() -> None:
    """The rows every DB-backed test starts from: the default tenant, flags on.

    Feature flags default OFF (available AND enabled), which would make every
    flag-gated service guard raise for the ~all-features-on legacy test suite.
    Turning every flag fully on for the default tenant keeps existing tests
    exercising features as before; feature-flag-specific tests set their own
    state under their own tenants.
    """
    await _models.Tenant.create(
        id=DEFAULT_TEST_TENANT_ID, name='Default', slug='default',
    )
    if ON_POSTGRES:
        # An explicit id does not advance the SERIAL sequence, so the next
        # auto-assigned Tenant id would collide with the one just forced above.
        # (SQLite hides this: its rowid counter is max(id) + 1 either way.)
        await connections.get('default').execute_script(
            "SELECT setval(pg_get_serial_sequence('tenant', 'id'), "
            "(SELECT MAX(id) FROM tenant));"
        )
    await _models.TenantFeatureFlag.bulk_create([
        _models.TenantFeatureFlag(
            tenant_id=DEFAULT_TEST_TENANT_ID, flag=flag.value,
            available=True, enabled=True,
        )
        for flag in _models.FeatureFlag
    ])


def _run_sqlite_on_the_event_loop() -> None:
    """Execute SQLite statements inline instead of on aiosqlite's worker thread.

    aiosqlite hands every statement to a background thread and waits on a
    future, so each query costs a queue put, a thread wake-up, a
    ``call_soon_threadsafe`` and a selector round trip on top of the query
    itself. That design is right for a server — a slow query would otherwise
    stall the loop — and wrong for this suite, where the database is in memory
    and every statement returns in microseconds. Skipping the hop halves the
    cost of every query in the suite.

    ``check_same_thread`` has to come off with it: the connection is still
    opened on aiosqlite's thread and is now used from the loop's.
    """
    import aiosqlite

    real_connect = aiosqlite.connect

    def connect(database, **kwargs):
        kwargs.setdefault('check_same_thread', False)
        return real_connect(database, **kwargs)

    async def execute_inline(self, fn, *args, **kwargs):
        if not self._running or not self._connection:
            raise ValueError('Connection closed')
        return fn(*args, **kwargs)

    aiosqlite.connect = connect
    aiosqlite.Connection._execute = execute_inline


if not ON_POSTGRES:
    _run_sqlite_on_the_event_loop()


# --- The engine, built once per worker process -----------------------------
#
# ``Tortoise.init()`` is not a connect call: most of its ~15ms is
# ``_build_initial_querysets()``, which re-derives a filter map for all ~70
# models from their metadata. Running it per test made it the single most
# expensive thing in the suite — 60s of a 190s profiled run. The models cannot
# change during a run, so it happens once and every test reuses the result.
#
# What each test still needs is an *empty* database, and on SQLite that is where
# the template comes in: the pristine schema-plus-baseline state is snapshotted
# into a detached in-memory ``sqlite3`` connection, and each test restores it
# with SQLite's own backup API — one C-level page copy, ~1ms, versus ~31ms to
# re-init Tortoise and replay the DDL. The snapshot carries ``sqlite_sequence``
# with it, so autoincrement ids restart at 1 exactly as they did with a
# brand-new database.
BASELINE_TEMPLATE = 'baseline'

_engine_ready = False
_templates: dict[str, sqlite3.Connection] = {}
_shared_sqlite: Optional[Any] = None


def _snapshot(live: sqlite3.Connection) -> sqlite3.Connection:
    """Copy the live database into a detached template connection."""
    template = sqlite3.connect(':memory:', check_same_thread=False)
    live.backup(template)
    return template


def _overwrite(live: sqlite3.Connection, template: sqlite3.Connection) -> None:
    """Overwrite ``live`` with ``template``.

    The rollback is a no-op in the autocommit mode Tortoise opens SQLite in; it
    is here for the test that dies inside ``in_transaction()``, because the
    backup API refuses a destination with a transaction open.
    """
    live.rollback()
    template.backup(live)


async def capture_template(name: str) -> None:
    """Snapshot the database as it stands now, under ``name``."""
    await _shared_sqlite._execute(
        lambda: _templates.__setitem__(name, _snapshot(_shared_sqlite._conn))
    )


async def restore_template(name: str) -> None:
    """Replace the live database with the snapshot taken under ``name``."""
    await _shared_sqlite._execute(
        _overwrite, _shared_sqlite._conn, _templates[name]
    )


async def _bootstrap_engine() -> None:
    """Initialise Tortoise and, on SQLite, build the baseline template.

    Idempotent: the first DB-backed test of the worker pays for it.
    """
    global _engine_ready, _shared_sqlite
    if _engine_ready:
        return
    await Tortoise.init(db_url=TEST_DB_URL, modules={"models": ["models"]})
    if not ON_POSTGRES:
        client = connections.get('default')
        await client.execute_script(_schema_sql('sqlite'))
        await _seed_baseline()
        _shared_sqlite = client._connection
        await capture_template(BASELINE_TEMPLATE)
    _engine_ready = True


async def _close_stray_connection() -> None:
    """Close a connection something other than this fixture opened.

    A test that touches the database *without* requesting ``db`` makes
    ``connections.get()`` build a client and open a second, schema-less
    ``:memory:`` database — which is what it has always done, and what makes the
    missing fixture show up as "no such table". What it also does is start an
    aiosqlite worker thread, and that thread is not a daemon: leave four of them
    behind and the interpreter never exits. ``Tortoise.init()`` used to sweep
    them up on the next test (it opens with ``close_all``); now that init runs
    once per process, this does.
    """
    stray = connections._get_storage().get('default')
    if stray is not None and stray._connection is not _shared_sqlite:
        await stray.close()


async def _bind_shared_sqlite():
    """Hand this test a fresh client object over the shared SQLite connection.

    The connection is reused (it *is* the database — a new ``:memory:`` handle
    would be a new, empty one), but the ``SqliteClient`` wrapper around it is
    rebuilt per test for two reasons: its ``asyncio.Lock`` would otherwise bind
    to the first test's event loop, and tests that shadow a method on the client
    (``tests/test_query_budget.py``, ``tests/api/test_error_paths.py``) restore
    by re-assigning rather than deleting, which would leave the shadow in place
    for every later test on this worker.
    """
    await _close_stray_connection()
    client = connections._create_connection('default')
    client._connection = _shared_sqlite
    connections._get_storage()['default'] = client
    return client


@pytest.fixture
async def db(monkeypatch):
    """Function-scoped in-memory SQLite for tests that need a real DB.

    Each test gets a fresh schema with no leakage from prior tests. A single
    default ``Tenant`` (id 1) is created up front, and every tenant-scoped
    model's ``.create`` is wrapped to stamp that tenant when a caller omits it —
    so the existing ~700 ``Model.create`` test sites need no per-call edits while
    the production ``never auto-stamp`` contract stays intact (that wrapper lives
    only here, in the test harness). Repositories that stamp tenant themselves
    pass ``tenant_id`` explicitly, which the wrapper leaves untouched.
    """
    # The tenant resolution caches hold ``Tenant`` instances read out of the
    # previous test's database. They are process-global and invalidated only on
    # a write, so with the database now outliving a single test they have to be
    # dropped here or a stale row survives into the next one.
    tenant_service._clear_cache()

    await _bootstrap_engine()
    if ON_POSTGRES:
        # A real database persists between tests, so hand each one an empty
        # schema. Dropping and recreating is slower than truncating but cannot
        # leave a stale column behind when the models move underneath it.
        conn = connections.get('default')
        await conn.execute_script(
            'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;'
        )
        await conn.execute_script(_schema_sql('postgres'))
        await _seed_baseline()
    else:
        await _bind_shared_sqlite()
        await restore_template(BASELINE_TEMPLATE)

    for model in _scoped_models():
        original = model.create

        def _make(original):
            async def _stamped_create(**kwargs):
                # Stamp the currently-scoped tenant (default id 1, or whatever a
                # leak test set via tenant_scope) so direct test creates land in
                # the right tenant without a per-call edit.
                if 'tenant' not in kwargs and 'tenant_id' not in kwargs:
                    kwargs['tenant_id'] = require_tenant_id()
                return await original(**kwargs)
            return _stamped_create

        monkeypatch.setattr(model, 'create', _make(original))

    try:
        yield
    finally:
        # Take the live client back out of the registry. A test that forgot to
        # request ``db`` then gets what it always got — a connection to an empty
        # database that has never seen the schema — rather than a quiet read of
        # whatever the previous test left behind.
        connections.discard('default')


SEEDED_TEMPLATE = 'seeded'


@pytest.fixture
async def seeded_db(db):
    """``db`` plus everything ``scripts/seed_dev.py`` creates.

    Seven tests across four modules assert against the dev fixture set, and
    ``seed_all()`` costs ~1.5s a call — the most expensive single thing left in
    the suite. It is idempotent and deterministic, so on SQLite the result is
    snapshotted the first time a worker needs it and restored from there on,
    exactly as the empty schema is.

    A test about the seed *running* rather than its result — idempotency,
    ordering — takes plain ``db`` and calls ``seed_all()`` itself.
    """
    if not ON_POSTGRES and SEEDED_TEMPLATE in _templates:
        await restore_template(SEEDED_TEMPLATE)
        tenant_service._clear_cache()
        return
    from scripts.seed_dev import seed_all

    await seed_all()
    if not ON_POSTGRES:
        await capture_template(SEEDED_TEMPLATE)


def pytest_sessionfinish(session, exitstatus):
    """Close the shared SQLite connection so the interpreter can exit.

    aiosqlite drives each connection from a non-daemon thread, which now
    outlives every individual test.
    """
    global _shared_sqlite, _engine_ready

    async def _close_all() -> None:
        await _close_stray_connection()
        if _shared_sqlite is not None:
            await _shared_sqlite.close()

    asyncio.run(_close_all())
    _shared_sqlite = None
    for template in _templates.values():
        template.close()
    _templates.clear()
    _engine_ready = False


# Canonical two-tenant fixtures. Historically each isolation test re-created a
# second tenant inline under one of four drifting slugs (``tenant-b``/``beta``/
# ``b``/``community``); these fixtures give one slug convention (``tenant-b``)
# and one place to build on. ``tenant_a`` is the default tenant (id 1) the
# ``db`` fixture creates.
SECOND_TENANT_SLUG = 'tenant-b'


async def enable_all_flags(tenant_id: int) -> None:
    """Turn every feature flag fully on for ``tenant_id`` (test helper).

    Mirrors what the ``db`` fixture does for the default tenant. A leak test's
    second tenant needs this: with flags off, a guarded service raises
    ``FeatureDisabledError`` before its tenant scoping is ever exercised, so the
    test would pass for the wrong reason (or fail confusingly) and stop proving
    isolation.
    """
    for flag in _models.FeatureFlag:
        await _models.TenantFeatureFlag.get_or_create(
            tenant_id=tenant_id, flag=flag.value,
            defaults={'available': True, 'enabled': True},
        )


@pytest.fixture
async def two_tenants(db):
    """The default tenant plus a second one (slug ``tenant-b``).

    Returns ``(tenant_a, tenant_b)``. Build tenant-scoped rows on top with
    ``tenant_scope(tenant.id)``. Both tenants have every feature flag on, so
    isolation tests exercise tenant scoping rather than tripping a feature gate.
    """
    tenant_a = await _models.Tenant.get(id=DEFAULT_TEST_TENANT_ID)
    tenant_b = await _models.Tenant.create(name='Tenant B', slug=SECOND_TENANT_SLUG)
    await enable_all_flags(tenant_b.id)
    return tenant_a, tenant_b


@pytest.fixture
async def two_tenant_api(two_tenants):
    """Two tenants each with a STAFF token, a tournament, and a match, plus an API app.

    Returns a dict: ``app``, ``token_a``/``token_b``, ``ta``/``tb``, ``ma``/``mb``.
    """
    from application.tenant_context import tenant_scope
    from models import Match, Role, Tournament
    from tests.api_helpers import build_api_app, create_user_token

    tenant_a, tenant_b = two_tenants
    with tenant_scope(tenant_a.id):
        _, token_a = await create_user_token(username='a-staff', roles=[Role.STAFF])
        ta = await Tournament.create(name='A Cup')
        ma = await Match.create(tournament=ta)
    with tenant_scope(tenant_b.id):
        _, token_b = await create_user_token(username='b-staff', roles=[Role.STAFF])
        tb = await Tournament.create(name='B Cup')
        mb = await Match.create(tournament=tb)

    return {
        'app': build_api_app(),
        'token_a': token_a, 'token_b': token_b,
        'ta': ta, 'tb': tb, 'ma': ma, 'mb': mb,
    }
