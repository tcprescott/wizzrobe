"""Discord embed builders for notification DMs.

Parallel to :mod:`application.utils.discord_messages` (which produces the
plain-text version). Every notification is *sent* as a Discord embed — a
colour-coded card with a community-name footer and native ``<t:…>`` timestamps
that render in each viewer's own timezone with a live countdown. The plain-text
string still flows through ``send_dm`` for the web-push mirror and the mock, so
this module is purely additive: it never replaces the text, it enriches the
Discord representation.

Kept separate from ``discord_messages`` so the text builders (imported by the
REST layer, tests, etc.) don't pull in ``discord`` just to format a string.
"""

from datetime import datetime
from typing import Optional, Sequence

import discord

# Colour by category / match state (0xRRGGBB). Warm-tuned toward the app's
# phoenix/status palette where it doesn't fight Discord's own semantics.
COLOR_SCHEDULED = 0x5865F2    # blurple — informational / new
COLOR_RESCHEDULED = 0xB45309  # burnt amber — needs attention
COLOR_CHECKED_IN = 0xB45309   # amber — imminent
COLOR_STARTED = 0x557A1F      # olive green — live
COLOR_FINISHED = 0x6B6258     # warm gray — done
COLOR_CONFIRMED = 0x0E7470    # teal — settled
COLOR_STREAM = 0xE0A82E       # gold — highlight
COLOR_CREW = 0x5865F2         # blurple
COLOR_SEED = 0x8250DF         # purple
COLOR_VOLUNTEER = 0x0E7470    # teal
COLOR_JOIN_REQUEST = 0x5865F2  # blurple — informational, like a new assignment
COLOR_CANCELLED = 0x9B2C2C    # deep red — called off, nothing to attend
COLOR_STAGE = 0xBE185D        # rose — go somewhere else, and soon

_STATE_COLORS = {
    'Started': COLOR_STARTED,
    'Finished': COLOR_FINISHED,
    'Confirmed': COLOR_CONFIRMED,
}

# A field tuple is (name, value, inline). Empty values are dropped.
Field = tuple


def _players_value(player_names: Optional[Sequence[str]]) -> str:
    """"A vs B" for two, comma-joined otherwise, '' for none."""
    if not player_names:
        return ''
    names = list(player_names)
    if len(names) == 2:
        return ' vs '.join(names)
    return ', '.join(names)


def time_field(when: Optional[datetime]) -> str:
    """A Discord timestamp that renders in the viewer's own timezone plus a live
    relative countdown: ``<t:unix:F> · <t:unix:R>``. '' when ``when`` is None."""
    if when is None:
        return ''
    ts = int(when.timestamp())
    return f"<t:{ts}:F> · <t:{ts}:R>"


def notification_embed(
    *,
    title: str,
    color: int,
    community_name: Optional[str] = None,
    description: Optional[str] = None,
    fields: Optional[Sequence[Field]] = None,
    url: Optional[str] = None,
) -> discord.Embed:
    """The shared card shape: title, colour, optional description, fields, and a
    community-name footer. Fields with an empty value are skipped."""
    embed = discord.Embed(title=title, colour=color)
    if description:
        embed.description = description
    if url:
        embed.url = url
    for name, value, inline in (fields or []):
        if value:
            embed.add_field(name=name, value=value, inline=inline)
    if community_name:
        embed.set_footer(text=community_name)
    return embed


def match_embed(
    *,
    title: str,
    color: int,
    tournament: str,
    community_name: Optional[str] = None,
    player_names: Optional[Sequence[str]] = None,
    when: Optional[datetime] = None,
    stage_name: Optional[str] = None,
    description: Optional[str] = None,
    url: Optional[str] = None,
    code: Optional[str] = None,
) -> discord.Embed:
    """Embed for a match notification: Tournament / Players / Time / Code / Stage."""
    fields: list[Field] = [('Tournament', tournament, True)]
    players = _players_value(player_names)
    if players:
        fields.append(('Players', players, True))
    if when is not None:
        fields.append(('Time', time_field(when), False))
    if code:
        fields.append(('Code', code, False))
    if stage_name:
        fields.append(('Stage', stage_name, True))
    return notification_embed(
        title=title, color=color, community_name=community_name,
        description=description, fields=fields, url=url,
    )


