"""Commentary invites waiting on the viewer, at the top of My Schedule.

A commentator who signed up for a match can ask someone to commentate it with
them. The partner answers here or on the DM's buttons; either way accepting
signs them up through the ordinary crew signup, so staff still approve it.
"""

from nicegui import ui

from application.services import CommentaryInviteService, get_user_from_session
from application.utils.match_labels import players_label
from application.utils.timezone import format_local_display
from models import CommentaryInvite
from theme.connection import REQUIRES_SOCKET_CLASS
from theme.section import section_panel
from theme.steady import steady_refreshable

#: The section's DOM id, so the invite DM's link lands on it.
INVITES_SECTION_ID = 'crew-invites'


def _match_line(invite: CommentaryInvite) -> str:
    match = invite.match
    names = [p.user.preferred_name for p in match.players] + list(match.bracket_placeholders or [])
    parts = [players_label(names) or match.title or 'A match']
    if match.title and names:
        parts.append(match.title)
    if match.tournament:
        parts.append(match.tournament.name)
    parts.append(format_local_display(match.scheduled_at) if match.scheduled_at else 'not booked yet')
    return ' · '.join(parts)


async def crew_invites_section(on_change=None) -> bool:
    """Draw the viewer's waiting invites; draws nothing, and says so, when there are none."""
    from nicegui import app

    user = await get_user_from_session(app.storage.user)
    if user is None:
        return False
    service = CommentaryInviteService()
    if not await service.pending_for(user):
        return False

    panel = await section_panel(
        'Commentary invites', icon='group_add',
        subtitle='Commentators asking you to commentate with them.',
        help_topics=[('crew-pairs', 'Commentating as a pair')], user=user,
    )
    panel.body.props(f'id={INVITES_SECTION_ID}')

    with panel.body:
        @steady_refreshable
        async def invites() -> None:
            pending = await service.pending_for(user)
            if not pending:
                ui.label('No invites waiting.').classes('text-muted italic-note')
                return
            for invite in pending:
                with ui.row().classes('wiz-invite-row items-center full-width'):
                    with ui.column().classes('gap-0 col'):
                        ui.label(f'{invite.inviter.preferred_name} asked you to commentate with them') \
                            .classes('text-weight-medium')
                        ui.label(_match_line(invite)).classes('text-caption text-muted')
                    with ui.row().classes('wiz-invite-row__acts no-wrap gap-2'):
                        ui.button('Decline', on_click=lambda i=invite: answer(i, False)) \
                            .props('outline no-caps').classes(REQUIRES_SOCKET_CLASS)
                        ui.button('Accept', on_click=lambda i=invite: answer(i, True)) \
                            .props('unelevated no-caps color=primary').classes(REQUIRES_SOCKET_CLASS)

        async def answer(invite: CommentaryInvite, accept: bool) -> None:
            try:
                await service.respond(invite.id, user, accept)
            except ValueError as e:
                ui.notify(str(e), color='warning')
            else:
                name = invite.inviter.preferred_name
                ui.notify(f"You're signed up to commentate with {name}." if accept
                          else f'Declined. {name} will see you can\'t make it.', color='positive')
            await invites.refresh()
            if on_change is not None:
                on_change()

        await invites()
    return True
