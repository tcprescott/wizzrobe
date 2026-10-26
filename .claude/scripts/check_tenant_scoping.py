#!/usr/bin/env python3
"""
PostToolUse hook: repository queries on tenant-scoped models must be scoped.

CLAUDE.md > Multitenancy: there is no auto-scoping manager — every repository
read on a tenant-scoped model goes through ``scoped(Model.filter(...))`` and
every write stamps ``tenant_id=current_tenant_id()``. A forgotten scope is a
silent cross-tenant leak (reads) or an orphaned/unstamped row (writes).

This inspects the resulting file after a Write/Edit to a **repository or
service** module. Tenant-scoped model names are discovered from the ``models/``
package at runtime (any Tortoise ``Model`` subclass with a ``tenant`` field).

In a **service** module only *writes* are flagged. Services legitimately read a
scoped model directly (the sanctioned load-or-404 shape passes ``tenant_id=``
and is checked like any other), but a direct unstamped write is never right: the
tenant FK is non-null, so the insert fails outright — a production bug the test
suite cannot see, because ``tests/conftest.py``'s ``db`` fixture stamps the
tenant for any caller that omits it. That is exactly how the enrolment write in
``UserService`` shipped broken. Route service writes through the repository that
stamps them.

Flags:

- a read root — ``Model.filter/get/get_or_none/all/first/exists(...)`` — that
  is neither wrapped in a ``scoped(...)`` (or ``event_scoped``/
  ``live_event_scoped``) call nor passing a ``tenant``/
  ``tenant_id``/``tenant__…`` kwarg;
- a write root — ``Model.create/get_or_create/update_or_create(...)`` — with
  no tenant kwarg (``get_or_create`` may carry it in ``defaults={...}``).

Escape hatches:

- the handful of deliberately cross-tenant repository methods (token lookup by
  hash, guild→tenant routing, worker scans) document it — any function whose
  source contains "cross-tenant", "unscoped", or "global" is exempt, matching
  the convention required by ``application/repositories/_tenant.py``;
- models where the ``tenant`` FK is the row's *subject* rather than a scoping
  stamp (membership/grant junction tables answering "which tenants…?") are in
  ``EXEMPT_MODELS`` — their queries legitimately select across tenants.

Deliberately misses (precision over recall): ``bulk_create`` (rows are built
elsewhere), bare instance ``obj.save()``, and queries built outside a
repository module.

Exit 0 = clean / not applicable; exit 2 = violation (stderr explains).
"""

import ast
import json
import os
import sys

from _hook_paths import anchor

anchor()  # hooks inherit the session's shell cwd; pin paths to the repo

READ_ROOTS = {"filter", "get", "get_or_none", "all", "first", "exists", "exclude"}
# ``event_scoped``/``live_event_scoped`` (application/event_context.py) filter on
# the tenant as well as the event, so they scope a read just as ``scoped`` does.
SCOPERS = {"scoped", "event_scoped", "live_event_scoped"}
WRITE_ROOTS = {"create", "get_or_create", "update_or_create"}
# Terms of art, plus an explicit opt-out in the shape the other hooks use.
# `global` used to be here and was the loosest possible hatch: an ordinary
# English word, matched as a substring anywhere in a function's source, that
# then exempted every query in that function. It appears 26 times across
# application/repositories/ for unrelated reasons ("the global OAuth token",
# "global_default"), so a new scoped method added to any of those files
# inherited a silent exemption.
EXEMPT_MARKERS = ("cross-tenant", "unscoped", "tenant-scope: exempt")
# tenant is the subject of these rows (which tenants does X belong to / may X
# act in), not a scoping stamp — reads legitimately span or select tenants.
EXEMPT_MODELS = {"TenantMembership", "TenantJoinRequest", "TenantMemberBlock", "RacetimeBotTenant"}


def find_models_source(start_path: str) -> str | None:
    """Locate the ``models/`` package (or legacy ``models.py``) walking up."""
    d = os.path.dirname(os.path.abspath(start_path))
    while True:
        pkg = os.path.join(d, "models")
        if os.path.isfile(os.path.join(pkg, "__init__.py")):
            return pkg
        single = os.path.join(d, "models.py")
        if os.path.isfile(single):
            return single
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _tenant_models_in(path: str) -> set[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_model = any(
            (base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)) == "Model"
            for base in node.bases
        )
        if not is_model:
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "tenant" for t in stmt.targets
            ):
                names.add(node.name)
                break
    return names


def load_tenant_models(models_source: str) -> set[str]:
    if os.path.isdir(models_source):
        names: set[str] = set()
        for entry in sorted(os.listdir(models_source)):
            if entry.endswith(".py"):
                names |= _tenant_models_in(os.path.join(models_source, entry))
        return names
    return _tenant_models_in(models_source)


