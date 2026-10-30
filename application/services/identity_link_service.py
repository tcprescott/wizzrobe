"""Identity-Link Service - shared account-linking business logic.

Twitch and racetime.gg link a logged-in user's third-party identity through the
same shape: a one-time OAuth login whose access token is used once and discarded,
retaining only the verified identity (id / username) on the ``User`` row. The two
services were line-for-line clones; this module captures that logic once,
parameterized by a small :class:`IdentityLinkProvider` config (field prefix,
audit actions, OAuth client wiring), so each provider service becomes a thin
delegating shell.

The service is provider-agnostic: it imports no concrete provider client. The
per-provider client classes, env-var names, authorize-URL builder, scope, and
error class arrive through the config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from application.repositories._save import save_fields
from application.services.audit_service import AuditService
from application.utils.environment import get_base_url
from models import User


@dataclass(frozen=True)
class IdentityLinkProvider:
    """Per-provider configuration for :class:`IdentityLinkService`.

    - ``field_prefix`` names the ``User`` columns (``{prefix}_user_id`` /
      ``{prefix}_username`` / ``{prefix}_linked_at``) and the audit detail keys.
    - ``label`` shapes user-facing messages (``"A {label} account id ..."``).
    - ``client_cls`` / ``mock_client_cls`` are the OAuth client classes; ``is_mock``
      selects between them (and enforces the no-mock-in-prod guard).
    - ``authorize_url_builder`` is the provider's ``build_authorize_url``.
    """

    field_prefix: str
    label: str
    linked_action: str
    unlinked_action: str
    client_id_env: str
    client_secret_env: str
    redirect_uri_env: str
    callback_path: str
    scope: str
    is_mock: Callable[[], bool]
    client_cls: type
    mock_client_cls: type
    authorize_url_builder: Callable[[str, str, str, str], str]
    token_error_class: type[Exception]


class IdentityLinkService:
    """Shared business logic for a third-party account-linking integration."""

    def __init__(self, provider: IdentityLinkProvider) -> None:
        self.provider = provider
        self.audit_service = AuditService()

    def _redirect_uri(self) -> str:
        return os.getenv(self.provider.redirect_uri_env) or f"{get_base_url()}{self.provider.callback_path}"

    def redirect_uri(self) -> str:
        return self._redirect_uri()

    def is_configured(self) -> bool:
        """True when the OAuth app credentials are present (or mock is on)."""
        if self.provider.is_mock():
            return True
        return bool(
            os.getenv(self.provider.client_id_env) and os.getenv(self.provider.client_secret_env)
        )

    def player_authorize_url(self, state: str) -> str:
        return self.provider.authorize_url_builder(
            os.getenv(self.provider.client_id_env, ''),
            self._redirect_uri(),
            self.provider.scope,
            state,
        )

    def build_oauth_client(self) -> Any:
        cls = self.provider.mock_client_cls if self.provider.is_mock() else self.provider.client_cls
        return cls(
            client_id=os.getenv(self.provider.client_id_env, ''),
            client_secret=os.getenv(self.provider.client_secret_env, ''),
        )

    async def exchange_player_code(self, client: Any, code: str) -> Dict[str, Any]:
        """Exchange a user's authorization code and return their identity.

        The token is used once and discarded. ``client`` is supplied by the
        caller so a monkeypatched provider client is honored.
        """
        payload = await client.exchange_code(code, self._redirect_uri())
        access = payload.get('access_token')
        if not access:
            raise self.provider.token_error_class(
                f"{self.provider.label} token response missing access_token: {payload}"
            )
        return await client.get_me(access)

    async def record_player_link(
        self,
        user: User,
        provider_user_id: str,
        provider_username: Optional[str],
        actor: User,
    ) -> None:
        prefix = self.provider.field_prefix
        pid = (provider_user_id or '').strip()
        if not pid:
            raise ValueError(f'A {self.provider.label} account id is required to link this user.')
        existing = await User.filter(**{f'{prefix}_user_id': pid}).exclude(id=user.id).first()
        if existing is not None:
            raise ValueError(
                f'That {self.provider.label} account is already linked to {existing.username}.'
            )
        username = (provider_username or '').strip() or None
        setattr(user, f'{prefix}_user_id', pid)
        setattr(user, f'{prefix}_username', username)
        setattr(user, f'{prefix}_linked_at', datetime.now(timezone.utc))
        await save_fields(user, f'{prefix}_user_id', f'{prefix}_username', f'{prefix}_linked_at')
        await self.audit_service.write_log(
            actor, self.provider.linked_action,
            {'user_id': user.id, f'{prefix}_user_id': pid, f'{prefix}_username': username},
        )

    async def unlink_player(self, user: User, actor: User) -> None:
        prefix = self.provider.field_prefix
        setattr(user, f'{prefix}_user_id', None)
        setattr(user, f'{prefix}_username', None)
        setattr(user, f'{prefix}_linked_at', None)
        await save_fields(user, f'{prefix}_user_id', f'{prefix}_username', f'{prefix}_linked_at')
        await self.audit_service.write_log(
            actor, self.provider.unlinked_action, {'user_id': user.id},
        )
