"""
Racetime Service - Business Logic Layer

Coordinates the racetime.gg account-linking integration: a logged-in user
completes a one-time racetime OAuth login (read scope) so we can record their
verified racetime identity (id / name). Their access token is used once and
discarded — we retain identity only, mirroring the Twitch link.

The linking logic is shared with the Twitch integration via
:class:`IdentityLinkService`; this module is the racetime-specific configuration
plus a thin public shell that preserves the historical method surface.
"""

from typing import Any, Dict, Optional

from application.services.audit_service import AuditActions
from application.services.identity_link_service import (
    IdentityLinkProvider,
    IdentityLinkService,
)
from application.utils.clients.racetime_client import (
    IDENTITY_SCOPE,
    MockRacetimeClient,
    RacetimeAPIError,
    RacetimeClient,
    build_authorize_url,
)
from application.utils.mocks.mock_racetime import is_mock_racetime
from models import User

_PROVIDER = IdentityLinkProvider(
    field_prefix='racetime',
    label='racetime',
    linked_action=AuditActions.RACETIME_LINKED,
    unlinked_action=AuditActions.RACETIME_UNLINKED,
    client_id_env='RACETIME_CLIENT_ID',
    client_secret_env='RACETIME_CLIENT_SECRET',
    redirect_uri_env='RACETIME_REDIRECT_URI',
    callback_path='/racetime/oauth/callback',
    scope=IDENTITY_SCOPE,
    is_mock=is_mock_racetime,
    client_cls=RacetimeClient,
    mock_client_cls=MockRacetimeClient,
    authorize_url_builder=build_authorize_url,
    token_error_class=RacetimeAPIError,
)

_LINK = IdentityLinkService(_PROVIDER)


class RacetimeService:
    """Business logic for the racetime.gg account-linking integration."""

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

    def _oauth_client(self) -> RacetimeClient:
        return self._link.build_oauth_client()

    async def exchange_player_code(self, code: str) -> Dict[str, Any]:
        """Exchange a user's authorization code and return their identity.

        Returns {'user_id', 'username'}. The token is used once and discarded.
        """
        return await self._link.exchange_player_code(self._oauth_client(), code)

    async def record_player_link(
        self,
        user: User,
        racetime_user_id: str,
        racetime_username: Optional[str],
        actor: User,
    ) -> None:
        await self._link.record_player_link(user, racetime_user_id, racetime_username, actor)

    async def unlink_player(self, user: User, actor: User) -> None:
        await self._link.unlink_player(user, actor)
