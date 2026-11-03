#!/usr/bin/env python3
"""
PostToolUse hook: block an async ``@ui.refreshable`` in the presentation layer.

The bug this catches
--------------------
``ui.refreshable`` clears its container and then runs the builder. An async
builder awaits (a query) between the two, and that await sends the empty
container to the browser: the section collapses, everything below it jumps up,
and jumps back when the query returns. The Match Schedule board snapped to full
width and back on every confirm this way (PR #482), and 38 more sections did the
same until ``theme.steady.steady_refreshable`` replaced them::

    from theme.steady import steady_refreshable

    @steady_refreshable
    async def section() -> None:
        rows = await service.list_rows()
        ...

``steady_refreshable`` builds the new content off-screen and swaps it in one
step. A synchronous ``@ui.refreshable`` is fine and stays as it is.

Opt out with a ``steady-refresh: exempt`` comment on the decorator line, with
the reason. docs/style-guide.md#hold-still has the rule.

Only presentation-layer files (pages/, theme/) are inspected. Exits 2 with
guidance on a hit; exits 0 otherwise (fail open).
"""

import ast
import json
import re
import sys

from _hook_paths import anchor

anchor()

_EXEMPT_RE = re.compile(r"steady-refresh:\s*exempt", re.IGNORECASE)


def is_presentation(path: str) -> bool:
    norm = path.replace("\\", "/")
    return "/pages/" in norm or "/theme/" in norm or norm.startswith(("pages/", "theme/"))


def _is_ui_refreshable(dec: ast.expr) -> bool:
    return (
        isinstance(dec, ast.Attribute)
        and dec.attr in ("refreshable", "refreshable_method")
        and isinstance(dec.value, ast.Name)
        and dec.value.id == "ui"
    )


def check(source: str, file_path: str) -> list[str]:
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []
    lines = source.splitlines()
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if not _is_ui_refreshable(dec):
                continue
            line = lines[dec.lineno - 1] if dec.lineno <= len(lines) else ""
            if _EXEMPT_RE.search(line):
                continue
            hits.append(
                f"{file_path}:{dec.lineno}: async `{node.name}` uses @ui.refreshable, which "
                "blanks the section while it awaits and makes the page jump. Use "
                "@steady_refreshable (from theme.steady import steady_refreshable). "
                "See docs/style-guide.md#hold-still."
            )
    return hits


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path or not file_path.endswith(".py"):
        sys.exit(0)
    if "/.claude/" in file_path.replace("\\", "/") or not is_presentation(file_path):
        sys.exit(0)
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        sys.exit(0)
    violations = check(source, file_path)
    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
