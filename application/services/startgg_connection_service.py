"""
start.gg Connection Service - Business Logic Layer

A community's start.gg sign-in. A staff member signs in once with
``user.identity tournament.reporter``; syncs and result reports in that
community then run as their account, which only needs an admin role (Reporter
or higher) on the start.gg tournament. Access tokens last a week and are
refreshed shortly before they lapse. A community with no sign-in falls back to
the platform's ``STARTGG_API_TOKEN``.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from application.feature_flags import requires_feature
from application.repositories.startgg_repository import StartggRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.startgg_link_service import StartggLinkService
from application.tenant_context import require_tenant_id
from application.utils.clients.startgg_client import (
    CONNECT_SCOPE,
    MockStartggOAuthClient,
    StartggAPIError,
    StartggClient,
    StartggOAuthClient,
    build_authorize_url,
    event_url,
    tournament_slug,
)
from application.utils.mocks.mock_startgg import MockStartggClient, is_mock_startgg
from application.utils.shared_lock import shared_lock
from models import FeatureFlag, StartggConnection, User

logger = logging.getLogger(__name__)

_REFRESH_BUFFER = timedelta(days=1)

# One refresh per community: start.gg may rotate the refresh token, and a racing loser sends a spent one.


@dataclass(frozen=True)
class TournamentAccess:
    """Whether the signed-in account can report results on one linked tournament.

    ``can_report`` is None when start.gg couldn't be asked.
    """

    tournament_id: int
    name: str
    event_url: str
    reports_results: bool
    can_report: Optional[bool]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class StartggConnectionService:
    """The community's start.gg sign-in: connect, refresh, disconnect, check access."""

    def __init__(self) -> None:
        self.repository = StartggRepository()
        self.audit_service = AuditService()

    @staticmethod
    def is_configured() -> bool:
        """True when the server has a start.gg OAuth app (or mock is on)."""
        return StartggLinkService.is_configured()

    @staticmethod
    def connect_authorize_url(state: str) -> str:
        return build_authorize_url(
            os.getenv('STARTGG_CLIENT_ID', ''), StartggLinkService.redirect_uri(), CONNECT_SCOPE, state,
        )

    @staticmethod
    def _oauth_client() -> StartggOAuthClient:
        if is_mock_startgg():
            return MockStartggOAuthClient()
        return StartggOAuthClient(os.getenv('STARTGG_CLIENT_ID', ''), os.getenv('STARTGG_CLIENT_SECRET', ''))

    @staticmethod
    def _unpack(payload: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[datetime]]:
        access = payload.get('access_token')
        if not access:
            raise StartggAPIError('start.gg sent no access token.')
        expires_in = payload.get('expires_in')
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in)) if expires_in else None
        return access, payload.get('refresh_token'), expires_at

    @requires_feature(FeatureFlag.STARTGG)
    async def exchange_connect_code(self, code: str) -> Dict[str, Any]:
        try:
            return await self._oauth_client().exchange_code(
                code, StartggLinkService.redirect_uri(), CONNECT_SCOPE,
            )
        except StartggAPIError as e:
            raise ValueError(f"start.gg didn't accept the sign-in: {e}") from e

    @requires_feature(FeatureFlag.STARTGG)
    async def save_connection(self, payload: Dict[str, Any], actor: User) -> StartggConnection:
        await AuthService.ensure(
            await AuthService.is_staff(actor), 'Only staff can connect start.gg for this community',
        )
        try:
            access, refresh, expires_at = self._unpack(payload)
            me = await self._oauth_client().get_me(access)
        except StartggAPIError as e:
            raise ValueError(f"Couldn't finish the start.gg sign-in: {e}") from e
        connection = await self.repository.save_connection(
            access_token=access,
            refresh_token=refresh,
            token_expires_at=expires_at,
            scopes=payload.get('scope') or CONNECT_SCOPE,
            startgg_user_id=me.get('user_id'),
            startgg_username=me.get('display_name') or me.get('username'),
            connected_by=actor,
        )
        await self.audit_service.write_log(actor, AuditActions.STARTGG_CONNECTED, {
            'startgg_user_id': connection.startgg_user_id,
            'startgg_username': connection.startgg_username,
        })
        return connection

    @requires_feature(FeatureFlag.STARTGG)
    async def disconnect(self, actor: User) -> None:
        await AuthService.ensure(
            await AuthService.is_staff(actor), 'Only staff can disconnect start.gg for this community',
        )
        await self.repository.clear_connection()
        await self.audit_service.write_log(actor, AuditActions.STARTGG_DISCONNECTED, {})

    @requires_feature(FeatureFlag.STARTGG)
    async def get_status(self) -> Dict[str, Any]:
        connection = await self.repository.get_connection()
        base = {
            'configured': self.is_configured(),
            'platform_token': bool(os.getenv('STARTGG_API_TOKEN')),
        }
        if connection is None:
            return {'connected': False, **base}
        try:
            await self._fresh_access_token(connection)
        except StartggAPIError:
            logger.warning('start.gg sign-in refresh failed for tenant %s', connection.tenant_id, exc_info=True)
        expired = (
            connection.token_expires_at is not None
            and _aware(connection.token_expires_at) <= datetime.now(timezone.utc)
        )
        return {
            'connected': True,
            'username': connection.startgg_username,
            'scopes': connection.scopes,
            'renewed_at': connection.updated_at,
            'expired': expired,
            **base,
        }

    async def api_token(self) -> Optional[str]:  # feature-gate: exempt — StartggService's callers are gated
        """The token this community's start.gg calls use: its sign-in, else the platform's.

        A sign-in whose refresh fails falls back to the platform token rather
        than raising, so syncs keep reading while staff reconnect.
        """
        connection = await self.repository.get_connection()
        if connection is None:
            return os.getenv('STARTGG_API_TOKEN') or None
        try:
            return await self._fresh_access_token(connection)
        except StartggAPIError:
            logger.warning('start.gg sign-in refresh failed for tenant %s', connection.tenant_id, exc_info=True)
            return os.getenv('STARTGG_API_TOKEN') or connection.access_token

    async def has_access(self) -> bool:  # feature-gate: exempt — a yes/no for gated callers
        return is_mock_startgg() or bool(os.getenv('STARTGG_API_TOKEN')) or (
            await self.repository.get_connection() is not None
        )

    async def _fresh_access_token(self, connection: StartggConnection) -> str:
        if not self._expiring(connection) or not connection.refresh_token:
            return connection.access_token
        async with shared_lock('startgg.token_refresh', connection.tenant_id):
            await connection.refresh_from_db()
            if not self._expiring(connection) or not connection.refresh_token:
                return connection.access_token
            payload = await self._oauth_client().refresh(
                connection.refresh_token, StartggLinkService.redirect_uri(), connection.scopes or CONNECT_SCOPE,
            )
            access, refresh, expires_at = self._unpack(payload)
            await self.repository.update_connection_tokens(connection, access, refresh, expires_at)
            return access

    @staticmethod
    def _expiring(connection: StartggConnection) -> bool:
        if connection.token_expires_at is None:
            return False
        return datetime.now(timezone.utc) >= _aware(connection.token_expires_at) - _REFRESH_BUFFER

    @requires_feature(FeatureFlag.STARTGG)
    async def access_for_linked(self, actor: User) -> List[TournamentAccess]:
        """For each linked tournament, whether the signed-in account holds an admin role on it.

        Two reads, either of which answers yes: the account's admin-view
        tournament list, then the tournament's own owner/admins. A Reporter may
        not be shown the admins list, so "no" needs both to say so.
        """
        await AuthService.ensure(await AuthService.is_staff(actor), 'Only staff can see start.gg access')
        connection = await self.repository.get_connection()
        tournaments = sorted(await self.repository.live_linked_tournaments(), key=lambda t: t.name.lower())
        if connection is None or not tournaments:
            return []
        client = MockStartggClient() if is_mock_startgg() else StartggClient(await self._fresh_access_token(connection))
        user_id: Optional[str] = connection.startgg_user_id
        admin_slugs: Optional[Set[str]]
        try:
            me, admin_slugs = await client.get_admin_tournament_slugs()
            user_id = me or user_id
        except StartggAPIError:
            logger.info('start.gg admin-tournament list unavailable for tenant %s', require_tenant_id(), exc_info=True)
            admin_slugs = None
        rows: List[TournamentAccess] = []
        for t in tournaments:
            rows.append(TournamentAccess(
                tournament_id=t.id, name=t.name, event_url=event_url(t.startgg_event_slug or ''),
                reports_results=t.startgg_report_results,
                can_report=await self._can_report(
                    client, tournament_slug(t.startgg_event_slug or ''), user_id, admin_slugs,
                ),
            ))
        return rows

    @staticmethod
    async def _can_report(
        client: Any, slug: str, user_id: Optional[str], admin_slugs: Optional[Set[str]],
    ) -> Optional[bool]:
        if admin_slugs is not None and slug in admin_slugs:
            return True
        admin_ids: Optional[Set[str]] = None
        if user_id:
            try:
                admin_ids = await client.get_tournament_admin_ids(slug)
            except StartggAPIError:
                admin_ids = None
        if admin_ids is not None and user_id in admin_ids:
            return True
        if admin_slugs is None and admin_ids is None:
            return None
        return False
