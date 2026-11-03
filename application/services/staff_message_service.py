"""A staff member's own direct message to one member (the Send message dialog).

The text is staff's, so it goes out as written and always pings: there's no
catalogue notice to switch it off, the same as the website's dialog. The audit
row records who wrote to whom, never the text; the sent-messages log keeps that.
"""

from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.discord.discord_service import DiscordService
from models import User

MESSAGE_MAX_LENGTH = 2000


class StaffMessageService:
    async def send(self, actor: User, member: User, message: str) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), 'Only staff can message members.')
        text = (message or '').strip()
        if not text:
            raise ValueError('Write a message first.')
        if len(text) > MESSAGE_MAX_LENGTH:
            raise ValueError(f'Keep it under {MESSAGE_MAX_LENGTH} characters.')
        if member.discord_id is None:
            raise ValueError("They don't have Discord linked, so there's nowhere to send it.")
        sent, error = await DiscordService().send_dm(member.discord_id, text)
        if not sent:
            raise ValueError(f"Couldn't send it: {error}")
        await AuditService().write_log(
            actor, AuditActions.USER_MESSAGED, {'user_id': member.id, 'length': len(text)},
        )
