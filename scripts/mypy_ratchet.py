#!/usr/bin/env python3
"""Run mypy and fail only when a file's error count grows.

Mypy has been informational (`continue-on-error`) since it was added, which
means it never stopped anything: the count only ever went up. Making it blocking
outright is not on the table — the tree carries ~1450 errors, and most are
Tortoise reverse relations and FK ``*_id`` attributes that mypy structurally
cannot see on a dynamically-built ORM model. Those are the ORM's shape, not
defects to fix.

A ratchet gets the value without the cleanup: ``scripts/mypy_baseline.json``
records the per-file error count, and a run fails when any file exceeds its
entry or a clean file starts producing errors. Existing debt stays put; new debt
does not land. Fixing errors and re-recording makes the baseline smaller, and it
can only grow when somebody deliberately runs ``--update``.

Usage::

    python scripts/mypy_ratchet.py            # check (what CI runs)
    python scripts/mypy_ratchet.py --update   # re-record after fixing errors

Record the baseline from a **cold** ``.mypy_cache``, which is what CI always has.
A warm cache reports a couple fewer errors in the files it has already seen; that
direction is harmless (the ratchet only fails on an increase) but a baseline
recorded warm would fail the first CI run.

**Record it on the same Python CI uses (3.12).** ``--update`` re-records *every*
file from whatever interpreter you are on, not just the ones you touched — so
running it on a newer Python quietly lowers the entry for any file that reports
fewer errors there, and the next CI run fails on files nobody edited. That
happened once with ``preset_service.py`` and ``seedgen_service.py``, which report
one error fewer on 3.13 than on 3.12. If it happens again, restore those specific
entries by hand rather than re-running ``--update``: the numbers CI produces are
the authoritative ones.

It happened again, on ``preset_service.py`` again, and the sharper lesson is that
matching the *minor* version is not enough — 3.12.3 also reports one fewer there
than the runner's 3.12.13. So there is no local interpreter that reliably
reproduces CI's numbers, and **the safe move after fixing errors is to leave the
baseline alone.** A stale-high baseline costs nothing: the check only fails on an
increase, and the run prints how many fewer there are, so the improvement is
still visible and still locked against regression at the old ceiling. Re-record
only when the slack has grown large enough to be worth the round trip, and expect
to fix up a file or two from what CI reports.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "scripts" / "mypy_baseline.json"

TARGETS = ["application", "api", "middleware", "main.py", "frontend.py"]

ERROR_RE = re.compile(r"^(?P<path>[^:]+):\d+:(?:\d+:)? error: ")


def run_mypy() -> Counter[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", *TARGETS],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if proc.returncode not in (0, 1):
        print(proc.stdout or proc.stderr, file=sys.stderr)
        raise SystemExit(f"mypy failed to run (exit {proc.returncode})")

    counts: Counter[str] = Counter()
    for line in proc.stdout.splitlines():
        m = ERROR_RE.match(line)
        if m:
            counts[m.group("path").replace("\\", "/")] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--update", action="store_true",
        help="re-record the current counts as the accepted baseline",
    )
    args = ap.parse_args()

    counts = run_mypy()

    if args.update:
        BASELINE_PATH.write_text(
            json.dumps(dict(sorted(counts.items())), indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline updated: {sum(counts.values())} errors across {len(counts)} files")
        return 0

    baseline: dict[str, int] = (
        json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        if BASELINE_PATH.exists() else {}
    )

    regressions = sorted(
        (path, baseline.get(path, 0), n)
        for path, n in counts.items()
        if n > baseline.get(path, 0)
    )
    improvements = sorted(
        (path, was, counts.get(path, 0))
        for path, was in baseline.items()
        if counts.get(path, 0) < was
    )

    if regressions:
        print("Mypy regressions — these files gained type errors:\n", file=sys.stderr)
        for path, was, now in regressions:
            print(f"  {path}: {was} -> {now}", file=sys.stderr)
        print(
            "\nRun `poetry run mypy " + " ".join(TARGETS) + "` to see them.\n"
            "If an error is a Tortoise-ORM false positive mypy cannot resolve, "
            "silence it at the line with a targeted `# type: ignore[code]` rather "
            "than re-recording the baseline.",
            file=sys.stderr,
        )
        return 1

    total = sum(counts.values())
    msg = f"mypy ratchet: {total} error(s), none above baseline"
    if improvements:
        freed = sum(was - now for _, was, now in improvements)
        msg += (
            f" — {freed} fewer in {len(improvements)} file(s); "
            "re-record with `python scripts/mypy_ratchet.py --update`"
        )
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