def state_changed_embed(
    tournament: str,
    new_state: str,
    *,
    community_name: Optional[str] = None,
    player_names: Optional[Sequence[str]] = None,
    when: Optional[datetime] = None,
    stage_name: Optional[str] = None,
) -> discord.Embed:
    """Started / Finished / Confirmed transition card."""
    emoji = {'Started': '🔴', 'Finished': '🏁', 'Confirmed': '☑️'}.get(new_state, '•')
    label = 'Match result confirmed' if new_state == 'Confirmed' else f'Match {new_state.lower()}'
    return match_embed(
        title=f"{emoji} {label}",
        color=_STATE_COLORS.get(new_state, COLOR_SCHEDULED),
        tournament=tournament, community_name=community_name,
        player_names=player_names, when=when, stage_name=stage_name,
    )


def matchup_ready_embed(
    *,
    tournament: str,
    round_name: str,
    opponent: str,
    community_name: Optional[str] = None,
    best_of: int = 1,
    schedule_url: Optional[str] = None,
    rebook: bool = False,
) -> discord.Embed:
    """Card for the "you have a matchup to schedule" DM (D7).

    ``COLOR_SCHEDULED`` for a fresh matchup and ``COLOR_RESCHEDULED`` for a
    released one, matching what those two colours already mean for a plain match
    — the palette is one vocabulary, not two.
    """
    fields: list[Field] = [
        ('Tournament', tournament, True),
        ('Round', round_name, True),
        ('Opponent', opponent, False),
    ]
    if best_of > 1:
        fields.append(('Format', f'Best of {best_of}', True))
    return notification_embed(
        title='🔁 Matchup ready to reschedule' if rebook else '🗓️ Matchup ready to schedule',
        color=COLOR_RESCHEDULED if rebook else COLOR_SCHEDULED,
        community_name=community_name,
        description=(
            'This game was called off — pick a new time below.'
            if rebook else 'Agree a time with your opponent, then book it below.'
        ),
        fields=fields,
        url=schedule_url,
    )


def stage_embed(
    *,
    title: str,
    tournament: str,
    community_name: Optional[str] = None,
    player_names: Optional[Sequence[str]] = None,
    when: Optional[datetime] = None,
    stage_name: Optional[str] = None,
    description: Optional[str] = None,
) -> discord.Embed:
    """Card for the three stage DMs: assigned, cleared, and the reminder.

    Its own colour rather than ``COLOR_SCHEDULED`` or ``COLOR_RESCHEDULED``:
    neither the time nor the fixture changed, where to stand did, and that is
    what the recipient has to spot in a stack of match cards.
    """
    return match_embed(
        title=title, color=COLOR_STAGE, tournament=tournament,
        community_name=community_name, player_names=player_names, when=when,
        stage_name=stage_name, description=description,
    )


def volunteer_embed(
    *,
    title: str,
    position: str,
    community_name: Optional[str] = None,
    starts: Optional[datetime] = None,
    ends: Optional[datetime] = None,
    description: Optional[str] = None,
    handover: str = '',
) -> discord.Embed:
    """Embed for a volunteer shift: Position / Start / End, then the handover line."""
    fields: list[Field] = [('Position', position, False)]
    if starts is not None:
        fields.append(('Start', time_field(starts), True))
    if ends is not None:
        fields.append(('End', time_field(ends), True))
    # A field needs a name; a zero-width one shows the line without a heading.
    fields.append(('\u200b', handover, False))
    return notification_embed(
        title=title, color=COLOR_VOLUNTEER, community_name=community_name,
        description=description, fields=fields,
    )
