"""
start.gg Link Service - Business Logic Layer

A player links their start.gg account once (OAuth, ``user.identity`` scope) so
every start.gg event they enter maps to them without staff matching names. The
token is used once and discarded; only the start.gg user id and gamer tag are
kept. Shares its flow with Twitch and racetime through
:class:`IdentityLinkService`.
"""

from typing import Any, Dict, Optional

from application.services.audit_service import AuditActions
from application.services.identity_link_service import (
    IdentityLinkProvider,
    IdentityLinkService,
)
from application.utils.clients.startgg_client import (
    MockStartggOAuthClient,
    StartggAPIError,
    StartggOAuthClient,
    build_authorize_url,
)
from application.utils.mocks.mock_startgg import is_mock_startgg
from models import User

_PROVIDER = IdentityLinkProvider(
    field_prefix='startgg',
    label='start.gg',
    linked_action=AuditActions.STARTGG_LINKED,
    unlinked_action=AuditActions.STARTGG_UNLINKED,
    client_id_env='STARTGG_CLIENT_ID',
    client_secret_env='STARTGG_CLIENT_SECRET',
    redirect_uri_env='STARTGG_REDIRECT_URI',
    callback_path='/startgg/oauth/callback',
    scope='user.identity',
    is_mock=is_mock_startgg,
    client_cls=StartggOAuthClient,
    mock_client_cls=MockStartggOAuthClient,
    authorize_url_builder=build_authorize_url,
    token_error_class=StartggAPIError,
)

_LINK = IdentityLinkService(_PROVIDER)


class StartggLinkService:
    """Links a Wizzrobe user to their start.gg account."""

    def __init__(self) -> None:
        self._link = _LINK
        self.audit_service = _LINK.audit_service

    @staticmethod
    def is_configured() -> bool:
        return _LINK.is_configured()

    @staticmethod
    def player_authorize_url(state: str) -> str:
        return _LINK.player_authorize_url(state)

    @staticmethod
    def redirect_uri() -> str:
        return _LINK.redirect_uri()

    async def exchange_player_code(self, code: str) -> Dict[str, Any]:
        return await self._link.exchange_player_code(self._link.build_oauth_client(), code)

    async def record_player_link(
        self, user: User, startgg_user_id: str, startgg_username: Optional[str], actor: User,
    ) -> None:
        await self._link.record_player_link(user, startgg_user_id, startgg_username, actor)

    async def unlink_player(self, user: User, actor: User) -> None:
        await self._link.unlink_player(user, actor)
