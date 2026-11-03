"""Archived events are read-only: the guard at the data layer.

An archived event keeps every row it owns, for anyone who could see it before,
but nothing in it may change. The rule is enforced where every surface meets,
the ORM, so a page, a REST route, an MCP tool, a Discord button and a worker
are all refused the same way without each remembering to ask:

- **Row writes.** A ``pre_save`` / ``pre_delete`` signal on every *guarded*
  model raises :class:`~application.errors.EventArchivedError` when the row
  belongs to an archived event. The row decides its own event: an event-owned
  model reads its ``event_id``; a child row (a match's players, a shift's
  assignments, a bracket's games) reads its parent's, through the first
  cascading foreign key that leads to an event-owned model.
- **Bulk writes.** ``QuerySet.update/delete`` and ``bulk_create`` skip signals,
  so a repository runs :func:`ensure_writable` on the queryset (or the parent
  rows) first. ``check_event_write_gating.py`` flags a bulk write on a guarded
  model that doesn't.

The error is a ``ValueError``: the web UI shows it as a notification, REST
answers 409 and MCP ``read_only``.

Archiving itself, and the jobs that tidy an archived event (closing race rooms,
taking back Discord roles, the 30-day purge of private data), write inside
:func:`archive_maintenance`.

Like ``feature_flags`` this is a peer of the layers rather than one of them:
repositories call :func:`ensure_writable`, and the event lookup is imported
lazily so nothing here pulls services in at import time.
"""

import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional, Tuple, cast

from tortoise.fields.relational import CASCADE
from tortoise.models import Model
from tortoise.queryset import QuerySet
from tortoise.signals import pre_delete, pre_save

import models
from application.errors import EventArchivedError
from models.enums import EventStatus

_maintenance: ContextVar[bool] = ContextVar('wizzrobe_archive_maintenance', default=False)


@contextmanager
def archive_maintenance() -> Iterator[None]:
    """Let the code inside write to archived events: archiving and its tidy-up only."""
    token = _maintenance.set(True)
    try:
        yield
    finally:
        _maintenance.reset(token)


def _is_event_fk(field: Any) -> bool:
    return getattr(field, 'model_name', None) == 'models.Event' and getattr(field, 'on_delete', None) == CASCADE


def _all_models() -> Dict[str, type[Model]]:
    return {
        name: obj for name, obj in vars(models).items()
        if inspect.isclass(obj) and issubclass(obj, Model) and obj is not Model and not obj._meta.abstract
    }


def _event_paths() -> Dict[type[Model], Tuple[Optional[str], Optional[type[Model]], str]]:
    """Every guarded model → ``(fk field, parent model, its own path to an event id)``.

    An event-owned model maps to ``(None, None, 'event_id')``: it carries its
    own. A child maps to its first cascading foreign key whose target already
    has a path, so a bracket game reaches its event through its matchup, the
    matchup's bracket and the bracket's tournament
    (``bracket_match__bracket__tournament__event_id``).
    """
    by_name = _all_models()
    paths: Dict[type[Model], Tuple[Optional[str], Optional[type[Model]], str]] = {
        model: (None, None, 'event_id')
        for model in by_name.values() if _is_event_fk(model._meta.fields_map.get('event'))
    }
    grew = True
    while grew:
        grew = False
        for model in by_name.values():
            if model in paths:
                continue
            for name in sorted(model._meta.fk_fields):
                field = model._meta.fields_map[name]
                if getattr(field, 'on_delete', None) != CASCADE:
                    continue
                parent = by_name.get(str(getattr(field, 'model_name', '')).split('.')[-1])
                if parent is None or parent not in paths:
                    continue
                paths[model] = (name, parent, f'{name}__{paths[parent][2]}')
                grew = True
                break
    return paths


_PATHS = _event_paths()
GUARDED_MODELS: frozenset[type[Model]] = frozenset(_PATHS)


def event_id_path(model: type[Model]) -> Optional[str]:
    """The lookup from ``model`` to its event id (``tournament__event_id``), or
    None for a model no event owns."""
    path = _PATHS.get(model)
    return path[2] if path else None


async def _event_id_of(instance: Model, using_db: Any) -> Optional[int]:
    fk, parent, _ = _PATHS[type(instance)]
    if fk is None:
        return getattr(instance, 'event_id', None)
    parent_id = getattr(instance, f'{fk}_id', None)
    if parent_id is None or parent is None:
        return None
    path = _PATHS[parent][2]
    query = parent.filter(id=parent_id)
    if using_db is not None:
        query = query.using_db(using_db)
    rows = await query.limit(1).values_list(path, flat=True)
    return cast(Optional[int], rows[0] if rows else None)


async def _refuse_if_archived(tenant_id: Optional[int], event_id: Optional[int]) -> None:
    if tenant_id is None or event_id is None:
        return
    from application.services.event_service import EventService

    event = await EventService.ref(tenant_id, event_id)
    if event is not None and event.is_archived:
        raise EventArchivedError(event.name)


async def _guard_save(sender: type[Model], instance: Model, using_db: Any, update_fields: Any) -> None:
    if _maintenance.get():
        return
    await _refuse_if_archived(getattr(instance, 'tenant_id', None), await _event_id_of(instance, using_db))


async def _guard_delete(sender: type[Model], instance: Model, using_db: Any) -> None:
    if _maintenance.get():
        return
    await _refuse_if_archived(getattr(instance, 'tenant_id', None), await _event_id_of(instance, using_db))


pre_save(*GUARDED_MODELS)(_guard_save)
pre_delete(*GUARDED_MODELS)(_guard_delete)


async def ensure_writable(queryset: QuerySet) -> None:
    """Refuse a bulk write when any row ``queryset`` would touch is in an archived event.

    Call it on the exact queryset about to ``.update()`` / ``.delete()``. For a
    ``bulk_create``, pass the parent rows' queryset (or use
    :func:`ensure_event_writable` with the event id the rows are stamped with).
    """
    if _maintenance.get():
        return
    model = queryset.model
    if model not in _PATHS:
        return
    prefix = _PATHS[model][2][:-len('event_id')]
    archived = await queryset.filter(**{f'{prefix}event__status': EventStatus.ARCHIVED}).limit(1).values_list(
        f'{prefix}event__name', flat=True,
    )
    if archived:
        raise EventArchivedError(str(archived[0]))


async def ensure_event_writable(event_id: Optional[int], tenant_id: Optional[int] = None) -> None:
    """Refuse when ``event_id`` (of the current community by default) is archived."""
    if _maintenance.get():
        return
    from application.tenant_context import get_current_tenant_id

    await _refuse_if_archived(tenant_id if tenant_id is not None else get_current_tenant_id(), event_id)


async def guarded_update(queryset: QuerySet, **fields: Any) -> int:
    """``queryset.update(**fields)``, refused when it would touch an archived event's rows."""
    await ensure_writable(queryset)
    return await queryset.update(**fields)


async def guarded_delete(queryset: QuerySet) -> int:
    """``queryset.delete()``, refused when it would touch an archived event's rows."""
    await ensure_writable(queryset)
    return await queryset.delete()
