"""Pick seats for a match's two players — opposite sides of the room, at random.

The counterpart to :class:`StationAssignmentMixin`: that one *validates and
writes* a seating a proctor chose, this one *proposes* one. Composed into
:class:`MatchService` the same way, and it reaches ``_require_match``,
``repository`` and ``station_repository`` through that composed class.

Nothing here writes. The suggestion goes back to the check-in dialog, which
fills its pickers with it and lets the proctor override before submitting —
so the only path that touches ``MatchPlayers.assigned_station`` is still
``assign_stations``, with its existing validation, audit and event.

Two rules, in priority order:

1. **Opposite sides.** The two players do not sit in the same half of the room.
   Enforced by drawing each player from a different :class:`StationSide`.
2. **Spread out.** Within a side, prefer a station whose neighbours are empty.
   Neighbours are same side, same section, ``position`` one apart.

Both bend rather than break. A full room, a one-sided pool, or a venue that has
described no layout at all still gets a valid random seating; each rule that
could not be honoured comes back named in ``relaxations`` so the dialog can say
so instead of quietly seating two players elbow to elbow.
"""

import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from application.services.auth_service import AuthService
from models import Match, Station, StationSide, User

if TYPE_CHECKING:
    from application.repositories import MatchRepository, StationRepository


@dataclass
class StationSuggestion:
    """A proposed seating, plus what it could not manage.

    ``assignments`` is keyed by ``MatchPlayers.id`` so it can be handed straight
    to ``assign_stations``. ``relaxations`` is user-facing copy: each entry is a
    complete sentence naming a rule that did not hold.
    """

    assignments: Dict[int, str]
    relaxations: List[str] = field(default_factory=list)


def _are_neighbours(a: Station, b: Station) -> bool:
    """``a`` and ``b`` are seats next to each other.

    Requires both to carry a position and to agree on side and section: two
    stations numbered 3 and 4 on opposite walls are not neighbours, and neither
    are two whose layout nobody has filled in.
    """
    if a.position is None or b.position is None:
        return False
    if a.side != b.side or a.section != b.section:
        return False
    return abs(a.position - b.position) == 1


class StationDrawMixin:
    repository: 'MatchRepository'
    station_repository: 'StationRepository'
    _require_match: Callable[[int], Awaitable[Match]]

    async def suggest_stations(
        self,
        match_id: int,
        actor: Optional[User] = None,
    ) -> StationSuggestion:
        """Propose a station for each of this match's two players.

        Raises ``ValueError`` when the match does not have exactly two players,
        when the community has defined no station pool, or when the room cannot
        seat them both.
        """
        match = await self._require_match(match_id)

        await AuthService.ensure(
            await AuthService.can_run_match(actor, match),
            f"User cannot assign stations for match {match_id}",
        )

        if match.tournament and match.tournament.is_racetime_enabled:
            raise ValueError(
                "Station assignment is disabled for racetime.gg tournaments — "
                "players race remotely, so there are no on-site stations to assign."
            )

        players = list(match.players)  # type: ignore[attr-defined]
        if len(players) != 2:
            raise ValueError(
                "Random seating needs a match with exactly two players; this one "
                f"has {len(players)}. Assign the stations by hand."
            )

        pool = await self.station_repository.get_active()
        if not pool:
            raise ValueError(
                "This community has not defined any stations yet, so there is "
                "nothing to draw from. Add them under Admin → Settings → Venue."
            )

        occupied = await self.repository.occupied_stations(exclude_match_id=match.id)
        free = [s for s in pool if s.name not in occupied]
        if len(free) < 2:
            raise ValueError(
                "Fewer than two stations are free right now — every other one is "
                "in use by a match already in play."
            )

        taken = [s for s in pool if s.name in occupied]

        relaxations: List[str] = []
        first, second = self._draw_pair(free, taken, relaxations)

        return StationSuggestion(
            assignments={players[0].id: first.name, players[1].id: second.name},
            relaxations=relaxations,
        )

    def _draw_pair(
        self,
        free: Sequence[Station],
        taken: Sequence[Station],
        relaxations: List[str],
    ) -> Tuple[Station, Station]:
        """Two free stations, on opposite sides and spread out where possible."""
        by_side = {
            side: [s for s in free if s.side == side] for side in StationSide
        }
        sided = [side for side, group in by_side.items() if group]

        if len(sided) < 2:
            if any(s.side is not None for s in free):
                relaxations.append(
                    "Both players are on the same side of the room — the other "
                    "side has no free stations."
                )
            else:
                relaxations.append(
                    "These stations have no side recorded, so the players could "
                    "not be split across the room. Set each station's side under "
                    "Admin → Settings → Venue."
                )
            first = self._pick_spread(list(free), taken, relaxations)
            rest = [s for s in free if s.name != first.name]
            second = self._pick_spread(rest, [*taken, first], relaxations)
            return first, second

        near, far = by_side[sided[0]], by_side[sided[1]]
        if secrets.choice((True, False)):
            near, far = far, near

        first = self._pick_spread(near, taken, relaxations)
        second = self._pick_spread(far, [*taken, first], relaxations)
        return first, second

    def _pick_spread(
        self,
        candidates: Sequence[Station],
        taken: Sequence[Station],
        relaxations: List[str],
    ) -> Station:
        """A random candidate, preferring ones with no occupied neighbour.

        The tiers are "no neighbour in use" then everything else, and the
        relaxation is only recorded when the fallback tier is actually used —
        a pool with no positions at all counts zero neighbours everywhere, so
        it never trips the message.
        """
        roomy = [
            s for s in candidates
            if not any(_are_neighbours(s, t) for t in taken)
        ]
        if roomy:
            return secrets.choice(roomy)

        crowded = (
            "The room is busy enough that a player had to be seated next to "
            "another station in use."
        )
        if crowded not in relaxations:
            relaxations.append(crowded)
        return secrets.choice(list(candidates))
