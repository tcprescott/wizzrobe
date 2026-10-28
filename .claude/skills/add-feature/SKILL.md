---
name: add-feature
description: >-
  End-to-end checklist for adding a feature to Wizzrobe: new model →
  migration → repository → service → exports → UI → dev seed → tests. Use
  whenever a task adds a model, a service capability, or a new admin/user
  surface — it sequences the three-layer work so nothing (tenant scoping,
  audit, events, seed rows, leak tests) is forgotten, and explains which
  guardrail hooks will fire along the way.
---

# Adding a feature (three-layer walkthrough)

Work the steps **in order** — each layer builds on the one below, and the
guardrail hooks in `.claude/settings.json` assume this sequence (e.g. the Stop
hooks block the turn if a model changed without a migration or a seed row).
Skip steps that don't apply (a service-only feature has no model/migration; a
UI-only change starts at step 5).

## -1. Plan first

For a new feature or a changed workflow, run **/plan-feature** and get the
user's sign-off on the spec ([docs/specs/](../../../docs/specs/README.md))
before writing code. The spec pre-answers every decision the steps below need
(flag, tenant impact, leak tests, seed rows, docs), so the build never stalls
mid-layer. Commit it to `docs/specs/<topic>.md` as the PR's first commit, keep
it true as the plan changes, and tick its acceptance items in the PR
description before leaving draft.

## 0. Feature flag? (always ask)

Before building, **ask the user whether this feature warrants a per-tenant
feature flag.** Flags exist only for deliberately-gated subsystems — not one per
feature — and do not get retrofitted onto existing features. If the answer is
yes:

- Add a member to `models.enums.FeatureFlag` and a `FeatureFlagSpec` to
  `application/feature_flags.py` (`established=True` only for an already-live
  feature — then add its key to the migration backfill so existing tenants keep
  it).
- Gate the surfaces in step 5: `@protected_page(feature=FeatureFlag.X)` on pages,
  `and FeatureFlag.X in live` on admin/home tabs, `require_feature(FeatureFlag.X)`
  on the REST router, an `is_enabled` skip in any worker.
- Seed it (step 6) and cover it (step 7). Full guide:
  [docs/features/feature-flags.md](../../../docs/features/feature-flags.md).

## 1. Model (`models/`)

- Add the model to the right per-domain submodule (`models/match.py`,
  `models/volunteer.py`, …) and re-export it from `models/__init__.py`.
- **Tenant-scoped?** Almost everything a community owns is. If so:
  - `tenant = fields.ForeignKeyField('models.Tenant', related_name='...', on_delete=fields.CASCADE)`
  - Make any formerly-global unique constraint composite:
    `unique_together = (('tenant', 'name'),)`
- Enums go in `models/enums.py`.

## 2. Migration

```bash
poetry run aerich migrate && poetry run aerich upgrade
```

Never hand-edit anything under `migrations/models/` (a PreToolUse hook blocks
it). The `check_migration_drift.py` Stop hook blocks the turn if you changed a
model without generating a migration.

## 3. Repository (`application/repositories/`)

Pure data access — no business logic, audit, or notifications.

- New file `foo_repository.py`, class `FooRepository`. Default shape: subclass
  `TenantScopedRepository` (`application/repositories/_base.py`) for the CRUD
  quartet instead of hand-rolling it — the `check_dry_regressions.py` hook
  blocks a new setattr update loop.
- **Tenant scoping is explicit** (no auto-scoping manager). Use
  `application/repositories/_tenant.py`:
  ```python
  from application.repositories._tenant import current_tenant_id, scoped

  return await scoped(Foo.filter(...)).order_by('-created_at')   # read
  return await Foo.create(tenant_id=current_tenant_id(), **kw)   # write
  ```
  The `check_tenant_scoping.py` hook flags unscoped reads/unstamped writes on
  tenant-FK models. A deliberately cross-tenant method (worker scan, global
  lookup) must say **"cross-tenant"**, **"unscoped"**, or **"global"** in its
  docstring/comment — that's both the convention and the hook's escape hatch.
- Export the class from `application/repositories/__init__.py` (import **and**
  `__all__` — `check_layer_exports.py` enforces this).

## 4. Service (`application/services/`)

Rules, validation, coordination. Must not import NiceGUI (nor any presentation
module — `enforce_architecture.py` blocks both directions).

- Raise `ValueError` for user-facing errors (UI catches → `ui.notify`). For a
  missing entity use `require_found(obj, 'Foo')` / `NotFoundError`
  (`application/errors.py`) — the API maps it to 404, and the DRY hook blocks
  a new inline `raise ValueError('… not found')`.
- **Audit** every create/update/delete: add a constant to `AuditActions`
  (`verb.object`, e.g. `foo.created`) and call
  `await AuditService().write_log(actor, AuditActions.FOO_CREATED, {...})`.
  Pass `actor: User` explicitly; never guard with `if actor:` (hooks enforce
  both the constant and the unguarded actor).
- **Event** after the commit + audit write, if webhook subscribers should be
  able to react:
  ```python
  from application.events import Event, EventType, event_bus
  event_bus.publish(Event.create(EventType.FOO_CREATED, {...}, actor))
  ```
  New event ⇒ add the constant to `EventType` **and** `EventType.ALL` in
  `application/events/event_types.py` (external webhook contract; the
  `check_event_types.py` hook enforces registry consistency and rejects
  string-literal event names). Tenant-internal config changes usually stay
  audit-only — see the commentary in `event_types.py` for the dividing line.
  An audit+event pair is one call: `AuditService.write_and_publish(...)`.
