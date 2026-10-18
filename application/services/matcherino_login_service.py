"""
Matcherino Login Service - Business Logic Layer

The Matcherino account a community's check-in sync signs in as, replacing the
former process-wide ``MATCHERINO_REFRESH_TOKEN`` environment variable. Each
community saves the refresh token of an account that administers its own venues,
so one community's sync never reads ticket sales through another's account.

Matcherino publishes no way to mint a login for a bot, so staff copy the
refresh token out of a signed-in browser session and paste it here whenever
Matcherino stops accepting the old one. Saving checks the token against
Matcherino first: a typo or an access token pasted by mistake is refused at the
form rather than surfacing later as a failed sync at the desk.

**The token never leaves this module except into the check-in sync.**
:meth:`status` reports configured-or-not and whose account it is, never the
value; the audit entries never carry it; :meth:`resolve` is the single unmasked
read and only ``CheckInService`` calls it.

**Failure alerts go to one person.** Staff pick who hears when Matcherino
refuses the saved login (``alert_user``, any Staff member). The sync reports
each outcome through :meth:`note_sync_result`: a refusal DMs that person once,
with a button to the card, and a good sync or a freshly saved token re-arms it.
A Matcherino outage is not a refusal and alerts nobody; the desk shows it.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional

from application.feature_flags import requires_feature
from application.repositories import MatcherinoLoginRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.utils.clients.matcherino_client import (
    MAX_REFRESH_TOKEN_LENGTH,
    MatcherinoAPIError,
    MatcherinoAuthError,
    MatcherinoClient,
    get_matcherino_client,
    normalize_refresh_token,
)
from models import FeatureFlag, Role, User

logger = logging.getLogger(__name__)

_STAFF_DENIED = 'Only staff can change the Matcherino login.'


@dataclass(frozen=True)
class MatcherinoLoginStatus:
    """What Admin → Check-in shows about the saved login. Never the token."""

    configured: bool
    matcherino_user_id: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    alert_user_id: Optional[int] = None
    alert_user: Optional[str] = None


class MatcherinoLoginService:
    """The per-community Matcherino login: status, save, remove, and sync-time resolution."""

    def __init__(
        self, client_factory: Callable[[Optional[str]], MatcherinoClient] = get_matcherino_client,
    ) -> None:
        self.repository = MatcherinoLoginRepository()
        self.audit_service = AuditService()
        self._client_factory = client_factory

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def status(self, actor: Optional[User]) -> MatcherinoLoginStatus:
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        login = await self.repository.get_current()
        if login is None:
            return MatcherinoLoginStatus(configured=False)
        return MatcherinoLoginStatus(
            configured=True,
            matcherino_user_id=login.matcherino_user_id,
            updated_at=login.updated_at,
            updated_by=login.updated_by.preferred_name if login.updated_by else None,
            alert_user_id=login.alert_user_id,
            alert_user=login.alert_user.preferred_name if login.alert_user else None,
        )

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def set_login(self, actor: User, pasted: str) -> MatcherinoLoginStatus:
        """Check ``pasted`` with Matcherino, then save it as this community's login."""
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        token = normalize_refresh_token(pasted)
        if not token:
            raise ValueError('Paste the refresh token first.')
        if len(token) > MAX_REFRESH_TOKEN_LENGTH or any(c.isspace() for c in token):
            raise ValueError("That doesn't look like a Matcherino refresh token.")
        try:
            account_id = await self._client_factory(token).verify_login()
        except MatcherinoAuthError as e:
            raise ValueError(
                "Matcherino didn't accept that token. Copy refreshToken (not accessToken) "
                'from a session that is still signed in, then try again.'
            ) from e
        except MatcherinoAPIError as e:
            raise ValueError(f"Couldn't check that token with Matcherino: {e}") from e

        previous = await self.repository.get_current()
        await self.repository.upsert(token, account_id, actor)
        await self.audit_service.write_log(
            actor, AuditActions.MATCHERINO_LOGIN_SET,
            {
                'replaced': previous is not None,
                'matcherino_user_id': account_id,
                'previous_matcherino_user_id': previous.matcherino_user_id if previous else None,
            },
        )
        return await self.status(actor)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def clear_login(self, actor: User) -> None:
        """Remove the saved login. Idempotent: clearing an unset one is a no-op."""
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        previous = await self.repository.get_current()
        if previous is None:
            return
        await self.repository.delete_current()
        await self.audit_service.write_log(
            actor, AuditActions.MATCHERINO_LOGIN_CLEARED,
            {'matcherino_user_id': previous.matcherino_user_id},
        )

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def resolve(self) -> Optional[str]:
        """The saved refresh token, unmasked.

        The one path that returns the token, and deliberately not role-gated:
        its only caller is ``CheckInService``, whose own boundary already
        authorized the actor (the sync worker runs as the system user). Never
        call it from a page, router, or anything that renders.
        """
        login = await self.repository.get_current()
        return login.refresh_token if login else None

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def alert_candidates(self, actor: Optional[User]) -> List[User]:
        """Who may be picked to hear about a refused login: the community's Staff."""
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        from application.repositories.user_role_repository import UserRoleRepository

        staff = await UserRoleRepository.list_users_with_role(Role.STAFF, active_only=True)
        return sorted((u for u in staff if not u.is_system), key=lambda u: u.preferred_name.lower())

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def set_alert_user(self, actor: User, user_id: Optional[int]) -> MatcherinoLoginStatus:
        """Choose who is DMed when Matcherino refuses the login; ``None`` alerts nobody."""
        await AuthService.ensure(await AuthService.can_manage_check_in(actor), _STAFF_DENIED)
        login = await self.repository.get_current()
        if login is None:
            raise ValueError('Save a Matcherino login first.')
        if user_id is not None and user_id not in {u.id for u in await self.alert_candidates(actor)}:
            raise ValueError('Pick a staff member of this community.')
        await self.repository.set_alert_user(user_id)
        await self.audit_service.write_log(
            actor, AuditActions.MATCHERINO_LOGIN_ALERTS_UPDATED,
            {'alert_user_id': user_id, 'previous_alert_user_id': login.alert_user_id},
        )
        return await self.status(actor)

    @requires_feature(FeatureFlag.EVENT_CHECK_IN)
    async def note_sync_result(self, error: Optional[Exception] = None) -> None:
        """Called by the sync after each Matcherino read: alert on a refusal, re-arm on success.

        Best-effort: a failure to alert never changes how the sync itself ends.
        """
        try:
            if error is None:
                await self.repository.clear_alert()
            elif isinstance(error, MatcherinoAuthError):
                await self._alert(str(error))
        except Exception:
            logger.exception('Matcherino login alert bookkeeping failed')

    async def _alert(self, reason: str) -> None:
        login = await self.repository.claim_alert(datetime.now(timezone.utc))
        recipient = login.alert_user if login else None
        if recipient is None or not recipient.discord_id:
            return
        if not await AuthService.can_manage_check_in(recipient):
            logger.warning('Matcherino login alert skipped: user %s is no longer staff', recipient.id)
            return
        from application.services import notification_links
        from application.services.discord import DiscordService, discord_queue
        from application.services.tenant_service import TenantService
        from application.utils.discord_embeds import COLOR_CANCELLED, notification_embed
        from application.utils.discord_messages_check_in import matcherino_login_refused_dm

        community = await TenantService.current_community_name()
        body = matcherino_login_refused_dm(community, reason)
        embed = notification_embed(
            title='🎟️ Matcherino login refused', color=COLOR_CANCELLED,
            community_name=community, description=body,
        )
        link = await notification_links.admin_matcherino_login()
        discord_queue.enqueue(DiscordService().send_dm(int(recipient.discord_id), body, embed=embed, link=link))
