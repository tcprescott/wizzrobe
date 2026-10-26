"""Guardrail: every check in ``.claude/scripts/`` is accounted for in CI.

The hooks fire on a Write/Edit inside a Claude session and nowhere else, so
``scripts/guardrails.py`` replays them against files on disk for everyone else.
A check that exists but is not listed there is enforced for exactly one author
on exactly one tool — the failure mode is silent, because nothing goes red.

So the invariant is coverage, not correctness: each script must be replayed, or
be named in ``EXCLUDED_CHECKS`` with a reason. Adding a hook without touching
either fails here.
"""

import ast
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "scripts"
SETTINGS = REPO / ".claude" / "settings.json"
README = REPO / ".claude" / "README.md"


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "_guardrails_runner", REPO / "scripts" / "guardrails.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _script_stems() -> set[str]:
    return {
        p.stem
        for p in SCRIPTS.glob("*.py")
        if not p.stem.startswith("_")
    }


def _settings_stems() -> set[str]:
    config = json.loads(SETTINGS.read_text())
    commands = [
        hook["command"]
        for event in config["hooks"].values()
        for group in event
        for hook in group.get("hooks", [])
        if hook.get("type") == "command"
    ]
    return {
        Path(token.strip('"')).stem
        for command in commands
        for token in command.split()
        if "/.claude/scripts/" in token
    }


def test_scripts_directory_is_not_empty() -> None:
    """Guard the guard: an empty listing would make everything below vacuous."""
    assert len(_script_stems()) > 10


def test_every_check_is_replayed_or_excluded() -> None:
    runner = _load_runner()
    covered = (
        set(runner.FILE_CHECKS)
        | set(runner.CHANGED_ONLY_CHECKS)
        | set(runner.REPO_CHECKS)
        | set(runner.EXCLUDED_CHECKS)
    )
    missing = sorted(_script_stems() - covered)
    assert not missing, (
        "These .claude/scripts/ checks are neither replayed by "
        "scripts/guardrails.py nor listed in its EXCLUDED_CHECKS, so they bind "
        f"Claude sessions only and never CI: {missing}"
    )


def test_runner_names_no_check_that_does_not_exist() -> None:
    runner = _load_runner()
    named = (
        set(runner.FILE_CHECKS)
        | set(runner.CHANGED_ONLY_CHECKS)
        | set(runner.REPO_CHECKS)
        | set(runner.EXCLUDED_CHECKS)
    )
    stale = sorted(named - _script_stems())
    assert not stale, (
        f"scripts/guardrails.py names checks with no script on disk: {stale}"
    )


def test_every_check_is_wired_as_a_hook() -> None:
    """The other half: a script CI replays but no hook runs is dead in-session."""
    orphans = sorted(_script_stems() - _settings_stems())
    assert not orphans, (
        "These .claude/scripts/ checks are not wired into .claude/settings.json, "
        f"so they never fire during a Claude session: {orphans}"
    )


# Where each check must be wired. A set is not enough: moving every PostToolUse
# check into the PreToolUse group leaves the stems identical while breaking all
# sixteen, because an AST check would then read the pre-edit file — or, for a
# Write to a new path, no file at all — and silently pass.
EXPECTED_WIRING = {
    "check_api_route_auth": {("PostToolUse", "Write|Edit")},
    "check_audit_actions": {("PostToolUse", "Write|Edit")},
    "check_audit_actor": {("PostToolUse", "Write|Edit")},
    "check_bare_save": {("PreToolUse", "Write|Edit")},
    "check_dry_regressions": {("PreToolUse", "Write|Edit")},
    "check_event_scoping": {("PostToolUse", "Write|Edit")},
    "check_event_types": {("PostToolUse", "Write|Edit")},
    "check_feature_flag_gating": {("PostToolUse", "Write|Edit")},
    "check_file_length": {("PostToolUse", "Write|Edit")},
    "check_fixture_cost": {("PreToolUse", "Write|Edit")},
    "check_layer_exports": {("PostToolUse", "Write|Edit")},
    "check_markdown_xss": {("PostToolUse", "Write|Edit")},
    "check_migration_drift": {("Stop", None)},
    "check_native_datetime_inputs": {("PreToolUse", "Write|Edit")},
    "check_secret_leak": {("PostToolUse", "Write|Edit")},
    "check_seed_coverage": {("Stop", None)},
    "check_slot_context": {("PostToolUse", "Write|Edit")},
    "check_syntax": {("PostToolUse", "Write|Edit")},
    "check_table_grid": {("PostToolUse", "Write|Edit")},
    "check_table_prefs": {("PostToolUse", "Write|Edit")},
    "check_view_urls": {("PostToolUse", "Write|Edit")},
    "check_steady_refresh": {("PostToolUse", "Write|Edit")},
    "check_tenant_scoping": {("PostToolUse", "Write|Edit")},
    "check_worker_loops": {("PreToolUse", "Write|Edit")},
    "enforce_architecture": {("PreToolUse", "Write|Edit")},
    "enforce_async_safety": {("PreToolUse", "Write|Edit")},
    "enforce_datetime_safety": {("PreToolUse", "Write|Edit")},
    # Two modes on purpose: per-edit feedback, plus the whole-repo Stop sweep
    # that is the only one able to catch a cross-file import break.
    "enforce_import_resolution": {("PostToolUse", "Write|Edit"), ("Stop", None)},
    "enforce_migration_safety": {("PreToolUse", "Write|Edit")},
    "enforce_nicegui_client_api": {("PreToolUse", "Write|Edit")},
    "enforce_no_orm_writes": {("PostToolUse", "Write|Edit")},
    "enforce_safe_commands": {("PreToolUse", "Bash")},
    "run_full_tests": {("Stop", None)},
    "run_related_tests": {("PostToolUse", "Write|Edit")},
}