- Export from `application/services/__init__.py` (import + `__all__`).

## 5. UI (`pages/`, `theme/`) or API (`api/`) or bot (`discordbot/`)

- All three are presentation: call **services**, never
  `application.repositories` or `service.repository.*`
  (`enforce_architecture.py` blocks it). Read-only *load-or-404* model lookups
  are OK but must hand-scope: `Foo.get_or_none(id=x, tenant_id=require_tenant_id())`.
- Catch service errors: `except ValueError as e: ui.notify(str(e), color='warning')`.
- No ORM **writes** in presentation (hook-enforced).
- Protect pages with `@protected_page('/path', roles=[Role.STAFF])`.
- Admin tables build on `theme/tables/admin_crud.py` (`current_actor`,
  `ServiceTableView`, `actions_slot`) and dialogs on
  `theme/dialog/_helpers.py` (header/actions/mobile-sheet/Enter-to-submit) —
  don't clone a sibling tab.
- **Every `ui.table` needs a mobile card view** — call
  `enable_mobile_grid(table, columns, actions=…, field_slots=…)`
  (`from theme.tables.mobile_grid import enable_mobile_grid`) after building it,
  passing the same row-action button HTML you put in `body-cell-actions` and a
  `field_slots` snippet for each badge/chip/icon column. `check_table_grid`
  blocks a table without it (opt out with `# mobile-grid: exempt`). Detail:
  [frontend.md](../../../docs/reference/frontend.md#data-tables).
- **Every `ui.table` also needs a `table_key`** — pass
  `table_key=TableKeys.X` to `enable_mobile_grid` (declare the key in
  `theme/tables/preferences.TableKeys`) so the viewer can hide, reorder and
  resize its columns. A bespoke `item` slot calls `customize_table(table,
  columns, key=…)` **after** that slot. `check_table_prefs` blocks a table
  without it (opt out with `# table-prefs: exempt — <reason>`).
- API routers: **never add a `_load_*_or_404` preload** (DRY hook blocks new
  ones) — the service raises `NotFoundError` and `ServiceErrorRoute` 404s.
- Datetimes: store UTC, display viewer-local — use `application/utils/timezone.py` (never bare `datetime.combine`/`date.today()`)
  helpers only (hook-enforced).
- NiceGUI: `background_tasks.create(...)` not `asyncio.create_task`; capture
  `context.client` before background tasks that touch UI; `@ui.refreshable`
  for dynamic sections; never block the loop (`httpx.AsyncClient`, never
  `requests`/`time.sleep`). Check `nicegui/llms.md` in the installed package
  for the authoritative API before writing nontrivial NiceGUI code.
- New REST endpoints: document in `docs/reference/rest-api.md`.

## 6. Dev seed (`scripts/seed_dev.py`)

Add at least one representative row per new model (and each meaningful state),
idempotent (`get_or_create`) and seeded **per tenant** like the existing rows
(domain blocks split into `seed_online.py`/`seed_challonge.py`). The
`check_seed_coverage.py` Stop hook blocks the turn until the new model's
**name appears** in a `scripts/seed_*.py` (`# seed-exempt: Foo — reason` is
the escape for a genuinely unseedable model), and
`tests/test_seed_coverage.py` runs the real seed to verify the row actually
lands — the seed is what `/ui-validation` and every dev environment run
against.

## 7. Tests (`tests/`)

- Service/repo behavior: `tests/services/test_foo_service.py` (the
  `run_related_tests.py` hook auto-runs the file matching your edited module).
- **Tenant-scoped model ⇒ add a leak test.** Pattern: two tenants, write the
  same data under each via `with tenant_scope(a.id): ...`, assert reads under
  tenant B never see tenant A's rows. Canonical examples:
  `tests/tenancy/test_tenant_isolation.py`, `tests/tenancy/test_tenant_read_isolation.py`.
  Enforced: `tests/tenancy/test_leak_test_coverage.py` fails a tenant-FK model that
  appears in no `tests/*isolation*` file, and its BACKLOG may only shrink.
- Use `tests/factories.py` (`make_user`, `utc`) and the hoisted conftest
  fixtures (`app`, `two_tenants`, `two_tenant_api`, `stub_discord_queue`) —
  the DRY hook blocks local re-definitions. No test may touch the network
  (conftest socket guard raises on non-loopback connects).
- Run locally with `MOCK_DISCORD=true poetry run pytest -q` (the full suite
  also runs at Stop when app code changed).

## 8. Verify in the browser / against the API

For any presentation change, run the **/ui-validation** skill — the pytest
suite cannot see client-side Vue/Quasar slot templates; only the headless
browser loop can. For REST changes, run the **/api-validation** skill — it
drives the live server with the seeded bearer tokens, including the
cross-tenant-404 leak probe.

## 9. Docs

Update the matching doc (`docs/reference/data-model.md` for models,
`docs/reference/services.md` for services, `docs/reference/frontend.md` for
pages, feature doc under `docs/features/`). The session-start and Stop doc
hooks report gaps; keep them quiet.

## 10. Changelog

A feature people will notice gets a bullet in a new
`application/changelog/changes/YYYY-MM-DD-<topic>.md` file, which the in-app
"What's new" page folds into that week. Use the
[`changelog`](../changelog/SKILL.md) skill for the format and the wording rules.
