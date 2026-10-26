#!/usr/bin/env python3
"""
PostToolUse hook: every REST endpoint declares who may call it.

Auth in this API is **per-endpoint, not global**. The aggregating router in
``api/__init__.py`` carries only ``rate_limit`` and ``tenant_context_scope``, so
a route inherits no actor requirement from being mounted. Two mistakes follow
from that, and neither is visible in review — the endpoint works perfectly for
the author, who has a staff read-write token:

1. A route with no ``require_*`` dependency at all is world-readable behind
   nothing but the IP rate limiter.
2. A mutating route (POST/PUT/PATCH/DELETE) that takes a read-side dependency
   (``require_api_actor``, ``require_admin``, ``require_staff``,
   ``require_super_admin``) is writable by a token its owner deliberately marked
   read-only. The write-side variants exist for exactly this and differ by one
   suffix, so the wrong one is an easy thing to type.

The convention is fully observed today and was enforced by nothing. A route may
satisfy it from its own parameters or from a router-level
``APIRouter(dependencies=[Depends(require_…)])``; both are read.

Exit 0 = clean / not applicable; exit 2 = violation (stderr explains).
"""

import ast
import json
import os
import sys

from _hook_paths import anchor

anchor()  # hooks inherit the session's shell cwd; pin paths to the repo

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
MUTATING = {"post", "put", "patch", "delete"}

# `require_feature(...)` gates a flag, not an actor — it authorizes nothing.
NON_ACTOR = {"require_feature"}

# Documented exceptions, by (module stem, function name):
#   health   — a liveness probe, deliberately open.
#   version  — the build version, also printed on the public /about page.
#   rotate   — a push service reissued an endpoint; the caller proves itself by
#              knowing the old endpoint and auth, and has no token to send.
ALLOWLIST = {("health", "health"), ("version", "version"), ("web_push", "rotate")}


def is_write_dep(name: str) -> bool:
    return name.endswith("_write") or name == "require_write_actor"


def is_actor_dep(name: str) -> bool:
    return name.startswith("require_") and name not in NON_ACTOR


def _depends_name(node: ast.AST) -> str | None:
    """The `X` in `Depends(X)` or `Depends(X(...))`, else None."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (isinstance(func, ast.Name) and func.id == "Depends"):
        return None
    if not node.args:
        return None
    target = node.args[0]
    if isinstance(target, ast.Call):  # Depends(require_feature(FLAG))
        target = target.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def router_level_deps(tree: ast.AST) -> dict[str, set[str]]:
    """Map each `x = APIRouter(dependencies=[...])` name to its dependency names."""
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if called != "APIRouter":
            continue
        names: set[str] = set()
        for kw in node.value.keywords:
            if kw.arg != "dependencies" or not isinstance(kw.value, (ast.List, ast.Tuple)):
                continue
            for element in kw.value.elts:
                dep = _depends_name(element)
                if dep:
                    names.add(dep)
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = names
    return out


def route_decorators(node: ast.AST) -> list[tuple[str, str]]:
    """(router name, http method) for each route decorator on a function."""
    found = []
    for dec in getattr(node, "decorator_list", []):
        call = dec if isinstance(dec, ast.Call) else None
        target = call.func if call else dec
        if not isinstance(target, ast.Attribute) or target.attr not in HTTP_METHODS:
            continue
        if isinstance(target.value, ast.Name):
            found.append((target.value.id, target.attr))
    return found


def param_deps(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = node.args
    defaults = list(args.defaults) + [d for d in args.kw_defaults if d is not None]
    names = set()
    for default in defaults:
        dep = _depends_name(default)
        if dep:
            names.add(dep)
    return names


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path or not file_path.endswith(".py"):
        sys.exit(0)

    norm = file_path.replace("\\", "/")
    if "/api/routers/" not in norm or "/.claude/" in norm or "/tests/" in norm:
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

    stem = os.path.basename(norm)[:-3]
    routers = router_level_deps(tree)

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = route_decorators(node)
        if not routes or (stem, node.name) in ALLOWLIST:
            continue
        own = param_deps(node)
        for router_name, method in routes:
            deps = own | routers.get(router_name, set())
            actors = {d for d in deps if is_actor_dep(d)}
            if not actors:
                violations.append(
                    (node.lineno, node.name, method,
                     "takes no require_* dependency, so anyone can call it")
                )
            elif method in MUTATING and not any(is_write_dep(d) for d in actors):
                violations.append(
                    (node.lineno, node.name, method,
                     f"is a write guarded only by {sorted(actors)}, which a "
                     f"read-only token satisfies — use the _write variant")
                )

    if violations:
        for line, name, method, why in violations:
            print(
                f"API AUTH VIOLATION in '{file_path}' (line {line}):\n"
                f"  {method.upper()} route '{name}' {why}.\n"
                f"  Auth here is per-endpoint: the aggregating router adds none.\n"
                f"  Add `actor: User = Depends(require_write_actor)` (or the\n"
                f"  role-specific _write variant), or declare it on the APIRouter.\n"
                f"  A deliberately open endpoint goes in this check's ALLOWLIST.",
                file=sys.stderr,
            )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
