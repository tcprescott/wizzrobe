#!/usr/bin/env python3
"""
PreToolUse hook: block new bare ``.save()`` calls on rows that already exist.

A bare ``obj.save()`` writes every column from the instance it is handed. When
that instance was loaded earlier — an edit dialog's tournament, a page's
``user``, a bracket match read before a parallel result advanced into it — the
save quietly puts back whatever anyone else changed in the meantime. That is
how the tournament editor's Save wiped start.gg settings (PR #535), and two
semifinals reported together could each blank the other's slot in the final.

Write only what you changed instead:

    await save_fields(obj, 'state', 'winner')   # application/repositories/_save.py
    await obj.save(update_fields=[...])         # equivalent, if you need using_db
    await Model.filter(id=...).update(...)      # when there is no instance

Not flagged: a ``.save()`` on a name the same function built with a model
constructor (``row = Thing(...)``, an insert), a ``.save(...)`` with positional
arguments (PIL, file objects), and any line carrying ``# full-save:`` with a
reason, for the rare case that really must write the whole row.

Counts net-new occurrences, so editing around an existing one never blocks.

Exit 0 = clean / not applicable; exit 2 = a new bare save (stderr explains).
"""

import ast
import json
import os
import sys

from _hook_paths import anchor

anchor()  # hooks inherit the session's shell cwd; pin paths to the repo

SCOPED_DIRS = ("application", "api", "discordbot", "mcpserver", "middleware", "models", "racetimebot")
OPT_OUT = "# full-save:"


def _norm(path: str) -> str:
    return "/" + path.replace("\\", "/").lstrip("/")


def in_scope(norm: str) -> bool:
    if "/tests/" in norm or "/migrations/" in norm or "/.claude/" in norm:
        return False
    return any(f"/{d}/" in norm for d in SCOPED_DIRS)


def _constructed_names(fn: ast.AST) -> set[str]:
    """Names bound in ``fn`` to ``SomeModel(...)``: a fresh instance, so its save inserts."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        ctor = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if not ctor[:1].isupper():
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def bare_saves(source: str) -> list[int]:
    """Line numbers of ``.save()`` calls that would write a whole existing row."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    out: list[int] = []
    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    seen: set[int] = set()
    # Innermost function first, so a nested function's constructors are judged in it.
    for fn in sorted(scopes, key=lambda n: n.end_lineno - n.lineno):
        fresh = _constructed_names(fn)
        for node in ast.walk(fn):
            if id(node) in seen or not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "save":
                continue
            seen.add(id(node))
            if node.args or any(k.arg == "update_fields" for k in node.keywords):
                continue
            if isinstance(func.value, ast.Name) and func.value.id in fresh:
                continue
            line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            if OPT_OUT in line:
                continue
            out.append(node.lineno)
    return out


def proposed_content(payload: dict) -> tuple[str, str, str] | None:
    """Return (file_path, old_content, new_content), or None to fail open."""
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path or not file_path.endswith(".py"):
        return None

    try:
        with open(file_path, encoding="utf-8") as fh:
            old = fh.read()
    except OSError:
        old = ""

    # A CI replay (scripts/guardrails.py) states the before side explicitly; see
    # check_dry_regressions.proposed_content for why.
    if "old_content" in tool_input:
        new = tool_input.get("new_string") or tool_input.get("content") or ""
        return file_path, tool_input.get("old_content") or "", new

    if tool_name == "Write":
        return file_path, old, tool_input.get("content", "")
    if tool_name == "Edit":
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        if not old_string or old_string not in old:
            return None
        count = -1 if tool_input.get("replace_all") else 1
        return file_path, old, old.replace(old_string, new_string, count)
    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    result = proposed_content(payload)
    if result is None:
        sys.exit(0)
    file_path, old, new = result
    if not in_scope(_norm(os.path.abspath(file_path))):
        sys.exit(0)

    before, after = bare_saves(old), bare_saves(new)
    if len(after) <= len(before):
        sys.exit(0)

    print(
        f"STALE-SAVE RISK in '{file_path}' (line(s) {', '.join(map(str, after))}):\n"
        f"  A bare .save() writes every column from this instance, putting back\n"
        f"  anything changed since it was loaded (PR #535: the tournament editor\n"
        f"  wiped start.gg settings this way).\n"
        f"  Fix: await save_fields(obj, 'field', ...) from application/repositories/_save.py,\n"
        f"  obj.save(update_fields=[...]), or Model.filter(...).update(...).\n"
        f"  A save that truly must write the whole row: add '{OPT_OUT} <reason>' to the line.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
