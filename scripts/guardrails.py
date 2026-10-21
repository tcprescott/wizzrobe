#!/usr/bin/env python3
"""Run the repo's guardrail checks over a set of files, outside Claude Code.

``.claude/scripts/`` holds the checks that encode this project's hardest-won
invariants — three-layer boundaries, tenant scoping, async and datetime safety,
feature-flag gating, audit discipline, the mobile-grid rule. They are wired as
Claude Code hooks, which means they fire on a Write/Edit *inside a Claude
session* and nowhere else: a hand-written commit, a merge, or an edit from any
other tool sails past all of them.

This runner replays each check against files on disk so CI can enforce the same
rules for everyone. It does not reimplement any check — it synthesizes the hook
payload each one already parses and reports its exit status.

Usage::

    python scripts/guardrails.py --changed origin/main   # PR diff (CI default)
    python scripts/guardrails.py --all                   # whole tree
    python scripts/guardrails.py pages/admin.py …        # explicit paths
    python scripts/guardrails.py --all --update-baseline # re-record known debt

Exit 0 when no file exceeds its baseline count, 1 otherwise.

Baseline
--------
Checks that predate this runner have pre-existing hits (a 900-line module, a
table with no mobile grid). ``scripts/guardrail_baseline.json`` records the
per-(check, file) count at the time of adoption; the run fails only when a count
*grows*. Deleting an entry is always safe, so the baseline can only shrink
without someone deliberately re-recording it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "scripts"
BASELINE_PATH = ROOT / "scripts" / "guardrail_baseline.json"

# Per-file checks: each is handed one path at a time.
FILE_CHECKS = [
    "check_api_route_auth",
    "check_audit_actions",
    "check_audit_actor",
    "check_bare_save",
    "check_dry_regressions",
    "check_event_types",
    "check_feature_flag_gating",
    "check_file_length",
    "check_fixture_cost",
    "check_layer_exports",
    "check_markdown_xss",
    "check_native_datetime_inputs",
    "check_secret_leak",
    "check_slot_context",
    "check_steady_refresh",
    "check_syntax",
    "check_table_grid",
    "check_table_prefs",
    "check_tenant_scoping",
    "check_view_urls",
    "check_worker_loops",
    "enforce_architecture",
    "enforce_async_safety",
    "enforce_datetime_safety",
    "enforce_import_resolution",
    "enforce_no_orm_writes",
    "enforce_nicegui_client_api",
]

# Only meaningful against a diff, and only against files the diff *modified*:
# "you edited a migration that already exists" is a violation, but every migration
# in the tree trivially matches it, and a newly added one is not an edit at all —
# adding a migration is how schema changes ship. Skipped for added paths (see
# ``added_files``), mirroring the hook's own allowance for a Write to a path that
# does not yet exist.
CHANGED_ONLY_CHECKS = {"enforce_migration_safety"}

# Whole-repo checks: run once, with no file payload.
REPO_CHECKS: list[str] = []

# Deliberately not replayed here, each with the reason. Data rather than a
# comment because ``tests/test_guardrail_ci_parity.py`` reads it: every script in
# ``.claude/scripts/`` must appear in one of the lists above or in this mapping,
# so a new hook cannot quietly end up enforced in a Claude session and nowhere
# else. That is exactly how ``check_fixture_cost`` was missed for a while.
EXCLUDED_CHECKS = {
    "run_full_tests": "a test runner, not a check — CI runs pytest directly",
    "run_related_tests": "a test runner, not a check — CI runs pytest directly",
    "enforce_safe_commands": "guards Bash tool calls, which have no CI analogue",
    "check_migration_drift": (
        "reads the working tree, which is clean in CI — the `migrations` job "
        "proves the chain applies to a real PostgreSQL instead"
    ),
    "check_seed_coverage": (
        "same shape as check_migration_drift: it reads `git diff HEAD` and "
        "untracked files for newly added models, and a checkout has neither, so "
        "listing it as a repo check ran it as a guaranteed no-op — "
        "tests/test_seed_coverage.py runs the real seed instead"
    ),
}

TEXT_SUFFIXES = {".py", ".md", ".html", ".js", ".json", ".yaml", ".yml", ".toml"}


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return out.stdout if out.returncode == 0 else ""


def changed_files(base: str) -> list[str]:
    merge_base = git("merge-base", "HEAD", base).strip() or base
    names = git("diff", "--name-only", "--diff-filter=d", merge_base, "HEAD")
    return [p for p in names.splitlines() if p.strip()]


def added_files(base: str) -> set[str]:
    """Paths this diff *created*, as opposed to modified.

    The distinction matters for :data:`CHANGED_ONLY_CHECKS`: a check that means
    "you changed a file you were not supposed to change" must not fire on a file
    that did not exist before, or adding one becomes impossible.
    """
    merge_base = git("merge-base", "HEAD", base).strip() or base
    names = git("diff", "--name-only", "--diff-filter=A", merge_base, "HEAD")
    return {p for p in names.splitlines() if p.strip()}


def tracked_files() -> list[str]:
    return [p for p in git("ls-files").splitlines() if p.strip()]


_BASE_CACHE: dict[str, str] = {}


def base_content(path: str, base: str | None) -> str:
    """The file as of the merge base, or '' for a whole-tree run.

    ``check_dry_regressions`` counts occurrences in old vs new and fires only on
    a net increase, so handing it the base revision is what makes "this PR added
    another one" mean the same thing in CI as it does in an editor.
    """
    if base is None:
        return ""
    if path not in _BASE_CACHE:
        merge_base = git("merge-base", "HEAD", base).strip() or base
        _BASE_CACHE[path] = git("show", f"{merge_base}:{path}")
    return _BASE_CACHE[path]


def read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def run_hook(name: str, payload: dict) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(HOOKS / f"{name}.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
        check=False,
    )
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def check_file(name: str, path: str, base: str | None) -> tuple[int, str]:
    """Replay one check against one file as if it had just been written.

    The payload is shaped as an ``Edit`` whose ``old_string`` is the base
    revision and whose ``new_string`` is the whole current file: the
    content-reading checks see the finished file, the net-new checks see both
    sides, and the checks that read from disk are unaffected either way.

    ``old_content`` states the before side explicitly for the net-new checks.
    Without it they cannot fire in CI at all — and did not, in either mode:
    ``--all`` hands them ``old_string=""``, which they read as "the Edit itself
    will fail, nothing to judge", and under ``--changed`` the base revision is
    not a *substring* of the current file, so they bail the same way. The sweep
    reported clean over 56 real hits, including eight live ``_load_*_or_404``
    router preloads, the exact shape one of the rules forbids.
    """
    head = read(ROOT / path)
    if head is None:
        return 0, ""
    return run_hook(name, {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(ROOT / path),
            "old_string": base_content(path, base),
            "new_string": head,
            "content": head,
            "old_content": base_content(path, base),
        },
    })


def load_baseline() -> dict[str, dict[str, int]]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", help="explicit files to check")
    ap.add_argument("--all", action="store_true", help="every tracked file")
    ap.add_argument("--changed", metavar="BASE", help="files changed vs BASE")
    ap.add_argument(
        "--update-baseline", action="store_true",
        help="record the current hits as the accepted baseline",
    )
    args = ap.parse_args()

    base: str | None = None
    added: set[str] = set()
    if args.changed:
        base = args.changed
        paths = changed_files(args.changed)
        added = added_files(args.changed)
    elif args.all:
        paths = tracked_files()
    elif args.paths:
        paths = args.paths
    else:
        ap.error("pass --all, --changed BASE, or explicit paths")

    paths = [
        p for p in paths
        if Path(p).suffix in TEXT_SUFFIXES and not p.startswith(".claude/")
    ]

    checks = list(FILE_CHECKS)
    if base is not None or args.paths:
        checks += sorted(CHANGED_ONLY_CHECKS)

    hits: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    messages: list[str] = []

    # Warm the base-revision cache serially; git is not the thing to parallelize.
    for path in paths:
        base_content(path, base)

    grid = [
        (name, path)
        for name in checks
        for path in paths
        if not (name in CHANGED_ONLY_CHECKS and path in added)
    ]
    workers = min(32, (os.cpu_count() or 4) * 4)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(lambda job: check_file(job[0], job[1], base), grid)
        for (name, path), (code, out) in zip(grid, results, strict=True):
            if code != 0:
                hits[name][path] += 1
                messages.append(f"[{name}] {path}\n{out}")

    for name in REPO_CHECKS:
        code, out = run_hook(name, {})
        if code != 0:
            hits[name]["<repo>"] += 1
            messages.append(f"[{name}]\n{out}")

    plain = {k: dict(v) for k, v in hits.items()}

    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(plain, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        total = sum(sum(v.values()) for v in plain.values())
        print(f"baseline updated: {total} accepted hit(s) across {len(plain)} check(s)")
        return 0

    baseline = load_baseline()
    regressions = [
        (name, path, count)
        for name, files in plain.items()
        for path, count in files.items()
        if count > baseline.get(name, {}).get(path, 0)
    ]

    if not regressions:
        accepted = sum(sum(v.values()) for v in plain.values())
        print(
            f"guardrails: {len(paths)} file(s), {len(checks)} check(s) — clean"
            + (f" ({accepted} baselined hit(s) unchanged)" if accepted else "")
        )
        return 0

    print("\n".join(messages), file=sys.stderr)
    print("\nGuardrail regressions:", file=sys.stderr)
    for name, path, count in sorted(regressions):
        was = baseline.get(name, {}).get(path, 0)
        print(f"  {name}: {path} ({was} baselined -> {count})", file=sys.stderr)
    print(
        "\nFix the violation above. If it is genuinely accepted debt, re-record it "
        "with:\n  python scripts/guardrails.py --all --update-baseline",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
