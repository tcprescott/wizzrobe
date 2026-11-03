#!/usr/bin/env python3
"""
PostToolUse hook: queries on event-owned models must name their event.

docs/features/events.md: a community holds events, and an event owns its
tournaments, matches, stages, shifts, check-in and the rest. Their rows carry
``event_id`` beside ``tenant_id``. The tenant check (``check_tenant_scoping``)
keeps communities apart; this one keeps a community's events apart, so SGL
2026's board never lists SGL 2027's matches.

Event-owned models are discovered from ``models/``: any class whose ``event``
foreign key to ``models.Event`` cascades. Models tagged with an event they
merely happened during (``on_delete=SET_NULL``: audit, telemetry, sent
messages…) are checked for writes only.

Flags, in a repository, a service or an entry surface (pages, theme, api,
discordbot, mcpserver):

- a list read, ``Model.filter/all/exclude/first/exists(...)``, that is not
  inside ``event_scoped(...)`` or ``live_event_scoped(...)`` and names no event (``event``/``event_id``/
  ``event__…``) and no **row key**. A row key is ``id``/``pk``, or a foreign key
  to another event-owned row (``tournament_id=…``, ``match=…``,
  ``position_id__in=…``): those rows already belong to one event, and the row
  decides its own event.

and in a repository or a service:

- a write, ``Model.create/get_or_create/update_or_create(...)``, that stamps no
  event (``get_or_create`` may carry it in ``defaults={...}``).

``get``/``get_or_none`` are by-row lookups and never flagged.

Escape hatch: a function whose source says ``cross-event`` (a personal page
across live events, a worker scan that binds each row's event) or
``event-scope: exempt — <reason>`` is skipped.

Exit 0 = clean / not applicable; exit 2 = violation (stderr explains).
"""

import ast
import json
import os
import sys

from _hook_paths import anchor

anchor()

LIST_ROOTS = {"filter", "all", "exclude", "first", "exists"}
WRITE_ROOTS = {"create", "get_or_create", "update_or_create"}
EXEMPT_MARKERS = ("cross-event", "event-scope: exempt")
ROW_KEYS = {"id", "pk"}
EVENT_KEYS = {"event", "event_id"}
SCOPERS = {"event_scoped", "live_event_scoped"}


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


def _fk_target(call: ast.AST) -> str | None:
    if not isinstance(call, ast.Call):
        return None
    name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
    if name not in ("ForeignKeyField", "OneToOneField") or not call.args:
        return None
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value.split(".")[-1]
    return None


def _on_delete(call: ast.Call) -> str:
    for kw in call.keywords:
        if kw.arg == "on_delete":
            return ast.unparse(kw.value)
    return ""


def load_models(models_dir: str) -> tuple[dict[str, dict[str, str]], set[str], set[str]]:
    """``(fks, owned, tagged)``: each model's FK fields, and the two event kinds."""
    fks: dict[str, dict[str, str]] = {}
    owned: set[str] = set()
    tagged: set[str] = set()
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
            fields: dict[str, str] = {}
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    targets, value = stmt.targets, stmt.value
                elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                    targets, value = [stmt.target], stmt.value
                else:
                    continue
                target_model = _fk_target(value)
                if target_model is None:
                    continue
                for t in targets:
                    if not isinstance(t, ast.Name):
                        continue
                    fields[t.id] = target_model
                    if t.id == "event" and target_model == "Event":
                        (owned if "CASCADE" in _on_delete(value) else tagged).add(node.name)
            fks[node.name] = fields
    return fks, owned, tagged


def _key_root(arg: str) -> str:
    return arg.split("__", 1)[0]


#: Filter builders that name an event themselves: ``role_event_q`` keeps a role
#: grant's community-wide rows plus those in one event (or a scope of events).
EVENT_FILTERS = {"role_event_q"}


def _names_event(call: ast.Call) -> bool:
    for arg in call.args:
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id in EVENT_FILTERS:
            return True
    for kw in call.keywords:
        if kw.arg and _key_root(kw.arg) in EVENT_KEYS:
            return True
        if kw.arg == "defaults" and isinstance(kw.value, ast.Dict):
            for key in kw.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and _key_root(key.value) in EVENT_KEYS:
                    return True
    return False


def _q_keywords(call: ast.Call) -> list[ast.keyword]:
    """Keywords of the ``Q(...)`` objects among a call's positional args (``Q(a) | Q(b)``)."""
    out: list[ast.keyword] = []
    for arg in call.args:
        for node in ast.walk(arg):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Q":
                out.extend(node.keywords)
    return out


