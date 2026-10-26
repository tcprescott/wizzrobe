"""Guardrail: ``check_bare_save.py`` blocks new whole-row saves on existing rows.

Behavioural: each case drives the real hook script with the payload Claude Code
sends for a Write, and asserts its exit code.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "scripts" / "check_bare_save.py"
SERVICE = str(REPO / "application" / "services" / "_bare_save_probe.py")

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
    return "async def go(user):\n" + "".join(f"    {line}\n" for line in body.splitlines())


@pytest.mark.parametrize("body", [
    "user.timezone = 'UTC'\nawait user.save()",
    "await user.save(using_db=conn)",
    "user = await User.get(id=1)\nawait user.save()",
])
def test_bare_save_on_an_existing_row_is_blocked(body: str) -> None:
    assert run_hook(fn(body)) == BLOCKED


@pytest.mark.parametrize("body", [
    "await save_fields(user, 'timezone')",
    "await user.save(update_fields=['timezone'])",
    "row = Thing(tenant_id=1)\nawait row.save()",
    "canvas.save(out, 'WEBP')",
    "await user.save()  # full-save: restoring a snapshot row",
])
def test_field_saves_inserts_and_opt_outs_pass(body: str) -> None:
    assert run_hook(fn(body)) == ALLOWED


def test_an_existing_bare_save_does_not_block_unrelated_edits() -> None:
    old = fn("await user.save()")
    assert run_hook(old + "\nX = 1\n", old=old) == ALLOWED


def test_out_of_scope_paths_are_ignored() -> None:
    body = fn("await user.save()")
    assert run_hook(body, path=str(REPO / "tests" / "services" / "probe.py")) == ALLOWED
    assert run_hook(body, path=str(REPO / "scripts" / "seed_probe.py")) == ALLOWED
