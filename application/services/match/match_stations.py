"""Which physical station each player sits at, and what is already taken.

Split out of ``match_service`` the way :class:`MatchReviewMixin` and
:class:`CancellationMixin` are: ``StationAssignmentMixin`` is composed into
:class:`MatchService` and reaches ``_require_match``, ``repository``,
``station_repository`` and ``audit_service`` through that composed class.

The validation here is the interesting part. A station has to match the
community's configured format, be one it has actually defined, and not already
be occupied by a match in play — and the assignment's *keys* have to be this
match's player-row ids, because the near-miss is a user id and applying the
subset that happens to match would report success for a write that changed
nothing.
"""

from typing import Optional

from application.events import EventType, match_live
from application.repositories._save import save_fields
from application.services.audit_service import AuditActions
from application.services.auth_service import AuthService
from application.services.match import _station_copy
from application.services.system_config_service import SystemConfigService
from models import STATION_REGEXES, Match, User


class StationAssignmentMixin:
    async def assign_stations(
        self,
        match_id: int,
        assignments: dict,
        actor: Optional[User] = None,
    ) -> Match:
        """Set MatchPlayers.assigned_station for one or more players.

        Args:
            match_id: Match to update.
            assignments: Mapping of MatchPlayers.id -> station string (or None).
            actor: User performing the assignment.
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

        fmt = await SystemConfigService.get_station_format()
        pattern = STATION_REGEXES[fmt]
        pool = await self.station_repository.active_names()
        occupied = await self.repository.occupied_stations(exclude_match_id=match.id)

        requested = [s for s in assignments.values() if s]

        # 1. Two players of the same match cannot share a station.
        duplicates = {s for s in requested if requested.count(s) > 1}
        if duplicates:
            raise ValueError(
                f"Station {sorted(duplicates)[0]} is assigned to more than one "
                "player in this match."
            )

        for station in requested:
            # 2. Format (unchanged behaviour, and the only check when no pool
            #    is defined).
            if not pattern.fullmatch(station):
                raise ValueError(
                    f"Station '{station}' does not match the required format ({fmt.value})"
                )
            # 3. Must be a real station, once this community has defined any.
            if pool and station not in pool:
                raise ValueError(
                    f"'{station}' is not one of this community's stations."
                )
            # 4. Not already in use by another match in play. Named rather than
            #    numbered, and resolved only here so the happy path keeps its
            #    query count.
            if station in occupied:
                name = await _station_copy.name_one(self.repository, occupied[station])
                raise ValueError(f"Station {station} is in use by {name}.")

        # Keys are MatchPlayers ids, and the confusion is with User ids. Without
        # this an assignment naming only unknown ids applies nothing and still
        # reports success, audits and publishes — a silent no-op is the worst
        # answer to give a caller that got the id space wrong.
        unknown = set(assignments) - {p.id for p in match.players}
        if unknown:
            raise ValueError(
                f"No player of this match has id {', '.join(str(i) for i in sorted(unknown))}. "
                "Station keys are the match's player-row ids, not user ids."
            )

        for player in match.players:
            if player.id in assignments:
                player.assigned_station = assignments[player.id]
                await save_fields(player, 'assigned_station')

        await self.audit_service.write_and_publish(
            actor,
            AuditActions.MATCH_STATIONS_ASSIGNED,
            {
                'match_id': match.id,
                'assignments': {str(k): v for k, v in assignments.items()},
            },
            EventType.MATCH_STATIONS_ASSIGNED,
            event_extra={'tournament_id': match.tournament_id},
        )

        match_live.publish(match.id)

        return match

    async def occupied_stations_for_dialog(self, match_id: int) -> dict:
        """``{station: the match sitting at it}`` — a *name*, not an id.

        The read-through the station picker needs: presentation must not reach a
        repository directly, and the dialog has to label an occupied station
        without offering this match's own stations as taken.
        """
        return await _station_copy.label_occupied(
            self.repository,
            await self.repository.occupied_stations(exclude_match_id=match_id),
        )
