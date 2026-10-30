"""Bracket engines — pairing/progression logic behind the ``bracket_format``
strategy kind (docs/features/brackets.md).

Importing this package registers every engine with the shared
:mod:`application.services.tournament_strategies` registry (a side effect of
importing each engine module below). Resolve one with :func:`get_bracket_engine`.

The engines are pure structural code (no ORM); the service layer owns
persistence. See :mod:`.base` for the contract.
"""

# Engines self-register at import; auto-importing every sibling means a new engine file needs no edit here.
import importlib as _importlib
import pkgutil as _pkgutil

from application.services.tournament_strategies import (
    available_strategies,
    get_strategy,
    register_strategy,
)

from .base import (
    GeneratedMatch,
    GenerativeEngine,
    PairingEngine,
    PairingPlayer,
    Slot,
    next_power_of_two,
    standard_seeding,
)

for _mod in _pkgutil.iter_modules(__path__):
    if _mod.name != 'base' and not _mod.name.startswith('_'):
        _importlib.import_module(f'{__name__}.{_mod.name}')
del _importlib, _pkgutil, _mod

_STRATEGY_KIND = 'bracket_format'


def get_bracket_engine(fmt: str) -> object:
    """Resolve the engine registered for a ``BracketFormat`` value (its ``.value``)."""
    return get_strategy(_STRATEGY_KIND, fmt)


def available_bracket_formats() -> list[str]:
    """The bracket-format engine names currently registered."""
    return available_strategies(_STRATEGY_KIND)


__all__ = [
    'GeneratedMatch',
    'GenerativeEngine',
    'PairingEngine',
    'PairingPlayer',
    'Slot',
    'available_bracket_formats',
    'get_bracket_engine',
    'next_power_of_two',
    'register_strategy',
    'standard_seeding',
]
