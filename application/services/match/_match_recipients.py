"""Shared DM-recipient resolution for a match.

``collect_match_recipients`` is needed both by the lifecycle half of
``match_schedule_service`` and by the notification mixin split out of it, so it
lives here rather than in either — keeping the two modules free of a cycle.
"""

from typing import NamedTuple

from application.tenant_context import require_tenant_id
from application.utils.discord_messages import (
    AUDIENCE_CREW,
    AUDIENCE_PLAYER,
    AUDIENCE_WATCHER,
    CREW_COMMENTATOR,
    CREW_TRACKER,
)
from models import Commentator, Match, MatchPlayers, MatchWatcher, Tracker, User


class MatchRecipient(NamedTuple):
    """Why one person is hearing about a match, and whether they get Unwatch.

    ``audience`` is the strongest tie they have to it — player, then crew, then
    watcher — because that decides what the copy can truthfully say ("your
    match" is only true for a player) and where its button can send them.
    ``is_watcher`` is independent: a player who also watches still gets the
    Unwatch button they asked for. ``crew_roles`` names the slots a crew
    recipient holds (``'commentator'``, ``'tracker'``, or both), so their copy
    can say which one is theirs.
    """

    audience: str
    is_watcher: bool
    crew_roles: tuple[str, ...] = ()


def _dm_opt_ok(user: User) -> bool:
    """Whether a user can be notified at all: they have a discord_id.

    The Discord on/off switch is enforced inside ``send_dm``, so a user with
    Discord DMs off still gets the device-notification mirror.
    """
    return bool(user.discord_id)


async def collect_match_recipients(
    match: Match,
    *,
    include_players: bool = True,
    include_watchers: bool = True,
    exclude_players: bool = False,
    include_unstaged_crew: bool = False,
) -> dict[int, bool]:
    """``{discord_id: is_watcher}`` — :func:`collect_match_audience` without the audience."""
    audience = await collect_match_audience(
        match,
        include_players=include_players,
        include_watchers=include_watchers,
        exclude_players=exclude_players,
        include_unstaged_crew=include_unstaged_crew,
    )
    return {discord_id: r.is_watcher for discord_id, r in audience.items()}


async def collect_match_audience(
    match: Match,
    *,
    include_players: bool = True,
    include_watchers: bool = True,
    exclude_players: bool = False,
    include_unstaged_crew: bool = False,
) -> dict[int, MatchRecipient]:
    """Return ``{discord_id: MatchRecipient}`` for a match's DM recipients.

    Players, approved commentators, and approved trackers are collected as
    non-watchers; a watcher flips ``is_watcher`` on (they get the unwatch-button
    DM) without demoting a player or crew member's audience. Insertion order is
    players → commentators → trackers → watchers, and each discord_id appears
    once.

    - ``include_players``: add players to the recipient set.
    - ``include_watchers``: add match watchers (with the watcher flag).
    - ``exclude_players``: drop any crew/watcher who is also a player (used by the
      crew notification, where players get a separate acknowledgment DM).
    - ``include_unstaged_crew``: keep crew on a match with no stage. Crew hear
      about a match only while it is on a stage: off stage nobody is
      broadcasting it, so its updates are noise to them. A crew member who also watches still gets the watcher copy.
      Set it for the stage-cleared DM (they were told they're on stage) and for
      de-duplicating follower copies.
    """
    tenant_id = require_tenant_id()
    recipients: dict[int, MatchRecipient] = {}

    players = await MatchPlayers.filter(
        match=match, tenant_id=tenant_id
    ).prefetch_related('user')
    player_discord_ids: set[int] = {
        mp.user.discord_id for mp in players if mp.user.discord_id
    }

    def _blocked(user: User) -> bool:
        return exclude_players and user.discord_id in player_discord_ids

    if include_players:
        for mp in players:
            if _dm_opt_ok(mp.user):
                recipients.setdefault(
                    mp.user.discord_id, MatchRecipient(AUDIENCE_PLAYER, False),
                )

    def _add_crew(user: User, role: str) -> None:
        if not _dm_opt_ok(user) or _blocked(user):
            return
        held = recipients.get(user.discord_id)
        if held is None:
            recipients[user.discord_id] = MatchRecipient(AUDIENCE_CREW, False, (role,))
        elif held.audience == AUDIENCE_CREW:
            recipients[user.discord_id] = held._replace(crew_roles=(*held.crew_roles, role))

    crew_hears = include_unstaged_crew or match.stage_id is not None  # type: ignore[attr-defined]

    commentators = [] if not crew_hears else await Commentator.filter(
        match=match, approved=True, tenant_id=tenant_id
    ).prefetch_related('user')
    for c in commentators:
        _add_crew(c.user, CREW_COMMENTATOR)

    trackers = [] if not crew_hears else await Tracker.filter(
        match=match, approved=True, tenant_id=tenant_id
    ).prefetch_related('user')
    for t in trackers:
        _add_crew(t.user, CREW_TRACKER)

    if include_watchers:
        watchers = await MatchWatcher.filter(
            match=match, tenant_id=tenant_id
        ).prefetch_related('user')
        for w in watchers:
            if _dm_opt_ok(w.user) and not _blocked(w.user):
                held = recipients.get(w.user.discord_id)
                recipients[w.user.discord_id] = (
                    held._replace(is_watcher=True) if held
                    else MatchRecipient(AUDIENCE_WATCHER, True)
                )

    return recipients

