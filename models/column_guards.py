"""Column-fit guards — reject a value that cannot be stored, as a ``ValueError``.

Two whole classes of production 500s originate at the same place: a value that
is *valid Python* but does not fit its column.

* A string longer than its ``CharField(max_length=N)``. Tortoise catches this
  itself, but raises :class:`tortoise.exceptions.ValidationError`, which is
  **not** a ``ValueError`` — so it sails past every ``except ValueError`` in the
  UI and past ``ServiceErrorRoute``'s ``ValueError -> 400``, and surfaces as a
  500 (REST) or a silently-dropped edit (a NiceGUI event handler).
* An integer outside its column's range. ``IntField`` is Postgres ``INT``
  (32-bit) and does not range-check at all, so the value reaches the driver and
  comes back as an ``asyncpg`` ``DataError`` — again a 500.

Neither is reproducible under the test suite's SQLite backend: SQLite has no
``VARCHAR`` length enforcement and stores 64-bit integers, so the suite is blind
to both.

The fix is one guard installed on every field, using Tortoise's own
``Field.validators`` extension point, so it holds for all models — including
future ones — without 62 hand-written checks. Two details are load-bearing:

* The guard raises :class:`FieldValueError`, a plain ``ValueError``. Tortoise's
  ``Field.validate`` re-wraps anything deriving from its own ``ValidationError``
  (losing the subclass), so raising a ``ValueError`` is what lets the error reach
  callers intact — and a ``ValueError`` is exactly what every entry surface
  already handles: a ``ui.notify`` on the web, a 400 over REST, an
  ``invalid_request`` tool error over MCP.
* The built-in ``MaxLengthValidator`` is *replaced* rather than supplemented, so
  a too-long string raises once, with our type and message.

This is a backstop, not a licence to skip validation: a service that knows a
sensible bound (a match duration, a shift's slot count) should still say so, with
a message that means something to the reader. What this guarantees is that a
bound nobody wrote still fails as a user error rather than as a crash.
"""

from __future__ import annotations

from typing import Any, Iterator, Optional

from tortoise.fields.base import Field
from tortoise.fields.data import IntField
from tortoise.models import Model
from tortoise.validators import MaxLengthValidator


class FieldValueError(ValueError):
    """A value that does not fit its column (too long, or out of range)."""


def _label(field_name: str) -> str:
    """``asset_number`` -> ``Asset number`` for the user-facing message."""
    return field_name.replace('_', ' ').capitalize()


class MaxLengthGuard:
    """Reject a string longer than the column's ``max_length``."""

    def __init__(self, max_length: int, label: str) -> None:
        self.max_length = max_length
        self.label = label

    def __call__(self, value: Any) -> None:
        if value is None:
            return
        length = len(value)
        if length > self.max_length:
            raise FieldValueError(
                f'{self.label} must be at most {self.max_length} characters '
                f'(got {length}).'
            )


class IntRangeGuard:
    """Reject an integer outside the column's storable range."""

    def __init__(self, low: int, high: int, label: str) -> None:
        self.low = low
        self.high = high
        self.label = label

    def __call__(self, value: Any) -> None:
        # bool is an int subclass and always fits; None is left to nullability.
        if value is None or isinstance(value, bool) or not isinstance(value, int):
            return
        if value < self.low or value > self.high:
            raise FieldValueError(
                f'{self.label} must be between {self.low} and {self.high} '
                f'(got {value}).'
            )


def _int_bounds(field: Field) -> Optional[tuple[int, int]]:
    """The ``(ge, le)`` range an int column declares, if it declares one.

    Read off ``Field.constraints`` rather than hard-coded, so ``SmallIntField``
    / ``IntField`` / ``BigIntField`` (and ``IntEnumField``, which narrows the
    range further) each get their own true bounds.
    """
    constraints = getattr(field, 'constraints', None) or {}
    low, high = constraints.get('ge'), constraints.get('le')
    if isinstance(low, int) and isinstance(high, int):
        return low, high
    return None


def _guard_field(field_name: str, field: Field) -> None:
    label = _label(field_name)

    max_length = getattr(field, 'max_length', None)
    if isinstance(max_length, int) and max_length > 0:
        field.validators = [
            v for v in field.validators if not isinstance(v, MaxLengthValidator)
        ]
        if not any(isinstance(v, MaxLengthGuard) for v in field.validators):
            field.validators.append(MaxLengthGuard(max_length, label))

    if isinstance(field, IntField):
        bounds = _int_bounds(field)
        if bounds and not any(isinstance(v, IntRangeGuard) for v in field.validators):
            field.validators.append(IntRangeGuard(*bounds, label))


def install_column_guards(namespace: dict) -> None:
    """Install the guards on every field of every ``Model`` in ``namespace``.

    Called once from ``models/__init__`` with its own module globals, after every
    model has been imported. Idempotent — re-running never double-installs.
    """
    for model in _models_in(namespace):
        for field_name, field in model._meta.fields_map.items():
            if isinstance(field, Field):
                _guard_field(field_name, field)


def _models_in(namespace: dict) -> Iterator[type[Model]]:
    seen: set[int] = set()
    for obj in namespace.values():
        if (
            isinstance(obj, type)
            and issubclass(obj, Model)
            and obj is not Model
            and id(obj) not in seen
        ):
            seen.add(id(obj))
            yield obj
