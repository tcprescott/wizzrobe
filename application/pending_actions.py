"""Actions held for a few seconds so the person who took them can undo them.

A one-tap result or crew approval has no confirm step; Undo in its toast is the
way back. So the write and everything it sets off (bracket advance, bracket-site
writeback, DMs, webhooks) waits here until the window closes, and an undone
action never leaves the app.

Server-side on purpose: closing the tab mid-window still commits, and every
board can show the action as on its way (:func:`pending_label`). A shutdown
commits whatever is still held (:func:`flush_all`) rather than dropping it.
A crash inside the window loses it, which the single worker accepts.

No NiceGUI import, like :mod:`application.events.match_live`: services and
presentation both reach it.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import secrets
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from typing import Optional

from application.events import match_live

logger = logging.getLogger(__name__)

UNDO_SECONDS = 8.0

Commit = Callable[[], Awaitable[None]]
OnError = Callable[[Exception], Awaitable[None]]


@dataclass
class _Held:
    key: Hashable
    label: str
    match_id: Optional[int]
    commit: Commit
    on_error: Optional[OnError]
    task: Optional[asyncio.Task] = None
    context: contextvars.Context = field(default_factory=contextvars.copy_context)


_held: dict[str, _Held] = {}
_by_key: dict[Hashable, str] = {}


def hold(
    key: Hashable,
    label: str,
    commit: Commit,
    *,
    match_id: Optional[int] = None,
    on_error: Optional[OnError] = None,
    seconds: float = UNDO_SECONDS,
) -> str:
    """Run ``commit`` after ``seconds`` unless :func:`cancel` gets there first.

    ``key`` names the thing being changed, so two taps on it can't both be held.
    ``label`` is what boards show meanwhile ("Andy wins"). ``match_id`` nudges
    open views of that match when the hold starts and ends. ``on_error`` hears a
    commit the service refused, since the toast already told the person it
    worked. Returns the token :func:`cancel` and :func:`commit_now` take.
    """
    if key in _by_key:
        raise ValueError('That change is already on its way. Wait a few seconds or undo it.')
    token = secrets.token_urlsafe(9)
    held = _Held(key=key, label=label, match_id=match_id, commit=commit, on_error=on_error)
    _held[token] = held
    _by_key[key] = token
    held.task = asyncio.get_running_loop().create_task(
        _after(token, seconds), name=f'pending: {label}', context=held.context,
    )
    _nudge(held)
    return token


def cancel(token: str) -> bool:
    """Undo a held action. False when it already committed (or never existed)."""
    held = _release(token)
    if held is None:
        return False
    if held.task is not None:
        held.task.cancel()
    _nudge(held)
    return True


async def commit_now(token: str) -> bool:
    """Commit a held action without waiting out its window."""
    held = _release(token)
    if held is None:
        return False
    if held.task is not None:
        held.task.cancel()
    await _commit(held)
    return True


def pending_label(key: Hashable) -> Optional[str]:
    token = _by_key.get(key)
    return _held[token].label if token else None


def is_pending(token: str) -> bool:
    return token in _held


async def flush_all() -> None:
    """Commit everything still held. Called once at shutdown."""
    for token in list(_held):
        await commit_now(token)


def _release(token: str) -> Optional[_Held]:
    held = _held.pop(token, None)
    if held is not None:
        _by_key.pop(held.key, None)
    return held


async def _after(token: str, seconds: float) -> None:
    await asyncio.sleep(seconds)
    held = _release(token)
    if held is not None:
        await _commit(held)


async def _commit(held: _Held) -> None:
    async def run() -> None:
        try:
            await held.commit()
        except (ValueError, PermissionError) as e:
            logger.info('held action %r refused on commit: %s', held.label, e)
            if held.on_error is not None:
                try:
                    await held.on_error(e)
                except Exception:
                    logger.exception('held action %r: error callback failed', held.label)
        except Exception:
            logger.exception('held action %r failed on commit', held.label)
        finally:
            _nudge(held)

    # The holder's tenant and event scope, whoever ends up running it.
    await asyncio.get_running_loop().create_task(run(), context=held.context.copy())


def _nudge(held: _Held) -> None:
    if held.match_id is not None:
        match_live.publish(held.match_id)
