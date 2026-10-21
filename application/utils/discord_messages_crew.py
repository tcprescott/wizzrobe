"""Discord Message Templates: match crew

The DM copy for :mod:`application.services.crew_service` and the ephemeral
replies the crew buttons send back — assignment, withdrawal, and the two
confirmations ``discordbot/crew_acknowledgment.py`` and
``discordbot/crew_signup.py`` reply with.

A sibling of :mod:`application.utils.discord_messages` rather than more lines in
it, on the same rule the qualifier and reschedule copy already follow: crew is a
domain of its own, and the parent module holds the match lifecycle. The
convention is otherwise identical — all Discord text lives in a builder, and
none of it is inlined in service or handler code.

Times arrive as **display strings** already rendered by
``discord_embeds.time_field``, so each recipient's client localises them.
"""

from typing import Optional

from application.utils.match_labels import players_label


def _crew_match_lines(
    match_title: Optional[str],
    scheduled_at_display: str,
    stage_name: Optional[str],
    player_names: Optional[list[str]],
) -> list[str]:
    """Identifying block shared by every crew DM.

    All three describe the same assignment to a different reader, so they have
    to identify it from the same fields. Pass None or '' for optional fields to
    suppress them.
    """
    players = players_label(player_names)
    details: list[str] = []
    # Only show the title when it adds information beyond the roster line — some
    # schedule feeds set the match title to the matchup itself, which would
    # otherwise print the same value twice ("Match: A vs B" / "Players: A vs B").
    if match_title and match_title != players:
        details.append(f"**Match:** {match_title}")
    if players:
        details.append(f"**Players:** {players}")
    if scheduled_at_display:
        details.append(f"**Scheduled:** {scheduled_at_display}")
    if stage_name:
        details.append(f"**Stage:** {stage_name}")
    return details


def _crew_dm(intro: str, details: list[str], call_to_action: str) -> str:
    """Intro / detail block / call to action, blank-line separated — the
    match-notification rhythm used elsewhere in this module."""
    blocks = [intro]
    if details:
        blocks.append("\n".join(details))
    blocks.append(call_to_action)
    return "\n\n".join(blocks)


def crew_assignment_dm(
    crew_type: str,
    match_title: Optional[str],
    scheduled_at_display: str,
    stage_name: Optional[str],
    player_names: Optional[list[str]],
) -> str:
    """DM with crew-acknowledgment button sent when a crew member is approved."""
    return _crew_dm(
        f"You're confirmed as {crew_type} for this match!",
        _crew_match_lines(match_title, scheduled_at_display, stage_name, player_names),
        "Please click below to acknowledge your assignment.",
    )


def crew_approval_withdrawn_dm(
    crew_type: str,
    match_title: Optional[str],
    scheduled_at_display: str,
    stage_name: Optional[str],
    player_names: Optional[list[str]],
) -> str:
    """DM sent when an admin withdraws a crew member's approval."""
    return _crew_dm(
        f"You've been taken off the {crew_type} slot for this match.",
        _crew_match_lines(match_title, scheduled_at_display, stage_name, player_names),
        "Check with staff if this looks wrong.",
    )


def crew_signup_pending_dm(
    crew_type: str,
    volunteer_name: str,
    match_title: Optional[str],
    scheduled_at_display: str,
    stage_name: Optional[str],
    player_names: Optional[list[str]],
) -> str:
    """DM to whoever approves crew for a match when someone signs up for it."""
    return _crew_dm(
        f"**{volunteer_name}** signed up as {crew_type} and is waiting for approval.",
        _crew_match_lines(match_title, scheduled_at_display, stage_name, player_names),
        "Approve them from the match on the schedule.",
    )


def crew_withdrawn_dm(
    crew_type: str,
    volunteer_name: str,
    match_title: Optional[str],
    scheduled_at_display: str,
    stage_name: Optional[str],
    player_names: Optional[list[str]],
    hours_notice: Optional[float] = None,
) -> str:
    """DM to whoever owns crew for a match when an *approved* member drops it.

    The mirror of :func:`crew_approval_withdrawn_dm`: that one tells the crew
    member an admin removed them, this one tells the admins the crew member
    removed themselves. ``hours_notice`` is how long until the match starts —
    the thing that decides whether this needs acting on now.
    """
    notice = ''
    if hours_notice is None:
        pass
    elif hours_notice < 0:
        notice = ' — already past its scheduled time'
    elif hours_notice < 1:
        notice = ' — in under an hour'
    elif hours_notice < 48:
        hours = round(hours_notice)
        notice = f" — in about {hours} hour{'' if hours == 1 else 's'}"
    return _crew_dm(
        f"**{volunteer_name}** has withdrawn as {crew_type}{notice}.",
        _crew_match_lines(match_title, scheduled_at_display, stage_name, player_names),
        "The slot is open again — the match needs someone else on it.",
    )


# ---------------------------------------------------------------------------
# Ephemeral replies to the crew buttons
# ---------------------------------------------------------------------------

def crew_ack_confirmation(crew_type: str, player_names: str) -> str:
    """Ephemeral success reply after a user clicks the crew Acknowledge button."""
    if player_names:
        return (
            f"You're confirmed for {crew_type} "
            f'({player_names}). Thanks!'
        )
    return f"You're confirmed for {crew_type}. Thanks!"


def crew_signup_confirmation(role: str, player_names: str) -> str:
    """Ephemeral success reply after a user signs up for crew via a DM button."""
    match_ref = f' for the match ({player_names})' if player_names else ''
    return (
        f"You're signed up as **{role}**{match_ref}. Staff will confirm it "
        f'shortly.'
    )