def has_tenant_kwarg(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg and (kw.arg == "tenant" or kw.arg.startswith("tenant_")):
            return True
        if kw.arg == "defaults" and isinstance(kw.value, ast.Dict):
            for key in kw.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and (
                    key.value == "tenant" or key.value.startswith("tenant_")
                ):
                    return True
    return False


def exempt_spans(tree: ast.AST, source_lines: list[str]) -> list[tuple[int, int]]:
    """Line spans of functions whose source declares itself cross-tenant."""
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min((d.lineno for d in node.decorator_list), default=node.lineno)
            end = node.end_lineno or node.lineno
            segment = "\n".join(source_lines[start - 1:end]).lower()
            if any(marker in segment for marker in EXEMPT_MARKERS):
                spans.append((start, end))
    return spans


def find_violations(
    tree: ast.AST,
    tenant_models: set[str],
    spans: list[tuple[int, int]],
    writes_only: bool = False,
):
    # Map each node to its parent so a read can be recognised inside scoped(...).
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def inside_scoped(node: ast.AST) -> bool:
        cur = parents.get(node)
        while cur is not None:
            if (
                isinstance(cur, ast.Call)
                and isinstance(cur.func, ast.Name)
                and cur.func.id in SCOPERS
            ):
                return True
            cur = parents.get(cur)
        return False

    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            continue
        model, method = func.value.id, func.attr
        if model not in tenant_models or model in EXEMPT_MODELS:
            continue
        if method not in READ_ROOTS and method not in WRITE_ROOTS:
            continue
        # Services legitimately read a tenant-scoped model directly (the
        # sanctioned load-or-404 shape passes tenant_id= and is caught below);
        # what they must never do is *write* one unstamped, because the tenant
        # FK is non-null and the row simply fails to insert.
        if writes_only and method not in WRITE_ROOTS:
            continue
        if any(start <= node.lineno <= end for start, end in spans):
            continue
        if has_tenant_kwarg(node):
            continue
        if method in READ_ROOTS and inside_scoped(node):
            continue
        out.append((node.lineno, model, method, method in WRITE_ROOTS))
    return out


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path or not file_path.endswith(".py"):
        sys.exit(0)

    norm = file_path.replace("\\", "/")
    in_repository = "/application/repositories/" in norm
    in_service = "/application/services/" in norm
    # CLAUDE.md sanctions a read-only load-or-404 model lookup at an entry
    # surface, and requires it to hand-scope:
    #   Tournament.get_or_none(id=x, tenant_id=require_tenant_id())
    # An unscoped one there reads across tenants, so the surfaces that are
    # allowed to query directly are exactly the ones that need checking.
    is_test = "/tests/" in norm or norm.startswith("tests/")
    in_entry = not is_test and any(
        f"/{d}/" in norm for d in ("pages", "theme", "api", "discordbot", "mcpserver")
    )
    if (not in_repository and not in_service and not in_entry) or "/.claude/" in norm:
        sys.exit(0)
    if norm.endswith(("/_tenant.py", "/__init__.py")):
        sys.exit(0)

    try:
        with open(file_path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        sys.exit(0)

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        sys.exit(0)  # check_syntax.py owns syntax-error reporting

    models_source = find_models_source(file_path)
    if not models_source:
        sys.exit(0)
    tenant_models = load_tenant_models(models_source)
    if not tenant_models:
        sys.exit(0)

    violations = find_violations(
        tree, tenant_models, exempt_spans(tree, source.splitlines()),
        writes_only=in_service,
    )
    if violations:
        for line, model, method, is_write in violations:
            if is_write and in_service:
                fix = (
                    "  Route the write through the repository that stamps it, or pass\n"
                    "  tenant_id=require_tenant_id() explicitly."
                )
            elif is_write:
                fix = (
                    f"  Stamp the row: {model}.{method}(..., tenant_id=current_tenant_id())\n"
                    f"  (from application.repositories._tenant import current_tenant_id)"
                )
            else:
                fix = (
                    f"  Wrap the queryset: scoped({model}.{method}(...))\n"
                    f"  (from application.repositories._tenant import scoped), or pass tenant_id=..."
                )
            print(
                f"TENANT SCOPING VIOLATION in '{file_path}' (line {line}):\n"
                f"  {model}.{method}(...) on a tenant-scoped model without tenant scoping.\n"
                f"{fix}\n"
                f"  If this query is deliberately cross-tenant, say so in the enclosing\n"
                f"  function's docstring or a comment: 'cross-tenant', 'unscoped', or\n"
                f"  'tenant-scope: exempt — <reason>' (see\n"
                f"  application/repositories/_tenant.py).",
                file=sys.stderr,
            )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
