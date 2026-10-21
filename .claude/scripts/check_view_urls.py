#!/usr/bin/env python3
"""
PostToolUse hook: block a presentation-layer view switcher that the address bar
does not follow.

The gap this catches
--------------------
A ``ui.tabs`` or segmented ``ui.toggle`` that swaps what a page shows redraws it
without loading a new page. Unless it tells ``theme/url_state.py``, the address
bar and the browser tab's title stay where the page was first loaded: Back
leaves the page instead of undoing the switch, a reload lands on the wrong view,
and a link someone copies opens somewhere else. It looks fine in review and in
every test that only clicks forward.

So a new switcher either says where it lives in the address or says why not::

    from theme.url_state import bind_url

    tabs = ui.tabs()
    ...
    bind_url('view', tabs, default='list', label=...)      # a view: Back steps
    bind_url('show', chips, default='all', history='replace')  # a filter

or ``url_param(key, element, ...)`` for a view the control does not drive by its
value alone. A toggle that is a form input rather than a view (a setting, a
choice inside a dialog) carries ``# url-state: exempt — <reason>`` on its line or
the line above.

Detection
---------
AST, whole file. Every ``ui.tabs(...)`` / ``ui.toggle(...)`` (or a subclass call
whose name ends in ``Toggle``) is compliant when its assigned name is the second
argument of a ``bind_url``/``url_param`` call in the same file, or an exempt
comment sits on the construction line or the line above. Fails open on anything
unparseable or outside pages/ and theme/.
"""

import ast
import json
import re
import sys

from _hook_paths import anchor

anchor()  # hooks inherit the session's shell cwd; pin paths to the repo

_EXEMPT_RE = re.compile(r"url-state:\s*exempt|noqa:\s*url-state", re.IGNORECASE)
_BINDERS = {"bind_url", "url_param"}


def is_presentation(path: str) -> bool:
    norm = path.replace("\\", "/")
    return "/pages/" in norm or "/theme/" in norm or norm.startswith(("pages/", "theme/"))


def _is_switcher(node: ast.Call) -> bool:
    fn = node.func
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "ui":
        return fn.attr in {"tabs", "toggle"}
    return isinstance(fn, ast.Name) and fn.id.endswith("Toggle")


def _seg(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ""


def check(source: str, file_path: str) -> list[str]:
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []

    parents: dict = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    bound: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name in _BINDERS:
            bound.add(_seg(source, node.args[1]))

    lines = source.splitlines()
    violations: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_switcher(node)):
            continue
        window = "\n".join(lines[max(0, node.lineno - 2): node.end_lineno or node.lineno])
        if _EXEMPT_RE.search(window):
            continue
        # The name the switcher is bound to: `x = ui.toggle(...)`, `with ui.tabs() as x:`,
        # or the base of a chained `x = ui.toggle(...).props(...)`.
        cur, target = node, None
        while cur is not None and target is None:
            parent = parents.get(cur)
            if isinstance(parent, ast.Assign) and parent.targets:
                target = _seg(source, parent.targets[0])
            elif isinstance(parent, ast.AnnAssign):
                target = _seg(source, parent.target)
            elif isinstance(parent, ast.withitem) and parent.optional_vars is not None:
                target = _seg(source, parent.optional_vars)
            elif isinstance(parent, ast.stmt) or parent is None:
                break
            cur = parent
        if target is not None and target in bound:
            continue
        violations.append(
            f"VIEW SWITCHER WITHOUT AN ADDRESS in '{file_path}' (line {node.lineno}):\n"
            f"  This ui.tabs/ui.toggle swaps what the page shows, but the address bar\n"
            f"  and title don't follow it, so Back, reload and shared links lose the\n"
            f"  view. Bind it (theme/url_state.py):\n"
            f"    bind_url('view', switch, default='list')              # a view\n"
            f"    bind_url('show', chips, default='all', history='replace')  # a filter\n"
            f"  or url_param(...) when the view isn't driven by the value alone.\n"
            f"  A toggle that is a form input, not a view, gets a\n"
            f"  `# url-state: exempt — <reason>` comment."
        )
    return violations


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
