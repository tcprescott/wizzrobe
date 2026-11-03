"""Versioned URLs for everything under ``static/``.

Every stylesheet, script and worker the app links must go through
:func:`asset_url`. The app sends ``no-cache`` on its own static responses in
development only; in production nothing between the app and the reader is
obliged to revalidate a URL that never changes, so a browser or proxy holding a
previous deploy's ``brackets.css`` renders the current markup against stale
rules — the page keeps its markup and silently loses every rule added since.
Stamping the URL with the file's mtime changes the URL exactly when the file
changes, which is what makes a deploy visible to a client that already has the
old copy.

Paired with the ``Cache-Control`` policy in :mod:`frontend`: a stamped URL is
immutable and cached for a year, an unstamped one must revalidate.
"""
from pathlib import Path

STATIC_ROOT = Path(__file__).resolve().parent.parent / 'static'

# frontend.py reads this key to mark a response immutable.
VERSION_PARAM = 'v'


CACHE_IMMUTABLE = b'public, max-age=31536000, immutable'
CACHE_REVALIDATE = b'public, max-age=0, must-revalidate'
CACHE_NO_STORE = b'no-cache, no-store, must-revalidate'


def cache_control(query_string: bytes, *, is_dev: bool) -> bytes:
    """The ``Cache-Control`` a ``/static`` response should carry.

    Development never stores, so an edit shows up on reload. Production answers
    on the stamp: a stamped URL changes when its file does and is safe to keep
    for a year, while an unstamped one is the same URL forever and must
    revalidate — left to Starlette's bare ``last-modified``, a browser caches it
    heuristically for a tenth of its age, which is how a deploy's stylesheet
    reached readers days late.
    """
    if is_dev:
        return CACHE_NO_STORE
    stamped = f'{VERSION_PARAM}='.encode() in query_string
    return CACHE_IMMUTABLE if stamped else CACHE_REVALIDATE


def asset_url(path: str) -> str:
    """``/static/<path>`` with a build stamp, e.g. ``/static/css/app.css?v=17…``.

    Falls back to the bare path when the file is missing rather than raising —
    a mistyped asset should 404 visibly, not break the page build.
    """
    try:
        stamp = int((STATIC_ROOT / path).stat().st_mtime)
    except OSError:
        return f'/static/{path}'
    return f'/static/{path}?{VERSION_PARAM}={stamp}'
