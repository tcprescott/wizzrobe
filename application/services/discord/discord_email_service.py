"""Fill in a notification email from the person's own Discord account.

The provider half of the "Use my Discord email" button: the page scaffolding in
``pages/_oauth_link.py`` runs the OAuth round trip, and this exchanges the code
for Discord's ``/users/@me`` and hands the address to
``NotificationChannelService.use_discord_email``. Only an address Discord marks
``verified`` is accepted, and only for the Discord account the person is signed
in with.

Needs the callback (``/discord/email/callback`` on ``BASE_URL``, or
``DISCORD_EMAIL_REDIRECT_URI``) added to the Discord application's OAuth2
redirects.
"""

import os
from typing import Any, Dict, Optional

from application.utils.clients.discord_email_client import (
    SCOPE,
    DiscordEmailClient,
    MockDiscordEmailClient,
    build_authorize_url,
)
from application.utils.environment import get_base_url
from application.utils.mocks.mock_discord import is_mock_discord
from models import User

CALLBACK_PATH = '/discord/email/callback'


def _redirect_uri() -> str:
    return os.getenv('DISCORD_EMAIL_REDIRECT_URI') or f'{get_base_url()}{CALLBACK_PATH}'


class DiscordEmailService:
    """Exchange and record for the Discord email link flow."""

    @staticmethod
    def player_authorize_url(state: str) -> str:
        return build_authorize_url(os.getenv('DISCORD_CLIENT_ID', ''), _redirect_uri(), SCOPE, state)

    def _client(self) -> DiscordEmailClient:
        if is_mock_discord():
            return MockDiscordEmailClient()
        return DiscordEmailClient(
            client_id=os.getenv('DISCORD_CLIENT_ID', ''),
            client_secret=os.getenv('DISCORD_CLIENT_SECRET', ''),
        )

    async def exchange_player_code(self, code: str) -> Dict[str, Any]:
        client = self._client()
        payload = await client.exchange_code(code, _redirect_uri())
        access = payload.get('access_token')
        if not access:
            raise ValueError("Discord didn't send your email back. Try again, or use a different address.")
        me = await client.get_me(access)
        if not me.get('email'):
            raise ValueError("Your Discord account doesn't have an email address. Use a different address instead.")
        if not me.get('verified'):
            raise ValueError("Discord hasn't confirmed that email yet. Use a different address and we'll send it a code.")
        return me

    async def record_player_link(
        self, user: User, discord_user_id: str, email: Optional[str], actor: User,
    ) -> None:
        from application.services.notification_channel_service import NotificationChannelService

        if is_mock_discord() and discord_user_id == 'mock':
            discord_user_id = str(user.discord_id)
        await NotificationChannelService().use_discord_email(
            user, discord_user_id, email, verified=True,
        )
