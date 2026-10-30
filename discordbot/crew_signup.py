"""
Discord crew signup interaction handler and view factory.
"""

import discord

from application.utils.discord_messages import msg_unexpected_error
from application.utils.discord_messages_crew import crew_signup_confirmation
from discordbot._ack_common import DMInteractionError, SendFn, run_dm_interaction

CUSTOM_ID_PREFIX = 'crew_signup'


def make_crew_signup_view(match_id: int, broadcaster: bool = False) -> discord.ui.View:
    """Create a Discord View with the crew signup buttons for a match.

    ``broadcaster`` adds the third button, for an event with broadcasters on.
    """
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label='Sign up as Commentator',
        style=discord.ButtonStyle.primary,
        custom_id=f'{CUSTOM_ID_PREFIX}:commentator:{match_id}',
    ))
    view.add_item(discord.ui.Button(
        label='Sign up as Tracker',
        style=discord.ButtonStyle.secondary,
        custom_id=f'{CUSTOM_ID_PREFIX}:tracker:{match_id}',
    ))
    if broadcaster:
        view.add_item(discord.ui.Button(
            label='Sign up as Broadcaster',
            style=discord.ButtonStyle.secondary,
            custom_id=f'{CUSTOM_ID_PREFIX}:broadcaster:{match_id}',
        ))
    return view


def _parse(custom_id: str) -> tuple[str, int]:
    parts = custom_id.split(':')
    if len(parts) != 3:
        raise DMInteractionError('Invalid interaction.')
    _, role, match_id_str = parts
    try:
        match_id = int(match_id_str)
    except ValueError:
        raise DMInteractionError('Invalid match ID.') from None
    if role not in ('commentator', 'tracker', 'broadcaster'):
        raise DMInteractionError('Invalid role.')
    return role, match_id


async def handle_crew_signup_interaction(interaction: discord.Interaction) -> None:
    """
    Handle a crew_signup button press from a Discord DM.

    custom_id format: 'crew_signup:<role>:<match_id>'
    Responds ephemerally so only the clicking user sees the result.
    """
    from application.services import CrewService, MatchService
    from discordbot._tenant import match_scope

    async def resolve_tenant(parsed: tuple[str, int]):
        _, match_id = parsed
        return await match_scope(match_id)

    async def handle(_interaction, parsed, user, send: SendFn) -> None:
        role, match_id = parsed
        match_service = MatchService()
        await CrewService().signup_crew(match_id, user, role)
        player_names = await match_service.get_player_names(match_id)
        await send(crew_signup_confirmation(role, player_names))

    await run_dm_interaction(
        interaction,
        log_label=CUSTOM_ID_PREFIX,
        parse=_parse,
        resolve_tenant=resolve_tenant,
        not_found_message='Match not found.',
        handle=handle,
        unexpected_error_message=msg_unexpected_error('sign up'),
    )
