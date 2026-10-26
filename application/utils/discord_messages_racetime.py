"""Text of the DMs the racetime room bot causes: a room opening for a player who
hasn't linked racetime, and a fair play pause reaching staff."""

from typing import Optional

import discord

from application.utils.discord_messages import _match_info_lines


def race_room_open_unlinked_dm(
    tournament_name: str,
    scheduled_at_display: str,
    *,
    player_names: Optional[list[str]] = None,
    bracket_line: str = '',
) -> str:
    """To a player with no racetime account linked, when a room the bot runs opens.

    The bot can't invite or score someone it can't recognise, so the button
    goes to linking first; the card's title still opens the room.
    """
    info = _match_info_lines(
        player_names=player_names,
        scheduled_at_display=scheduled_at_display,
        bracket_line=bracket_line,
    )
    body = "\n".join(info)
    block = f"{body}\n\n" if body else ''
    return (
        f"Your race room for **{tournament_name}** is open.\n\n"
        f"{block}"
        f"Link your racetime.gg account first, so the room bot can invite you and "
        f"record your time. Then join the room on racetime.gg."
    )


def racetime_fpa_staff_dm(
    runner_name: str, tournament_name: str, race_time: str, note: Optional[str],
) -> str:
    """A runner called a fair play pause in a bot-run room; the copy staff get.

    Name and note come from racetime chat, so both are escaped.
    """
    def safe(text: str) -> str:
        return discord.utils.escape_mentions(discord.utils.escape_markdown(text))

    lines = [
        f"**{safe(runner_name)}** called a fair play pause at **{race_time}** in their "
        f"**{tournament_name}** race. The result will wait for you to check it."
    ]
    if note:
        lines.append(f"> {safe(note)}")
    return '\n\n'.join(lines)
