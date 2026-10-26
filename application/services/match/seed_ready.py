"""When players may tap Ready for seed, and when their taps release it.

Pure predicates over a match with ``tournament`` and ``players`` loaded, shared
by :class:`~application.services.match.seed_ready_service.SeedReadyService`,
the roll in ``MatchScheduleService`` and the board rows, so the button, the
refusal and the roll all ask the same question.
"""

from typing import Optional

from models import Match, User


def seed_ready_open(match: Match) -> bool:
    """Whether the players of ``match`` can mark themselves ready right now.

    Only on a tournament that lets players release their own seed, once a
    proctor has checked the match in and until a seed exists. A racetime
    tournament's room attaches its own seed and skips check-in, so it never is.
    """
    tournament = match.tournament
    if tournament is None or not getattr(tournament, 'players_release_seed', False):
        return False
    if tournament.is_racetime_enabled:
        return False
    return (
        match.seated_at is not None
        and match.finished_at is None
        and getattr(match, 'generated_seed_id', None) is None
    )


def ready_count(match: Match) -> tuple[int, int]:
    """``(ready, players)`` for the "1 of 2 ready" line."""
    players = list(match.players)  # type: ignore[attr-defined]
    return sum(1 for p in players if p.seed_ready_at is not None), len(players)


def players_may_release(match: Match, actor: Optional[User]) -> bool:
    """Whether ``actor``'s tap is the one that sends the seed: they play the
    match, it is open for Ready, and every player has tapped it."""
    if actor is None or not seed_ready_open(match):
        return False
    players = list(match.players)  # type: ignore[attr-defined]
    if not players or not any(p.user_id == actor.id for p in players):
        return False
    return all(p.seed_ready_at is not None for p in players)
