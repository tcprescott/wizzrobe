"""
Twitch Service - Business Logic Layer

Coordinates the Twitch account-linking integration: a logged-in user completes
a one-time Twitch OAuth login so we can record their verified Twitch identity
(id / login / display name). Their access token is used once and discarded — we
retain identity only.

The linking logic is shared with the racetime integration via
:class:`IdentityLinkService`; this module is the Twitch-specific configuration
plus a thin public shell that preserves the historical method surface.
"""

from typing import Any, Dict, Optional

from application.services.audit_service import AuditActions
from application.services.identity_link_service import (
    IdentityLinkProvider,
    IdentityLinkService,
)
from application.utils.clients.twitch_client import (
    MockTwitchClient,
    TwitchAPIError,
    TwitchClient,
    build_authorize_url,
)
from application.utils.mocks.mock_twitch import is_mock_twitch
from models import User

# Basic public identity (id / login / display name via helix/users) needs no
# scope; an empty scope keeps the consent screen minimal.
_PLAYER_SCOPES = ''

_PROVIDER = IdentityLinkProvider(
    field_prefix='twitch',
    label='Twitch',
    linked_action=AuditActions.TWITCH_LINKED,
    unlinked_action=AuditActions.TWITCH_UNLINKED,
    client_id_env='TWITCH_CLIENT_ID',
    client_secret_env='TWITCH_CLIENT_SECRET',
    redirect_uri_env='TWITCH_REDIRECT_URI',
    callback_path='/twitch/oauth/callback',
    scope=_PLAYER_SCOPES,
    is_mock=is_mock_twitch,
    client_cls=TwitchClient,
    mock_client_cls=MockTwitchClient,
    authorize_url_builder=build_authorize_url,
    token_error_class=TwitchAPIError,
)

_LINK = IdentityLinkService(_PROVIDER)


class TwitchService:
    """Business logic for the Twitch account-linking integration."""

    def __init__(self) -> None:
        self._link = _LINK
        self.audit_service = _LINK.audit_service

    @staticmethod
    def is_configured() -> bool:
        """True when the OAuth app credentials are present (or mock is on)."""
        return _LINK.is_configured()

    @staticmethod
    def player_authorize_url(state: str) -> str:
        return _LINK.player_authorize_url(state)

    @staticmethod
    def redirect_uri() -> str:
        return _LINK.redirect_uri()

    def _oauth_client(self) -> TwitchClient:
        return self._link.build_oauth_client()

    async def exchange_player_code(self, code: str) -> Dict[str, Any]:
        """Exchange a user's authorization code and return their identity.

        Returns {'user_id', 'username', 'display_name'}. The token is used once
        and discarded.
        """
        return await self._link.exchange_player_code(self._oauth_client(), code)

    async def record_player_link(
        self,
        user: User,
        twitch_user_id: str,
        twitch_username: Optional[str],
        actor: User,
    ) -> None:
        await self._link.record_player_link(user, twitch_user_id, twitch_username, actor)

    async def unlink_player(self, user: User, actor: User) -> None:
        await self._link.unlink_player(user, actor)
