#!/usr/bin/env python3
"""
PreToolUse hook: block re-introduction of copy-paste patterns the 2026-07
code-quality audit removed.

Each rule names a shared primitive that replaced a repeated shape; writing the
shape again is drift back toward the audited debt. Because a few legacy
occurrences of some shapes still exist, every rule counts **net-new**
occurrences: the proposed file content is compared against the current on-disk
content, and the write is blocked only when it *increases* the number of
matches. Editing around a legacy occurrence, or rewriting a legacy file
verbatim, never blocks; introducing a new occurrence anywhere always does.

Exit 0 = no net-new occurrence (or not applicable); exit 2 = a killed pattern
is being re-introduced (stderr names the shared primitive to use instead).
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Callable

from _hook_paths import anchor

anchor()  # hooks inherit the session's shell cwd; pin paths to the repo


def _norm(path: str) -> str:
    return "/" + path.replace("\\", "/").lstrip("/")


def in_repositories(norm: str) -> bool:
    return "/application/repositories/" in norm and os.path.basename(norm) != "_base.py"


def in_services(norm: str) -> bool:
    return "/application/services/" in norm


def in_api_routers(norm: str) -> bool:
    return "/api/routers/" in norm


def in_tests(norm: str) -> bool:
    return "/tests/" in norm and os.path.basename(norm) not in {
        "conftest.py", "factories.py", "api_helpers.py",
    }


def in_presentation(norm: str) -> bool:
    if "/tests/" in norm or os.path.basename(norm) == "notify.py":
        return False
    return "/pages/" in norm or "/theme/" in norm or norm.endswith("frontend.py")


def anywhere(norm: str) -> bool:
    return os.path.basename(norm) != "environment.py" and "/tests/" not in norm


@dataclass
class Rule:
    name: str
    applies: Callable[[str], bool]
    pattern: re.Pattern
    message: str


RULES = [
    Rule(
        "repo-update-loop",
        in_repositories,
        re.compile(r"for\s+\w+\s*,\s*\w+\s+in\s+\w+\.items\(\)\s*:[\s\S]{0,80}?setattr\("),
        "Hand-rolled setattr update loop in a repository.\n"
        "  Use TenantScopedRepository.update (application/repositories/_base.py) —\n"
        "  the audit removed 20 copies of this loop (audit §2A.2).",
    ),
    Rule(
        "router-preload-404",
        in_api_routers,
        re.compile(r"def\s+_load\w*_or_404\b"),
        "A _load_*_or_404 preload helper in a router.\n"
        "  Raise NotFoundError in the service instead (application/errors.py,\n"
        "  require_found) — ServiceErrorRoute maps it to 404, so the router preload\n"
        "  and its double-fetch are unnecessary (audit §2B.1/§2B.2).",
    ),
    Rule(
        "service-not-found-valueerror",
        in_services,
        re.compile(r"raise\s+ValueError\(\s*f?['\"][^'\"\n]*[Nn]ot found"),
        "Inline `raise ValueError('… not found')` in a service.\n"
        "  Use require_found(obj, 'Label') / NotFoundError (application/errors.py) —\n"
        "  the API layer then 404s correctly instead of 400 (audit §2A.6).",
    ),
    Rule(
        "permission-error-amber",
        in_presentation,
        re.compile(
            r"except[^\n]*PermissionError[^\n]*:[\s\S]{0,300}?"
            r"ui\.notify\((?:[^()]|\([^()]*\))*color\s*=\s*['\"]warning['\"]"
        ),
        "A PermissionError painted amber.\n"
        "  theme/notify.py exists to prevent this inversion: a PermissionError is a\n"
        "  refusal and shows red (`negative`); amber is for a user-facing ValueError.\n"
        "  Call notify_error(e) — it reads the actual exception type at runtime, so\n"
        "  it is also the right fix for `except (ValueError, PermissionError)`\n"
        "  (audit §T4.4).",
    ),
    Rule(
        "env-truthiness-literal",
        anywhere,
        re.compile(r"\.lower\(\)\s*in\s*[\(\[]\s*['\"](?:1|true|yes|on)['\"]"),
        "A literal truthy-env comparison.\n"
        "  Use env_flag(name) (application/utils/environment.py) — the single\n"
        "  canonical truthy grammar; two coexisting grammars caused the\n"
        "  MOCK_SPEEDGAMING=on split-brain (audit §3.2/§2A.19).",
    ),
    Rule(
        "not-implemented-error",
        lambda n: in_services(n) or "/application/repositories/" in n,
        re.compile(r"raise\s+NotImplementedError\b"),
        "`raise NotImplementedError` in the service/repository layer.\n"
        "  It escapes ServiceErrorRoute and every UI handler as a 500/unhandled\n"
        "  exception. Raise ValueError('… not yet implemented') — the documented\n"
        "  user-error contract — or keep the feature unselectable (audit §1.3).",
    ),
    Rule(
        "audit-publish-pair",
        lambda n: in_services(n) and os.path.basename(n) != "audit_service.py",
        # Bounded by the enclosing function rather than a short character
        # window: MatchService.create_match hid its pair behind ~800 chars of
        # notification code, so a 400-char window let it through.
        re.compile(
            r"write_log\((?:(?!\n[ \t]*(?:async[ \t]+)?def[ \t])[\s\S]){0,6000}?"
            r"event_bus\.publish\("
        ),
        "Hand-rolled `write_log(...)` followed by `event_bus.publish(...)`.\n"
        "  Use AuditService.write_and_publish(actor, action, details, event_type)\n"
        "  (application/services/audit_service.py). When the event needs extra\n"
        "  routing keys the audit row doesn't carry, pass event_extra={...}; when it\n"
        "  needs a wholly different payload, pass event_details={...}\n"
        "  (2026-07 engineering-practices audit §A1).",
    ),
    Rule(
        "local-audit-emit-wrapper",
        lambda n: in_services(n) and os.path.basename(n) != "audit_service.py",
        re.compile(r"def\s+_audit_and_(?:emit|publish|write)\w*\s*\("),
        "A private _audit_and_* wrapper reimplementing the shared pairing.\n"
        "  Build this domain's detail dict, then delegate to\n"
        "  AuditService.write_and_publish — race_room_service carried exactly this\n"
        "  local copy (2026-07 engineering-practices audit §A1).",
    ),
    Rule(
        "test-factory-redefinition",
        in_tests,
        re.compile(r"def\s+(?:utc|make_user|_user)\s*\("),
        "Local re-definition of a shared test factory.\n"
        "  Import utc / make_user from tests.factories — local copies drifted into\n"
        "  an incompatible utc() signature once already (audit §2D.6).",
    ),
    Rule(
        "test-fixture-redefinition",
        in_tests,
        re.compile(r"def\s+(?:app|two_tenants|two_tenant_api|stub_discord_queue|bypass_auth)\s*\("),
        "Local re-definition of a hoisted conftest fixture.\n"
        "  app / two_tenants / two_tenant_api / stub_discord_queue / bypass_auth live\n"
        "  in tests/conftest.py (audit §2D.2–2D.5). Delete the local copy.",
    ),
]


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

    # A CI replay states both sides explicitly, because the "before" side is not
    # on disk to read: in a sweep the file already contains its violations, so
    # comparing it against itself nets zero and the check reports clean. An
    # empty string is the honest baseline for a whole-tree run — every
    # occurrence is net-new against nothing. This short-circuits the Edit
    # substring test below, which a replay can never satisfy: a whole base
    # revision is not a substring of the file it became. Absent for a real tool
    # call, where the on-disk file IS the before side.
    if "old_content" in tool_input:
        new = tool_input.get("new_string") or tool_input.get("content") or ""
        return file_path, tool_input.get("old_content") or "", new

    if tool_name == "Write":
        return file_path, old, tool_input.get("content", "")
    if tool_name == "Edit":
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        if not old_string or old_string not in old:
            return None  # the Edit itself will fail; nothing to judge
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

    norm = _norm(file_path)
    if "/.claude/" in norm:
        sys.exit(0)

    violations = []
    for rule in RULES:
        if not rule.applies(norm):
            continue
        old_n = len(rule.pattern.findall(old))
        new_n = len(rule.pattern.findall(new))
        if new_n > old_n:
            violations.append(
                f"DRY REGRESSION ({rule.name}) in '{file_path}':\n  {rule.message}"
            )

    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(
            "  (Net-new check: pre-existing occurrences in this file do not block —\n"
            "  only adding another one does.)",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
