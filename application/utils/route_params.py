"""Parsing ids out of public URLs.

A spectator link pasted with a trailing character or an extra digit is the input
the public routes see most. Typed as ``int`` in the route signature, the first
kind answers FastAPI's raw 422 JSON, and the second reaches Postgres as a value
an ``INT`` column cannot hold and comes back as a 500 (plus a Sentry event per
bad link). Both are just "no such page", so these routes take the segment as a
string and parse it here.

Pure, so it unit-tests without a request.
"""

from typing import Any, Optional

MAX_INT_ID = 2_147_483_647


def parse_route_id(raw: Any) -> Optional[int]:
    """``raw`` as a primary key, or ``None`` when it cannot name a row.

    Digits only — no sign, no whitespace, no ``1e3`` — and within ``1 ..
    MAX_INT_ID``. Anything else is a link that cannot resolve, and the caller
    answers it with its ordinary not-found page.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() and len(raw) <= 10:
        value = int(raw)
    else:
        return None
    return value if 1 <= value <= MAX_INT_ID else None
