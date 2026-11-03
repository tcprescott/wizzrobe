"""Tournament strategy registry - Business Logic Layer.

The user-definable-tournament-logic design principle (docs/online-tournaments)
implements a **finite, named set of strategy primitives**; schema-validated
config picks and parameterizes them. Adding a new *primitive* is a reviewed code
change available to every tenant; standing up a new tournament is pure config.
**No ``eval``, ever** — config never becomes code.

This module is the register/lookup substrate for those primitives. PR 0 ships
the empty registry and its API; concrete strategies (seed source, scoring,
scheduling, reattempt rules, …) register themselves from their feature PRs, e.g.::

    from application.services.tournament_strategies import register_strategy

    @register_strategy('scoring', 'par_time')
    class ParTimeScoring:
        ...

Kinds group strategies by the facet they implement so a config value like
``{"scoring": "par_time"}`` resolves within one namespace.
"""

from typing import Callable, Dict, List, Tuple, TypeVar

T = TypeVar('T')

_REGISTRY: Dict[Tuple[str, str], object] = {}


def register_strategy(kind: str, name: str) -> Callable[[T], T]:
    """Decorator registering ``target`` as strategy ``name`` within ``kind``.

    Raises :class:`ValueError` on a duplicate ``(kind, name)`` so a collision
    fails loudly at import time rather than silently shadowing a primitive.
    """
    key = (kind, name)

    def _register(target: T) -> T:
        if key in _REGISTRY:
            raise ValueError(f"Strategy already registered: kind={kind!r} name={name!r}")
        _REGISTRY[key] = target
        return target

    return _register


def get_strategy(kind: str, name: str) -> object:
    """Look up a registered strategy, raising :class:`ValueError` if unknown."""
    try:
        return _REGISTRY[(kind, name)]
    except KeyError:
        raise ValueError(f"Unknown strategy: kind={kind!r} name={name!r}") from None


def available_strategies(kind: str) -> List[str]:
    """Names registered under ``kind`` (sorted). Empty when none are registered."""
    return sorted(name for k, name in _REGISTRY if k == kind)


__all__ = ['available_strategies', 'get_strategy', 'register_strategy']
