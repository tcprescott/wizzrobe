"""Request-time table-preference context — what every customized table reads.

Which columns a person wants on a table is stored in Postgres, but a table build
is **synchronous**: ``customize_table`` runs inside slot-registration code, and a
page hosting a dozen tables cannot afford a DB read per table.

**Resolution is async and hits the DB; reading it back is sync and free.** The
resolver (:meth:`application.services.TablePreferenceService.prime`) runs once at
page build, loading the signed-in user's whole preference set in one query, and
the caller binds the answer here with :func:`table_prefs_scope`. Each
``customize_table`` call then reads it back synchronously.

``None`` means *unprimed*, and unprimed means *the shipped defaults*. There is no
fallback lookup, no client stash and no lazy DB read — a synchronous render must
never issue a query. A surface that does not prime (``/platform``, the public
bracket views, a test) simply gets the defaults, which is a correct answer rather
than a failure, so nothing logs.

This module is a peer of ``application/services`` (like ``application/events``
and ``application/timezone_context``) and is import-safe from every layer.
"""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional

_prefs_var: ContextVar[Optional[dict[str, dict]]] = ContextVar(
    'wizzrobe_table_prefs', default=None)


def current_table_prefs() -> Optional[dict[str, dict]]:
    """The primed preference set, or ``None`` when nothing primed it."""
    return _prefs_var.get()


def table_prefs_for(table_key: str) -> Optional[dict]:
    """One table's stored config, or ``None`` (⇒ defaults)."""
    prefs = _prefs_var.get()
    if not prefs:
        return None
    config = prefs.get(table_key)
    return config if isinstance(config, dict) and config else None


def set_table_prefs(prefs: Optional[dict[str, dict]]) -> Token:
    """Bind a preference set, returning a token for :func:`reset_table_prefs`."""
    return _prefs_var.set(prefs)


def reset_table_prefs(token: Token) -> None:
    """Restore the preference set the matching :func:`set_table_prefs` replaced."""
    _prefs_var.reset(token)


@contextmanager
def table_prefs_scope(prefs: Optional[dict[str, dict]]) -> Iterator[None]:
    """Rebind a captured preference set for the duration of the block.

    Needed wherever a table is built outside the page-build task that primed it:
    a lazily-built tab (the websocket handler has its own context) and any
    background refresh. Miss one and the table renders correctly on load, then
    reverts to default columns the first time the user switches tabs or refreshes.

    Passing ``None`` deliberately unbinds, restoring default-column behaviour.
    """
    token = _prefs_var.set(prefs)
    try:
        yield
    finally:
        _prefs_var.reset(token)