def _wiring() -> dict[str, set[tuple[str, str | None]]]:
    config = json.loads(SETTINGS.read_text())
    found: dict[str, set[tuple[str, str | None]]] = {}
    for event, groups in config["hooks"].items():
        for group in groups:
            matcher = group.get("matcher")
            for hook in group.get("hooks", []):
                if hook.get("type") != "command":
                    continue
                for token in hook.get("command", "").split():
                    if "/.claude/scripts/" in token:
                        stem = Path(token.strip('"')).stem
                        found.setdefault(stem, set()).add((event, matcher))
    return found


def test_every_check_is_wired_on_the_right_event() -> None:
    assert _wiring() == EXPECTED_WIRING, (
        "A check's event or matcher changed. If that was deliberate, update "
        "EXPECTED_WIRING here and the '(EVENT: MATCHER)' annotation in its "
        ".claude/README.md heading — a check on the wrong event fails silently."
    )


def _int_literal(script: str, name: str) -> int:
    tree = ast.parse((SCRIPTS / script).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return int(node.value.value)  # type: ignore[attr-defined]
    raise AssertionError(f"{script} no longer defines {name}")


def _hook_timeout(stem: str) -> int:
    config = json.loads(SETTINGS.read_text())
    for groups in config["hooks"].values():
        for group in groups:
            for hook in group.get("hooks", []):
                if f"/{stem}.py" in hook.get("command", ""):
                    return int(hook["timeout"])
    raise AssertionError(f"{stem} is not wired in settings.json")


def test_test_runners_time_out_before_the_harness_kills_them() -> None:
    """The runner must reach its own timeout branch, not be killed mid-run.

    Both runners treat a pytest timeout as a failure — "result UNKNOWN; this is
    not a pass" — and exit 2. That branch is only reachable if pytest gives up
    before the harness does. Raise PYTEST_TIMEOUT past the hook's timeout and
    the harness kills the process first, which reads as a silent pass: exactly
    the failure the branch was written to prevent.
    """
    for stem in ("run_related_tests", "run_full_tests"):
        internal = _int_literal(f"{stem}.py", "PYTEST_TIMEOUT")
        harness = _hook_timeout(stem)
        assert harness - internal >= 10, (
            f"{stem}: pytest gets {internal}s but the harness kills the hook at "
            f"{harness}s, so its own timeout branch can never run. Raise the "
            f"timeout in .claude/settings.json alongside PYTEST_TIMEOUT."
        )


def _tuple_literal(script: str, name: str) -> set[str]:
    """Read a module-level tuple of strings without importing the script.

    The scripts call ``anchor()`` at import time, which chdirs — importing one
    into the test process would move every later test's relative path.
    """
    tree = ast.parse((SCRIPTS / script).read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            continue
        return {
            e.value
            for e in node.value.elts  # type: ignore[attr-defined]
            if isinstance(e, ast.Constant) and isinstance(e.value, str)
        }
    raise AssertionError(f"{script} no longer defines {name}")


def test_presentation_surface_agrees_across_checks() -> None:
    """One word, one meaning: both checks must guard the same surface.

    ``enforce_architecture`` has always counted api/, discordbot/ and mcpserver/
    as presentation, while ``enforce_no_orm_writes`` guarded only pages/ and
    theme/ — so a direct ``Match.filter(...).update(...)`` in a REST router was
    flagged by neither, though the identical line in pages/ was rejected.
    """
    architecture = _tuple_literal("enforce_architecture.py", "PRESENTATION_MODULES")
    # enforce_no_orm_writes matches frontend.py by filename, not by directory.
    orm_writes = _tuple_literal("enforce_no_orm_writes.py", "PRESENTATION_DIRS") | {
        "frontend"
    }
    assert architecture == orm_writes, (
        "enforce_architecture and enforce_no_orm_writes disagree about which "
        f"directories are presentation: {architecture ^ orm_writes}"
    )


def test_every_check_is_documented() -> None:
    """The third leg: a check nobody can read about is a check nobody maintains.

    ``.claude/README.md`` is where an author goes to learn what already fires
    before writing a new rule — so an undocumented check gets re-implemented, or
    silently worked around when it blocks an edit and its message is the only
    explanation on offer. Four checks had drifted out of it before this test.
    """
    prose = README.read_text()
    undocumented = sorted(s for s in _script_stems() if s not in prose)
    assert not undocumented, (
        "These .claude/scripts/ checks are wired up but absent from "
        f".claude/README.md, so nothing tells an author they exist: {undocumented}"
    )
