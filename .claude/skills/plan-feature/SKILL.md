---
name: plan-feature
description: >-
  Turn a feature request into an approved spec BEFORE any code is written,
  following docs/specs/README.md: scoping questions, a lite or full spec from
  docs/specs/templates/, mockups, one sign-off. The full spec's Data and
  permissions and PR plan cover the feature-flag decision, model sketch +
  tenant impact, layer touchpoints, audit/event additions, test plan, seed and
  docs touchpoints. Use whenever a task adds a feature or changes a workflow
  and no approved spec exists yet; implementation then follows /add-feature.
---

# Planning a feature (spec before code)

Follow [docs/specs/README.md](../../../docs/specs/README.md): ask the scoping
questions, write the spec from
[templates/lite.md](../../../docs/specs/templates/lite.md) or
[templates/full.md](../../../docs/specs/templates/full.md), make the mockups,
then **stop for the one sign-off**. Don't start implementing until the spec is
`Approved`. Bug fixes, copy tweaks, small polish and refactors skip this skill.

Explore first: read the models/services/pages the feature touches, and check
[docs/current-state.md](../../../docs/current-state.md) plus the relevant
[docs/features/](../../../docs/features/) doc so the spec builds on what
exists instead of re-inventing it.

For a **full** spec, work through the checklist below and put the answers in
its **Data and permissions** and **PR plan** sections (tests, seed rows and
docs belong to the PR that owes them). Every item maps to an /add-feature step
and to the guardrail that will enforce it later, so a complete spec means a
friction-free build. A lite spec only needs the items that apply.

## Full-spec checklist

### 1. Feature flag?

Ask the user whether this warrants a per-tenant feature flag (CLAUDE.md step 0
— flags exist only for deliberately-gated subsystems, not one per feature). If
yes, list what gets gated: the `FeatureFlag` enum member + `FeatureFlagSpec`
(`application/feature_flags.py` — `tests/test_feature_flags.py` fails until
registry parity holds), `@protected_page(feature=...)` on pages,
`and FeatureFlag.X in live` on tabs, `require_feature(...)` on REST routers,
and the `is_enabled` skip in workers.

### 2. Model sketch + tenant impact

Per new/changed model: fields and enums; **tenant FK?** (almost everything a
community owns is tenant-scoped — identity is global); formerly-global uniques
that become composite `unique_together (('tenant', ...))`; relations to
existing models. Then state the consequences explicitly:

- a migration (`aerich migrate`) — the drift hook token-matches your changed
  model/field names against it;
- a **leak test per tenant-scoped model** — `tests/tenancy/test_leak_test_coverage.py`
  fails a model that appears in no `tests/*isolation*` file (its BACKLOG is
  for pre-existing debt only, and it may never grow);
- seed rows — `tests/test_seed_coverage.py` runs the real seed and fails any
  tenant-FK model with no row.

### 3. Layer touchpoints

- **Repository** (`application/repositories/`): new module or extension?
  Default shape: subclass `TenantScopedRepository` (`_base.py`); scope reads
  with `scoped(...)`, stamp writes; docstring says "cross-tenant"/"unscoped"
  where deliberate; export from `__init__.py`.
- **Service** (`application/services/`): rules and coordination; new
  `AuditActions` constants; matching `EventType` members (+ `ALL`) or a note
  that the absence of events is deliberate; `require_found(obj, label)` /
  `NotFoundError` for missing entities; `AuditService.write_and_publish` for
  audit+event pairs; export from `__init__.py` (functional modules export
  their stem).
- **UI** (`pages/`, `theme/`): which page/tab/dialog; admin tables build on
  `theme/tables/admin_crud.py` (`current_actor`, `ServiceTableView`,
  `actions_slot`) and dialogs on `theme/dialog/_helpers.py`; role gate for
  `@protected_page`.
- **API** (`api/`): endpoints and schemas; services raise `NotFoundError`
  (→404 via `ServiceErrorRoute`) — **no router `_load_*_or_404` preloads**
  (the DRY hook blocks new ones); `require_feature` if flagged.
- **Bot** (`discordbot/`): interaction handlers; any worker wraps scoped data
  in `tenant_scope(tenant_id)` and uses `run_worker_loop`, with a feature-flag
  skip if flagged.

### 4. Test plan

Name the files: `tests/services/test_<service>.py` behaviors;
`tests/test_<model>_tenant_isolation.py` (or an existing isolation file) for
each scoped model; API tests covering the 401 / 403-role / 403-read-only /
404 matrix plus cross-tenant 404. Use `tests/factories.py` and the hoisted
conftest fixtures — the DRY hook flags local `make_user`/`utc`/`app` copies.
No test may touch the network (conftest socket guard).

### 5. Seed additions

Rows per model and per meaningful state in `scripts/seed_dev.py` (idempotent
`get_or_create`, tenant-threaded like existing rows; split files
`seed_online.py`/`seed_challonge.py` show where domain blocks go).

### 6. Docs touchpoints

Which of `docs/reference/{data-model,services,rest-api,frontend,discord-integration}.md`
and `docs/features/` need updates (doc-reminder/doc-check hooks will nag).

### 7. Open questions

Anything ambiguous: scope cuts, permissions, UX placement, naming, rollout.
Ask them (one per question card where the host has cards), log each answer in
the spec's **Decisions**, and stop for sign-off once **Open questions** is
empty.