def _names_row(call: ast.Call, model: str, fks: dict[str, dict[str, str]], owned: set[str]) -> bool:
    fields = fks.get(model, {})
    for kw in [*call.keywords, *_q_keywords(call)]:
        if not kw.arg:
            continue
        root = _key_root(kw.arg)
        if root in ROW_KEYS:
            return True
        field = root[:-3] if root.endswith("_id") else root
        if fields.get(field) in owned and field != "event":
            return True
    return False


def exempt_spans(tree: ast.AST, lines: list[str]) -> list[tuple[int, int]]:
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min((d.lineno for d in node.decorator_list), default=node.lineno)
            end = node.end_lineno or node.lineno
            segment = "\n".join(lines[start - 1:end]).lower()
            if any(marker in segment for marker in EXEMPT_MARKERS):
                spans.append((start, end))
    return spans


def find_violations(tree, fks, owned, tagged, spans, *, check_reads: bool, check_writes: bool):
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def inside_event_scoped(node: ast.AST) -> bool:
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name) and cur.func.id in SCOPERS:
                return True
            cur = parents.get(cur)
        return False

    def chain_names_scope(node: ast.Call, model: str) -> bool:
        # Model.filter(a=1).filter(event_id=…) or .filter(tournament_id=…)
        # Only a narrowing call counts: ``.exclude(id=x)`` names a row to drop,
        # which leaves every other event's rows in.
        cur = parents.get(node)
        while isinstance(cur, (ast.Attribute, ast.Call)):
            if (
                isinstance(cur, ast.Call)
                and isinstance(cur.func, ast.Attribute)
                and cur.func.attr in ("filter", "get", "get_or_none")
                and (_names_event(cur) or _names_row(cur, model, fks, owned))
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
        if any(start <= node.lineno <= end for start, end in spans):
            continue
        if method in WRITE_ROOTS:
            if not check_writes or (model not in owned and model not in tagged):
                continue
            if _names_event(node):
                continue
            out.append((node.lineno, model, method, True))
        elif method in LIST_ROOTS:
            if not check_reads or model not in owned:
                continue
            if inside_event_scoped(node) or _names_event(node):
                continue
            if method != "exclude" and _names_row(node, model, fks, owned):
                continue
            if chain_names_scope(node, model):
                continue
            out.append((node.lineno, model, method, False))
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
    is_test = "/tests/" in norm or norm.startswith("tests/")
    in_repository = "/application/repositories/" in norm
    in_service = "/application/services/" in norm
    in_entry = not is_test and any(
        f"/{d}/" in norm for d in ("pages", "theme", "api", "discordbot", "mcpserver")
    )
    if is_test or "/.claude/" in norm or not (in_repository or in_service or in_entry):
        sys.exit(0)
    if norm.endswith(("/_tenant.py", "/_event.py", "/__init__.py")):
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
    fks, owned, tagged = load_models(models_dir)
    if not owned:
        sys.exit(0)

    violations = find_violations(
        tree, fks, owned, tagged, exempt_spans(tree, source.splitlines()),
        check_reads=True,
        check_writes=in_repository or in_service,
    )
    if not violations:
        sys.exit(0)
    for line, model, method, is_write in violations:
        if is_write and model in tagged:
            fix = (
                f"  Tag the row: {model}.{method}(..., event_id=tagged_event_id())\n"
                f"  (from application.repositories._event import tagged_event_id)"
            )
        elif is_write:
            fix = (
                f"  Stamp the row: {model}.{method}(..., event_id=current_event_id()), or its\n"
                f"  parent's event via parent_event_id(...) (application/repositories/_event.py)"
            )
        else:
            fix = (
                f"  Wrap the queryset: event_scoped({model}.{method}(...)), or filter by a row\n"
                f"  that already belongs to one event (id=…, tournament_id=…)"
            )
        print(
            f"EVENT SCOPING VIOLATION in '{file_path}' (line {line}):\n"
            f"  {model}.{method}(...) on an event-{'tagged' if model in tagged else 'owned'} model names no event.\n"
            f"{fix}\n"
            f"  If the query deliberately spans events, say 'cross-event' or\n"
            f"  'event-scope: exempt — <reason>' in the enclosing function.",
            file=sys.stderr,
        )
    sys.exit(2)


if __name__ == "__main__":
    main()
