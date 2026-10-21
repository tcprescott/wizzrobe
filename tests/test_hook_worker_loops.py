"""Guardrail: ``check_worker_loops.py`` blocks new hand-rolled worker loops.

Behavioural: each case drives the real hook script with the payload Claude Code
sends for a Write, and asserts its exit code.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "scripts" / "check_worker_loops.py"
SERVICE = str(REPO / "application" / "services" / "_worker_loop_probe.py")

BLOCKED = 2
ALLOWED = 0


def run_hook(content: str, path: str = SERVICE, old: str = "") -> int:
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": path, "content": content, "new_string": content, "old_content": old},
    }
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode


def fn(body: str) -> str:
    return "async def go():\n" + "".join(f"    {line}\n" for line in body.splitlines())


@pytest.mark.parametrize("body", [
    "while True:\n    await tick()\n    await asyncio.sleep(60)",
    "while not stopped:\n    try:\n        await tick()\n    except Exception:\n        pass\n    await asyncio.sleep(5)",
])
def test_a_periodic_while_loop_is_blocked(body: str) -> None:
    assert run_hook(fn(body)) == BLOCKED


@pytest.mark.parametrize("body", [
    "_loop = run_worker_loop(_tick, 60, 'my worker', logger)",
    "for _ in range(3):\n    await asyncio.sleep(1)",
    "while True:\n    if await ready():\n        break\n    await asyncio.sleep(1)",
    "while True:\n    if await ready():\n        return\n    await asyncio.sleep(1)",
    "while True:  # worker-loop: reconnects with its own backoff\n    await asyncio.sleep(1)",
    "while True:\n    await queue.get()",
])
def test_helpers_bounded_loops_and_opt_outs_pass(body: str) -> None:
    assert run_hook(fn(body)) == ALLOWED


def test_the_helpers_themselves_are_exempt() -> None:
    path = str(REPO / "application" / "utils" / "background_loop.py")
    assert run_hook(fn("while True:\n    await asyncio.sleep(1)"), path=path) == ALLOWED


def test_tests_are_out_of_scope() -> None:
    path = str(REPO / "tests" / "test_probe.py")
    assert run_hook(fn("while True:\n    await asyncio.sleep(1)"), path=path) == ALLOWED


def test_an_existing_loop_does_not_block_an_edit() -> None:
    existing = fn("while True:\n    await asyncio.sleep(1)")
    assert run_hook(existing + "\nX = 1\n", old=existing) == ALLOWED
