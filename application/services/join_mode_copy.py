"""What staff read when they pick who can join: each mode's label and what it means."""

from application.services.tenant_membership_service import JoinMode

JOIN_MODE_LABELS = {
    JoinMode.OPEN: 'Anyone who signs in',
    JoinMode.DISCORD_OR_REQUEST: 'Discord server members, and others can ask',
    JoinMode.DISCORD: 'Discord server members only',
    JoinMode.REQUEST: 'Everyone asks, and staff approve',
    JoinMode.STAFF_ONLY: 'Only people staff add',
}

JOIN_MODE_HELP = {
    JoinMode.OPEN: (
        'Signing in with Discord makes someone a member on the spot. Nobody gets '
        'a role from it. Removing a member doesn’t keep them out: they’re back '
        'the next time they open the community.'
    ),
    JoinMode.DISCORD_OR_REQUEST: (
        'Members of the linked Discord server get in the first time they open the '
        'community. Anyone else can ask from the join page, and Staff decide on '
        'Members.'
    ),
    JoinMode.DISCORD: (
        'Members of the linked Discord server get in the first time they open the '
        'community. Nobody else can ask.'
    ),
    JoinMode.REQUEST: (
        'People ask from the join page and Staff approve or decline them on the '
        'Users tab. Requests already waiting stay there whichever option you pick.'
    ),
    JoinMode.STAFF_ONLY: (
        'The join page takes no requests. Staff add people from Members.'
    ),
}


def offered_join_modes(current: JoinMode, guild_linked: bool) -> dict[JoinMode, str]:
    """The modes staff may pick: Discord ones need a linked server, unless already chosen."""
    return {
        mode: label for mode, label in JOIN_MODE_LABELS.items()
        if guild_linked or not mode.uses_discord or mode is current
    }


def join_mode_help(mode: JoinMode, guild_linked: bool) -> str:
    """What picking ``mode`` means, given whether a Discord server is linked."""
    if mode.uses_discord and not guild_linked:
        return 'No Discord server is linked, so nobody gets in this way until one is.'
    caption = JOIN_MODE_HELP[mode]
    if not guild_linked and mode is not JoinMode.OPEN:
        caption += ' Link a Discord server under Settings → Discord to let its members in.'
    return caption
