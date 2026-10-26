#!/usr/bin/env python3
"""
PreToolUse hook: block new hand-rolled background worker loops.

A periodic worker built as ``while True: ...; await asyncio.sleep(n)`` in a task
of its own gets none of the supervision the shared helpers give: no tick time
limit, no restart when the task dies, no ``worker.tick_*`` Sentry metrics, no
row in ``runtime_stats``, and the worker watchdog never sees it. If it hangs or
dies, it does so silently. Build it on the helper instead:

    from application.utils.background_loop import run_worker_loop
    _loop = run_worker_loop(_tick, TICK_SECONDS, 'my worker', logger)

    from application.utils.coroutine_queue import CoroutineQueue   # a FIFO of calls

Flagged: a ``while`` loop inside an ``async def`` whose body awaits
``asyncio.sleep(...)``, the shape of a periodic worker. Not flagged: the helpers
themselves, a loop with a ``break`` or ``return`` in it (a bounded retry or
poll), and any ``while`` line carrying ``# worker-loop:`` with a reason, for a
loop that supervises itself (a bot connection with its own reconnect backoff).

Counts net-new occurrences, so editing around an existing one never blocks.

Exit 0 = clean / not applicable; exit 2 = a new worker loop (stderr explains).
"""

import ast
import json
import os
import sys

from _hook_paths import anchor

anchor()  # hooks inherit the session's shell cwd; pin paths to the repo

SCOPED_DIRS = ("application", "api", "discordbot", "mcpserver", "middleware", "racetimebot")
HELPERS = ("/application/utils/background_loop.py", "/application/utils/coroutine_queue.py")
OPT_OUT = "# worker-loop:"


def _norm(path: str) -> str:
    return "/" + path.replace("\\", "/").lstrip("/")


def in_scope(norm: str) -> bool:
    if "/tests/" in norm or "/migrations/" in norm or "/.claude/" in norm:
        return False
    if any(norm.endswith(h) for h in HELPERS):
        return False
    return any(f"/{d}/" in norm for d in SCOPED_DIRS)


def _is_asyncio_sleep(node: ast.AST) -> bool:
    if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute) and func.attr == "sleep"
        and isinstance(func.value, ast.Name) and func.value.id == "asyncio"
    )


def _own_nodes(loop: ast.While):
    """Nodes in the loop body, not descending into nested functions."""
    stack = list(loop.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def worker_loops(source: str) -> list[int]:
    """Line numbers of ``while`` loops shaped like a hand-rolled periodic worker."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    out: list[int] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.While):
                continue
            body = list(_own_nodes(node))
            if not any(_is_asyncio_sleep(n) for n in body):
                continue
            if any(isinstance(n, (ast.Break, ast.Return)) for n in body):
                continue
            line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            if OPT_OUT in line:
                continue
            if node.lineno not in out:
                out.append(node.lineno)
    return sorted(out)


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

    before, after = worker_loops(old), worker_loops(new)
    if len(after) <= len(before):
        sys.exit(0)

    print(
        f"HAND-ROLLED WORKER LOOP in '{file_path}' (line(s) {', '.join(map(str, after))}):\n"
        f"  A `while` + `await asyncio.sleep(...)` loop in its own task has no tick time\n"
        f"  limit, no restart if it dies, no Sentry metrics, no runtime_stats row, and the\n"
        f"  worker watchdog can't see it, so it can hang or die silently.\n"
        f"  Fix: _loop = run_worker_loop(_tick, TICK_SECONDS, 'name', logger)\n"
        f"  (application/utils/background_loop.py), or a CoroutineQueue for a FIFO of calls.\n"
        f"  A loop that supervises itself: add '{OPT_OUT} <reason>' to the while line.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
