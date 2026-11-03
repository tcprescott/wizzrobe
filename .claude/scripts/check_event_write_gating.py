#!/usr/bin/env python3
"""
PostToolUse hook: a bulk write on an archived-guarded model must check the event.

docs/features/events.md: an archived event is read-only. ``application/event_writes.py``
enforces it with ``pre_save``/``pre_delete`` signals on every model an event
owns (an ``event`` foreign key to ``models.Event`` that cascades) and on their
children (a cascading foreign key to a guarded model: a match's players, a
bracket's games). Signals fire for ``Model.create``, ``instance.save()`` and
``instance.delete()``. They do **not** fire for queryset ``.update()`` /
``.delete()`` or for ``bulk_create`` / ``bulk_update``, so those must ask first:

    qs = scoped(Match.filter(tournament_id=tid))
    await ensure_writable(qs)          # from application.event_writes
    await qs.update(stage=None)

Flags, in a repository or a service, a bulk write whose queryset is rooted at a
guarded model (directly, through ``scoped``/``event_scoped``/…, or through a
local variable assigned from one) when the enclosing function calls neither
``ensure_writable`` nor ``ensure_event_writable``.

Escape hatch: ``event-write: exempt — <reason>`` in the enclosing function (a
write that archiving itself makes, or one on rows that can't be archived yet).

Exit 0 = clean / not applicable; exit 2 = violation (stderr explains).
"""

import ast
import json
import os
import sys

from _hook_paths import anchor

anchor()

BULK_ON_QUERYSET = {"update", "delete"}
BULK_ON_MODEL = {"bulk_create", "bulk_update"}
CHECKS = {"ensure_writable", "ensure_event_writable"}
EXEMPT_MARKER = "event-write: exempt"
QUERY_METHODS = {"filter", "all", "exclude", "select_for_update", "only", "limit", "order_by", "distinct"}


def _models_dir(start_path: str) -> str | None:
    d = os.path.dirname(os.path.abspath(start_path))
    while True:
        pkg = os.path.join(d, "models")
        if os.path.isfile(os.path.join(pkg, "__init__.py")):
            return pkg
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _fk(call: ast.AST) -> tuple[str, bool] | None:
    """``(target model, cascades)`` for a ForeignKeyField/OneToOneField call."""
    if not isinstance(call, ast.Call):
        return None
    name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
    if name not in ("ForeignKeyField", "OneToOneField") or not call.args:
        return None
    arg = call.args[0]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    # Tortoise's default on_delete is CASCADE.
    on_delete = next((ast.unparse(kw.value) for kw in call.keywords if kw.arg == "on_delete"), "CASCADE")
    cascades = "CASCADE" in on_delete
    return arg.value.split(".")[-1], cascades


def guarded_models(models_dir: str) -> set[str]:
    edges: dict[str, list[str]] = {}
    owned: set[str] = set()
    for entry in sorted(os.listdir(models_dir)):
        if not entry.endswith(".py"):
            continue
        try:
            with open(os.path.join(models_dir, entry), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            parents = edges.setdefault(node.name, [])
            for stmt in node.body:
                value = stmt.value if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else None
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [getattr(stmt, "target", None)]
                fk = _fk(value) if value is not None else None
                if fk is None or not fk[1]:
                    continue
                target, _ = fk
                if target == "Event" and any(isinstance(t, ast.Name) and t.id == "event" for t in targets):
                    owned.add(node.name)
                else:
                    parents.append(target)
    guarded = set(owned)
    grew = True
    while grew:
        grew = False
        for model, parents in edges.items():
            if model not in guarded and any(p in guarded for p in parents):
                guarded.add(model)
                grew = True
    return guarded


def _root_model(node: ast.AST, assigned: dict[str, ast.AST], seen: frozenset[str] = frozenset()) -> str | None:
    """The model class a queryset expression starts from, or None."""
    while True:
        if isinstance(node, ast.Await):
            node = node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in QUERY_METHODS:
            node = node.func.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args:
            # scoped(...), event_scoped(...), live_event_scoped(...)
            node = node.args[0]
        elif isinstance(node, ast.Name):
            if node.id in assigned and node.id not in seen:
                return _root_model(assigned[node.id], assigned, seen | {node.id})
            return node.id if node.id[:1].isupper() else None
        else:
            return None


def find_violations(tree: ast.AST, guarded: set[str], lines: list[str]) -> list[tuple[int, str, str]]:
    out = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = min((d.lineno for d in func.decorator_list), default=func.lineno)
        segment = "\n".join(lines[start - 1:(func.end_lineno or func.lineno)]).lower()
        if EXEMPT_MARKER in segment:
            continue
        checked = any(
            isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) in CHECKS
            for n in ast.walk(func)
        )
        if checked:
            continue
        assigned: dict[str, ast.AST] = {}
        for n in ast.walk(func):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                assigned[n.targets[0].id] = n.value
        for n in ast.walk(func):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            method = n.func.attr
            if method in BULK_ON_MODEL:
                model = n.func.value.id if isinstance(n.func.value, ast.Name) else None
            elif method in BULK_ON_QUERYSET:
                target = n.func.value
                # instance.delete() / instance.update_from_dict() fire signals: only a
                # queryset expression (a call, or a name bound to one) is bulk.
                if isinstance(target, ast.Name) and target.id not in assigned:
                    continue
                model = _root_model(target, assigned)
                if isinstance(target, ast.Name) and model is not None and not isinstance(assigned[target.id], (ast.Call, ast.Await)):
                    continue
            else:
                continue
            if model in guarded:
                out.append((n.lineno, model, method))
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
    if "/tests/" in norm or norm.startswith("tests/") or "/.claude/" in norm:
        sys.exit(0)
    if not ("/application/repositories/" in norm or "/application/services/" in norm):
        sys.exit(0)

    try:
        with open(file_path, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=file_path)
    except (OSError, SyntaxError):
        sys.exit(0)

    models_dir = _models_dir(file_path)
    if not models_dir:
        sys.exit(0)
    violations = find_violations(tree, guarded_models(models_dir), source.splitlines())
    if not violations:
        sys.exit(0)
    for line, model, method in violations:
        print(
            f"ARCHIVED-EVENT WRITE GATING in '{file_path}' (line {line}):\n"
            f"  .{method}() on {model} skips the archived-event signal guard.\n"
            f"  Call `await ensure_writable(<the same queryset>)` first, or\n"
            f"  `await ensure_event_writable(event_id)` for a bulk_create\n"
            f"  (from application.event_writes import ensure_writable).\n"
            f"  A write archiving itself makes: say 'event-write: exempt — <reason>'\n"
            f"  in the enclosing function.",
            file=sys.stderr,
        )
    sys.exit(2)


if __name__ == "__main__":
    main()
