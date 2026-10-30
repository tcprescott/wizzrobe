"""Guardrail: ``enforce_safe_commands.py`` blocks the right commands in the right place.

Most rows in the hook's ``RULES`` table are unconditional. The database-drop row
is not: it exists to protect a developer's real, long-lived dev database, and a
remote session (Claude Code on the web) has no such thing — it gets a fresh
container and a scratch Postgres that setup routinely tears down and rebuilds.
Enforcing it there blocks legitimate work and protects nothing.

The trigger strings are assembled from fragments so this file does not trip the
hook that reads it, and the assertions are behavioural (exit code of the real
script) so they survive a refactor of the rule table.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / ".claude" / "scripts" / "enforce_safe_commands.py"

BLOCKED = 2
ALLOWED = 0

# Split so the literals never appear whole in this file (see .claude/README.md > Gotchas).
DROP_DB = "drop" + "db wizzrobe_dev"
DROP_TABLE = 'psql -c "DROP ' + 'TABLE match"'
FORCE_DELETE = "rm " + "-rf build/"
HARD_RESET = "git reset " + "--hard HEAD~1"
DOWNGRADE = "aerich " + "downgrade"
BENIGN = "poetry run pytest tests/"


def run_hook(command: str, *, remote: bool | None) -> int:
    """Drive the real hook script the way Claude Code does, and return its exit code.

    ``remote=None`` runs with ``CLAUDE_CODE_REMOTE`` absent entirely.
    """
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
    if remote is not None:
        env["CLAUDE_CODE_REMOTE"] = "true" if remote else "false"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result.returncode


@pytest.mark.parametrize("command", [DROP_DB, DROP_TABLE])
def test_database_drop_is_blocked_locally(command: str) -> None:
    assert run_hook(command, remote=False) == BLOCKED


@pytest.mark.parametrize("command", [DROP_DB, DROP_TABLE])
def test_database_drop_is_allowed_in_a_remote_session(command: str) -> None:
    """The scratch Postgres in a web container is meant to be dropped and rebuilt."""
    assert run_hook(command, remote=True) == ALLOWED


def test_absent_remote_marker_counts_as_local() -> None:
    """Fail safe: an unset CLAUDE_CODE_REMOTE must enforce, not exempt."""
    assert run_hook(DROP_DB, remote=None) == BLOCKED


@pytest.mark.parametrize("command", [FORCE_DELETE, HARD_RESET, DOWNGRADE])
@pytest.mark.parametrize("remote", [True, False])
def test_other_destructive_rules_stay_unconditional(command: str, remote: bool) -> None:
    """Only the database-drop row is local-only — the exemption must not spread."""
    assert run_hook(command, remote=remote) == BLOCKED


@pytest.mark.parametrize("remote", [True, False])
def test_ordinary_commands_pass(remote: bool) -> None:
    assert run_hook(BENIGN, remote=remote) == ALLOWED


# `-f` process matching kills the shell that runs it: `-f` matches full command
# lines, and that shell has the whole command — pattern included — in its own.
# The pattern is always there, because it is the argument, so this is a
# certainty rather than a risk. The shell dies (exit 143/144, no output) and the
# intended target keeps running.
#
# Assembled from fragments for the same reason as the constants above.
_KILL = "pk" + "ill"
_FIND = "pg" + "rep"

KILL_QUOTED = f'{_KILL} -f "uvicorn main:app"'
KILL_BARE = f"{_KILL} -f uvicorn"
# Naming a different process does not save it: the pattern is still in the
# command line of the shell doing the matching.
KILL_OTHER_PROCESS = f"{_KILL} -f some-other-daemon"
# The bracket "trick" that looks like it dodges the match and does not.
FIND_THEN_KILL = f"for pid in $({_FIND} -f 'uvi[c]orn'); do kill $pid; done"

# Safe: looking is harmless — it only becomes the hazard when piped into kill.
FIND_ONLY = f"{_FIND} -af 'uvicorn' | head"
# Safe: no -f at all, so it matches process *names*, not full command lines.
BY_NAME = f"{_KILL} uvicorn"
STOP_SCRIPT = "./start.sh stop"
# Safe: prose, not a command. Writing a doc or a test about this hazard must not
# trip the hook that guards it — which it did, on the first attempt.
IN_A_COMMENT = f"echo hello  # never run {_KILL} -f uvicorn here"


@pytest.mark.parametrize(
    "command", [KILL_QUOTED, KILL_BARE, KILL_OTHER_PROCESS, FIND_THEN_KILL]
)
@pytest.mark.parametrize("remote", [True, False])
def test_pattern_matching_process_kills_are_blocked(command: str, remote: bool) -> None:
    assert run_hook(command, remote=remote) == BLOCKED


@pytest.mark.parametrize("command", [FIND_ONLY, BY_NAME, STOP_SCRIPT, IN_A_COMMENT])
@pytest.mark.parametrize("remote", [True, False])
def test_harmless_process_lookups_pass(command: str, remote: bool) -> None:
    assert run_hook(command, remote=remote) == ALLOWED
