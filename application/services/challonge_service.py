"""
Challonge Service - Business Logic Layer

Coordinates the Challonge integration: the Wizzrobe service-account OAuth
connection, per-player identity linking, pulling a tournament's bracket into
wizzrobe, scheduling bracket matchups (via the existing match-request flow), and
pushing results back to Challonge.

Credential model: one shared Wizzrobe service account writes brackets. Players link
their own Challonge identity (scope ``me``) only so we can map them to bracket
participants; their tokens are not retained.
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from application.errors import require_found
from application.feature_flags import requires_feature
from application.repositories import ChallongeRepository, TournamentRepository, UserRepository
from application.services._challonge_communities import ChallongeCommunitiesMixin, not_found_message
from application.services._challonge_participants import ChallongeParticipantAssignmentMixin
from application.services._challonge_signup import ChallongeSignupMixin, misc_user_id
from application.services._challonge_stages import ChallongeStageMixin
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.match.match_service import MatchService
from application.utils.clients.challonge_client import (
    ChallongeAPIError,
    ChallongeClient,
    MockChallongeClient,
    build_authorize_url,
)
from application.utils.environment import get_base_url
from application.utils.mocks.mock_challonge import is_mock_challonge
from models import (
    ChallongeConnection,
    ChallongeMatch,
    ChallongeMatchState,
    FeatureFlag,
    Match,
    Tournament,
    User,
)

logger = logging.getLogger(__name__)

# Refresh the service token slightly before it actually expires.
_TOKEN_REFRESH_BUFFER = timedelta(minutes=5)

# Collapse rapid repeat syncs (double-clicks, webhook bursts) — a non-forced
# sync within this window of the last one is treated as a no-op.
_SYNC_THROTTLE_WINDOW = timedelta(seconds=60)

# The Challonge plan's monthly request quota, surfaced in the admin UI.
CHALLONGE_MONTHLY_QUOTA = 500

_DEFAULT_SERVICE_SCOPES = (
    'me tournaments:read tournaments:write matches:read matches:write '
    'participants:read participants:write communities:manage'
)
_PLAYER_SCOPES = 'me'


def _redirect_uri() -> str:
    # One registered redirect URI serves both the service-account and the
    # per-player flows; Challonge OAuth apps only validate against a single URI.
    return os.getenv('CHALLONGE_REDIRECT_URI') or f"{get_base_url()}/challonge/oauth/callback"


def _service_scopes() -> str:
    return os.getenv('CHALLONGE_SCOPES') or _DEFAULT_SERVICE_SCOPES


def _as_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Treat naive datetimes (e.g. from a non-tz DB) as UTC so comparisons are safe."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


class ChallongeService(
    ChallongeParticipantAssignmentMixin, ChallongeStageMixin, ChallongeSignupMixin,
    ChallongeCommunitiesMixin,
):
    """Business logic for the Challonge integration."""

    def __init__(self) -> None:
        self.repository = ChallongeRepository()
        self.tournament_repository = TournamentRepository()
        self.audit_service = AuditService()

    # ------------------------------------------------------------------
    # OAuth configuration / URLs (reused by the OAuth middleware)
    # ------------------------------------------------------------------
    @staticmethod
    def is_configured() -> bool:
        """True when the OAuth app credentials are present (or mock is on)."""
        if is_mock_challonge():
            return True
        return bool(os.getenv('CHALLONGE_CLIENT_ID') and os.getenv('CHALLONGE_CLIENT_SECRET'))

    @staticmethod
    def service_authorize_url(state: str, community: Optional[str] = None) -> str:
        return build_authorize_url(
            os.getenv('CHALLONGE_CLIENT_ID', ''), _redirect_uri(), _service_scopes(), state,
            community_id=ChallongeService.normalize_community(community),
        )

    @staticmethod
    def normalize_community(value: Optional[str]) -> Optional[str]:
        """Reduce a typed community (``speedgaming``, ``speedgaming.challonge.com``,
        or any URL on it) to its bare subdomain, or None when it isn't one."""
        raw = (value or '').strip().lower()
        if not raw:
            return None
        if '.challonge.com' in raw:
            _, community = ChallongeService.parse_tournament_identifier(raw)
            raw = community or ''
        return raw if re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', raw) else None

    @staticmethod
    def player_authorize_url(state: str) -> str:
        return build_authorize_url(
            os.getenv('CHALLONGE_CLIENT_ID', ''), _redirect_uri(), _PLAYER_SCOPES, state,
        )

    @staticmethod
    def redirect_uri() -> str:
        return _redirect_uri()

    # ------------------------------------------------------------------
    # Client factories
    # ------------------------------------------------------------------
    def _oauth_client(self) -> ChallongeClient:
        """Client for token exchange/refresh and get_me (no bearer needed)."""
        cls = MockChallongeClient if is_mock_challonge() else ChallongeClient
        return cls(
            client_id=os.getenv('CHALLONGE_CLIENT_ID', ''),
            client_secret=os.getenv('CHALLONGE_CLIENT_SECRET', ''),
            on_request=self.repository.increment_api_usage,
        )

    def _api_client(self) -> ChallongeClient:
        """Client for authenticated service calls (token via get_valid_access_token)."""
        cls = MockChallongeClient if is_mock_challonge() else ChallongeClient
        return cls(
            client_id=os.getenv('CHALLONGE_CLIENT_ID', ''),
            client_secret=os.getenv('CHALLONGE_CLIENT_SECRET', ''),
            token_provider=self.get_valid_access_token,
            on_request=self.repository.increment_api_usage,
        )

    # ------------------------------------------------------------------
    # Service-account token management
    # ------------------------------------------------------------------
    async def get_valid_access_token(self, force_refresh: bool = False) -> str:
        """Return a live service access token, refreshing if expired/forced."""
        connection = await self.repository.get_connection()
        if connection is None:
            raise ValueError("Challonge is not connected. A staff member must connect the Wizzrobe account.")

        if not force_refresh and not self._token_expiring(connection):
            return connection.access_token

        if not connection.refresh_token:
            if force_refresh:
                raise ValueError("Challonge token expired and no refresh token is available; reconnect required.")
            return connection.access_token

        payload = await self._oauth_client().refresh(connection.refresh_token)
        access, refresh, expires_at = self._unpack_token(payload)
        await self.repository.update_connection_tokens(connection, access, refresh, expires_at)
        return access

    @staticmethod
    def _token_expiring(connection: ChallongeConnection) -> bool:
        if connection.token_expires_at is None:
            return False
        expires = _as_aware_utc(connection.token_expires_at)
        return datetime.now(timezone.utc) >= (expires - _TOKEN_REFRESH_BUFFER)

    @staticmethod
    def _unpack_token(payload: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[datetime]]:
        access = payload.get('access_token')
        if not access:
            raise ChallongeAPIError(f"Challonge token response missing access_token: {payload}")
        refresh = payload.get('refresh_token')
        expires_in = payload.get('expires_in')
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if expires_in else None
        )
        return access, refresh, expires_at

    # ------------------------------------------------------------------
    # Connection lifecycle (called by OAuth middleware)
    # ------------------------------------------------------------------
    @requires_feature(FeatureFlag.CHALLONGE)
    async def save_service_connection(
        self, token_payload: Dict[str, Any], actor: User, community: Optional[str] = None,
    ) -> ChallongeConnection:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only staff can connect the Wizzrobe Challonge account",
        )
        access, refresh, expires_at = self._unpack_token(token_payload)
        scopes = token_payload.get('scope') or _service_scopes()
        try:
            me = await self._oauth_client().get_me(access)
            username = me.get('username')
        except ChallongeAPIError:
            username = None
        connection = await self.repository.save_connection(
            access_token=access,
            refresh_token=refresh,
            token_expires_at=expires_at,
            scopes=scopes,
            challonge_username=username,
            connected_by=actor,
        )
        await self._remember_community(community)
        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_CONNECTED,
            {'challonge_username': username, 'community': self.normalize_community(community)},
        )
        return connection

    @requires_feature(FeatureFlag.CHALLONGE)
    async def disconnect(self, actor: User) -> None:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only staff can disconnect the Wizzrobe Challonge account",
        )
        await self.repository.clear_connection()
        await self.audit_service.write_log(actor, AuditActions.CHALLONGE_DISCONNECTED, {})

    @requires_feature(FeatureFlag.CHALLONGE)
    async def get_connection_status(self) -> Dict[str, Any]:
        connection = await self.repository.get_connection()
        usage = await self.repository.get_monthly_usage()
        base = {
            'configured': self.is_configured(),
            'request_usage': usage,
            'request_quota': CHALLONGE_MONTHLY_QUOTA,
        }
        if connection is None:
            return {'connected': False, **base}
        return {
            'connected': True,
            'challonge_username': connection.challonge_username,
            'scopes': connection.scopes,
            'token_expires_at': connection.token_expires_at,
            **base,
        }

    # ------------------------------------------------------------------
    # Player identity linking (called by player OAuth callback)
    # ------------------------------------------------------------------
    async def _write_player_link(
        self,
        user: User,
        challonge_user_id: str,
        challonge_username: Optional[str],
        actor: User,
        *,
        manual: bool,
    ) -> None:
        """Validate and record a player's Challonge identity, then audit it.

        ``User.challonge_user_id`` is ``unique=True`` (participant matching has to
        resolve one user per Challonge id), so the collision is checked up front:
        reaching the index instead raises ``IntegrityError``, which the OAuth
        callback turns into "Please try again." — advice that fails identically
        every time it is followed. The remaining race (two users linking the same
        id in the same instant) still lands on the index, and the generic message
        is correct for that.
        """
        cuid = (challonge_user_id or '').strip()
        if not cuid:
            raise ValueError('A Challonge account id is required to link this user.')
        existing = await User.filter(challonge_user_id=cuid).exclude(id=user.id).first()
        if existing is not None:
            raise ValueError(f'That Challonge account is already linked to {existing.username}.')
        user.challonge_user_id = cuid
        user.challonge_username = (challonge_username or '').strip() or None
        user.challonge_linked_at = datetime.now(timezone.utc)
        await user.save()
        details = {
            'user_id': user.id,
            'challonge_user_id': cuid,
            'challonge_username': user.challonge_username,
        }
        if manual:
            details['manual'] = True
        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_PLAYER_LINKED, details,
        )

    @requires_feature(FeatureFlag.CHALLONGE)
    async def record_player_link(
        self, user: User, challonge_user_id: str, challonge_username: Optional[str], actor: User,
    ) -> None:
        """Record a player's verified Challonge identity (the OAuth callback path)."""
        await self._write_player_link(
            user, challonge_user_id, challonge_username, actor, manual=False,
        )

    @staticmethod
    async def _ensure_may_link_for(user: User, actor: User) -> None:
        """Refuse a staff override on a Challonge identity that isn't only this community's.

        ``challonge_user_id`` is global and unique, and every community's bracket
        sync resolves participants through it. Pointing a shared account at
        someone else's Challonge id would hand them that person's bracket rows in
        every other community, and taking a real id would lock its owner out of
        linking it anywhere. So the override follows the rule for the other
        global account fields: Staff only, and only for people who belong to no
        other community.
        """
        if actor.id == user.id:
            return
        await AuthService.ensure(
            await AuthService.can_grant_roles(actor),
            'Only Staff can change another member’s Challonge link',
        )
        from application.services.user_service import UserService

        await UserService.ensure_may_edit_account(user, actor)

    @requires_feature(FeatureFlag.CHALLONGE)
    async def participant_tournament_ids(self, user: User) -> Set[int]:
        """Tournament IDs whose mirrored Challonge bracket includes this user.

        Bracket membership is what drives participation in a linked tournament,
        rather than any manual opt-in. Currently read only by tests: the Profile
        card that used it is gone, and the Tournaments tab needs only *whether* a
        tournament is Challonge-managed, not who is in its bracket. Kept as the
        query behind that question if a surface needs it again.
        """
        return await self.repository.participant_tournament_ids_for_user(user)

    @requires_feature(FeatureFlag.CHALLONGE)
    async def link_player_manually(
        self,
        user: User,
        challonge_user_id: str,
        challonge_username: Optional[str],
        actor: User,
    ) -> None:
        """Staff override to link a user to a Challonge account by id.

        Mirrors the OAuth ``record_player_link`` but is initiated by Staff (e.g.
        for a player who can't complete the OAuth flow) — the audit row carries
        ``manual`` to tell the two apart.
        """
        await self._ensure_may_link_for(user, actor)
        await self._write_player_link(
            user, challonge_user_id, challonge_username, actor, manual=True,
        )

    @requires_feature(FeatureFlag.CHALLONGE)
    async def set_player_username(
        self, user: User, challonge_username: Optional[str], actor: User,
    ) -> None:
        """Manually correct the linked Challonge username (Staff override).

        Participant matching keys off ``challonge_user_id``; this only fixes the
        display username, so it requires an already-linked account.
        """
        if not user.challonge_user_id:
            raise ValueError('This user has no linked Challonge account to edit.')
        new_username = (challonge_username or '').strip() or None
        if new_username == user.challonge_username:
            return
        await self._ensure_may_link_for(user, actor)
        old_username = user.challonge_username
        user.challonge_username = new_username
        await user.save()
        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_PLAYER_USERNAME_UPDATED,
            {'user_id': user.id, 'old_username': old_username, 'new_username': new_username},
        )

    @requires_feature(FeatureFlag.CHALLONGE)
    async def unlink_player(self, user: User, actor: User) -> None:
        user.challonge_user_id = None
        user.challonge_username = None
        user.challonge_linked_at = None
        await user.save()
        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_PLAYER_UNLINKED, {'user_id': user.id},
        )

    @requires_feature(FeatureFlag.CHALLONGE)
    async def exchange_player_code(self, code: str) -> Dict[str, Any]:
        """Exchange a player's authorization code and return their identity.

        Returns {'user_id', 'username'}. The token is used once and discarded.
        """
        payload = await self._oauth_client().exchange_code(code, _redirect_uri())
        access = payload.get('access_token')
        if not access:
            raise ChallongeAPIError(f"Challonge token response missing access_token: {payload}")
        return await self._oauth_client().get_me(access)

    @requires_feature(FeatureFlag.CHALLONGE)
    async def exchange_service_code(self, code: str) -> Dict[str, Any]:
        """Exchange the service account's authorization code for a token payload."""
        return await self._oauth_client().exchange_code(code, _redirect_uri())

    # ------------------------------------------------------------------
    # Tournament linking + bracket sync
    # ------------------------------------------------------------------
    @staticmethod
    def parse_tournament_identifier(id_or_url: str) -> Tuple[str, Optional[str]]:
        """Extract a Challonge ``(tournament identifier, community)`` from a raw id or URL.

        ``https://challonge.com/abc123``           -> ``('abc123', None)``
        ``https://user.challonge.com/abc123``       -> ``('abc123', 'user')``
        ``abc123`` / ``12345``                      -> ``(value, None)``

        The community is the organization subdomain; v2.1 resolves a subdomain
        tournament only under ``/communities/<subdomain>/``.
        """
        value = (id_or_url or '').strip()
        if not value:
            raise ValueError("Provide a Challonge tournament ID or URL.")
        if '://' not in value and '.challonge.com' not in value and '/' not in value:
            return value, None
        parsed = urlparse(value if '://' in value else f'https://{value}')
        slug = parsed.path.strip('/').split('/')[0]
        host = (parsed.hostname or '').lower()
        community = None
        if host.endswith('.challonge.com'):
            subdomain = host[:-len('.challonge.com')]
            if subdomain and subdomain != 'www':
                community = subdomain
        return slug or value, community

    @requires_feature(FeatureFlag.CHALLONGE)
    async def link_tournament(self, tournament_id: int, id_or_url: str, actor: User) -> Tournament:
        tournament = require_found(
            await self.tournament_repository.get_by_id(tournament_id), f"Tournament {tournament_id}"
        )
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only staff can link a tournament's Challonge bracket",
        )

        # Exclusivity: one bracket site per tournament (native, Challonge or start.gg); the
        # symmetric guards are ``BracketService._ensure_no_challonge_link`` and ``StartggService.link_event``.
        if tournament.startgg_event_slug:
            raise ValueError("This tournament follows a start.gg event; a tournament follows one bracket site.")
        from application.repositories import BracketRepository
        if await BracketRepository().list_for_tournament(tournament_id):
            raise ValueError("This tournament already has a native bracket; a tournament uses one bracket site.")

        identifier, community = self.parse_tournament_identifier(id_or_url)
        try:
            full = await self._api_client().get_tournament_full(identifier, community=community)
        except ChallongeAPIError as e:
            if community and e.status == 403:
                raise ValueError(
                    f"Challonge won't let the connected account read tournaments in the '{community}' "
                    f"community. In Admin → Challonge, enter '{community}' as the community and "
                    f"reconnect, signing in as one of its admins."
                ) from e
            if e.status == 404:
                raise ValueError(not_found_message(identifier)) from e
            raise ValueError(f"Could not find that Challonge tournament: {e}") from e

        remote = full['tournament']
        tournament.challonge_tournament_id = remote.get('id') or identifier
        tournament.challonge_community = community  # type: ignore[assignment]
        tournament.challonge_tournament_url = remote.get('url')
        # A bracket-run tournament schedules only what the bracket produced, so
        # close the manual player-request path. Staff can re-open it per
        # tournament from the tournament editor.
        tournament.allow_player_match_requests = False
        await tournament.save()

        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_TOURNAMENT_LINKED,
            {'tournament_id': tournament.id, 'challonge_tournament_id': tournament.challonge_tournament_id},
        )

        # The full fetch already carries the bracket, so mirror it without a
        # second round-trip.
        await self._mirror_bracket(tournament, full['participants'], full['matches'], actor)
        await self._record_sync(tournament, remote)
        return tournament

    @requires_feature(FeatureFlag.CHALLONGE)
    async def unlink_tournament(self, tournament_id: int, actor: User) -> Tournament:
        """Detach a tournament from Challonge and drop its mirrored bracket.

        The migration path off Challonge. Without it a community that wants to
        move an existing tournament onto a native bracket cannot: the exclusivity
        guard refuses the native bracket while the link stands, and no surface
        clears ``challonge_tournament_id`` (the tournament editor refuses a blank
        one). Nothing is deleted on Challonge's side — this only detaches the
        local mirror, so re-linking re-imports it.
        """
        tournament = require_found(
            await self.tournament_repository.get_by_id(tournament_id), f"Tournament {tournament_id}"
        )
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only staff can unlink a tournament's Challonge bracket",
        )
        if not tournament.challonge_tournament_id:
            raise ValueError("This tournament is not linked to a Challonge bracket")

        participants, matches = await self.repository.delete_mirror(tournament)
        details = {
            'tournament_id': tournament.id,
            'challonge_tournament_id': tournament.challonge_tournament_id,
            'participants_removed': participants,
            'matches_removed': matches,
        }
        tournament.challonge_tournament_id = None
        tournament.challonge_community = None  # type: ignore[assignment]
        tournament.challonge_tournament_url = None
        tournament.challonge_last_synced_at = None
        tournament.challonge_state = None  # type: ignore[assignment]
        tournament.challonge_group_stage = False
        await tournament.save()

        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_TOURNAMENT_UNLINKED, details,
        )
        return tournament

    @requires_feature(FeatureFlag.CHALLONGE)
    async def sync_bracket(self, tournament_id: int, actor: User, force: bool = False) -> Dict[str, int]:
        tournament = require_found(
            await self.tournament_repository.get_by_id(tournament_id), f"Tournament {tournament_id}"
        )
        await AuthService.ensure(
            await AuthService.can_edit_tournament(actor, tournament),
            "Only staff and this tournament's admins can sync its Challonge bracket",
        )
        return await self._sync_tournament(tournament, actor, force=force)

    async def _sync_tournament(
        self, tournament: Tournament, actor: User, force: bool = False,
    ) -> Dict[str, int]:
        """Fetch + mirror a linked bracket. Permission is the caller's concern.

        Shared by the admin sync action, the post-push auto re-sync, and a
        stage change. A non-forced call within the throttle window of the
        last successful sync is a no-op (returns cached counts, no API request).
        """
        if not tournament.challonge_tournament_id:
            raise ValueError("This tournament is not linked to a Challonge tournament yet.")

        if not force and self._synced_recently(tournament):
            return {
                'participants': await self.repository.count_participants(tournament),
                'matches': await self.repository.count_matches(tournament),
                'skipped': True,
            }

        full = await self._api_client().get_tournament_full(
            tournament.challonge_tournament_id, community=tournament.challonge_community,
        )
        result = await self._mirror_bracket(
            tournament, full['participants'], full['matches'], actor,
        )
        await self._record_sync(tournament, full['tournament'])
        return result

    async def _record_sync(self, tournament: Tournament, remote: Dict[str, Any]) -> None:
        await self.repository.record_sync(
            tournament, datetime.now(timezone.utc),
            remote.get('state'), bool(remote.get('group_stage')),
        )

    @staticmethod
    def _synced_recently(tournament: Tournament) -> bool:
        last = _as_aware_utc(tournament.challonge_last_synced_at)
        if last is None:
            return False
        return datetime.now(timezone.utc) - last < _SYNC_THROTTLE_WINDOW

    async def _mirror_bracket(
        self,
        tournament: Tournament,
        remote_participants: List[Dict[str, Any]],
        remote_matches: List[Dict[str, Any]],
        actor: User,
    ) -> Dict[str, int]:
        bookable_before = await self._bookable_match_ids(tournament)
        # Participants -> resolve to linked wizzrobe users by Challonge account id.
        # Resolve every linked user in one query rather than per participant.
        users_by_cuid = await self.repository.resolve_users_by_challonge_ids(
            [rp.get('challonge_user_id') for rp in remote_participants]
        )
        # A hand assignment outlives every sync: an entrant added by name has no
        # account to resolve, and staff chose this mapping on purpose.
        manual = {
            p.challonge_participant_id: p.user
            for p in await self.repository.list_participants(tournament)
            if p.user_assigned_manually
        }
        # An entrant a player added from the Tournaments tab carries no account
        # id until they accept Challonge's invite; its ``misc`` names them.
        users_by_misc = await UserRepository.get_by_ids([
            uid for uid in (misc_user_id(rp.get('misc')) for rp in remote_participants) if uid
        ])
        participant_by_cid: Dict[str, Any] = {}
        for rp in remote_participants:
            cuid = rp.get('challonge_user_id')
            if rp['participant_id'] in manual:
                user = manual[rp['participant_id']]
            else:
                user = users_by_cuid.get(cuid) if cuid else None
                misc_uid = misc_user_id(rp.get('misc'))
                if user is None and misc_uid is not None:
                    user = users_by_misc.get(misc_uid)
            participant = await self.repository.upsert_participant(
                tournament=tournament,
                challonge_participant_id=rp['participant_id'],
                name=rp.get('name'),
                challonge_user_id=cuid,
                user=user,
            )
            participant_by_cid[rp['participant_id']] = participant

        # Matches -> mirror with resolved participant FKs.
        for rm in remote_matches:
            p1 = participant_by_cid.get(rm.get('player1_participant_id'))
            p2 = participant_by_cid.get(rm.get('player2_participant_id'))
            winner = participant_by_cid.get(rm.get('winner_participant_id'))
            await self.repository.upsert_match(
                tournament=tournament,
                challonge_match_id=rm['match_id'],
                round_=rm.get('round'),
                state=self._map_state(rm.get('state')),
                participant1=p1,
                participant2=p2,
                winner_participant=winner,
            )

        # A reset or cleared roster on Challonge; a hand assignment goes with its entrant.
        removed_p, removed_m = await self.repository.prune_mirror(
            tournament, set(participant_by_cid), {rm['match_id'] for rm in remote_matches},
        )
        await self._mirror_players(tournament, actor)
        result = {'participants': len(remote_participants), 'matches': len(remote_matches)}
        await self.audit_service.write_log(actor, AuditActions.CHALLONGE_BRACKET_SYNCED, {
            'tournament_id': tournament.id, **result,
            'participants_removed': removed_p, 'matches_removed': removed_m,
        })
        await self._announce_newly_bookable(tournament, bookable_before)
        return result

    @staticmethod
    def _map_state(state: Optional[str]) -> ChallongeMatchState:
        if state == 'open':
            return ChallongeMatchState.OPEN
        if state == 'complete':
            return ChallongeMatchState.COMPLETE
        return ChallongeMatchState.PENDING

    # ------------------------------------------------------------------
    # Scheduling a bracket matchup (reuses the existing match-request flow)
    # ------------------------------------------------------------------
    @requires_feature(FeatureFlag.CHALLONGE)
    async def list_unscheduled_matches_for_user(self, user: User) -> List[ChallongeMatch]:
        return await self.repository.unscheduled_open_matches_for_user(user)

    @requires_feature(FeatureFlag.CHALLONGE)
    async def schedule_challonge_match(
        self,
        challonge_match_pk: int,
        scheduled_date: str,
        scheduled_time: str,
        actor: User,
        comment: Optional[str] = None,
    ) -> Match:
        cmatch = require_found(
            await self.repository.get_match(challonge_match_pk), "Challonge match"
        )
        if cmatch.match_id is not None:
            raise ValueError("This match has already been scheduled.")
        if cmatch.state != ChallongeMatchState.OPEN:
            raise ValueError("This match isn't ready to schedule yet.")

        p1, p2 = cmatch.participant1, cmatch.participant2
        if p1 is None or p2 is None or p1.user is None or p2.user is None:
            raise ValueError(
                "Both players must link their Challonge account before this match can be scheduled."
            )

        match = await MatchService().submit_match_request(
            tournament_id=cmatch.tournament_id,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            player_ids=[p1.user_id, p2.user_id],
            actor=actor,
            comment=comment,
            # The bracket is the sanctioned scheduling path for a Challonge-linked
            # tournament, so this bypasses its allow_player_match_requests toggle.
            from_bracket=True,
        )
        await self.repository.link_match(cmatch, match)
        return match

    # ------------------------------------------------------------------
    # Pushing results back to Challonge
    # ------------------------------------------------------------------
    # feature-gate: exempt — the soft integration point; see the docstring below.
    async def push_result_if_linked(self, match: Match, actor: Optional[User]) -> bool:
        """Push the result only when the match mirrors a Challonge match.

        Returns True if a push was attempted. Used by the confirm flow so
        non-Challonge matches are silently skipped. Requires an actor for the
        audit entry; with none, the push is skipped.

        Soft-gated: the match confirm/finish flow calls this for *every* match, so
        a community without the Challonge feature must simply confirm its matches
        — raising here would break match completion for everyone who does not use
        Challonge. The flag is still enforced (no result leaves the tenant where
        the feature is off) by answering "nothing pushed"; the administrative
        entry points above raise instead.
        """
        if actor is None:
            return False
        if not await FeatureFlagService().is_enabled(FeatureFlag.CHALLONGE):
            return False
        cmatch = await self.repository.get_challonge_match_for_match(match)
        if cmatch is None:
            return False
        await self.push_match_result(match, actor)
        return True

    @requires_feature(FeatureFlag.CHALLONGE)
    async def push_match_result(self, match: Match, actor: User) -> None:
        cmatch = await self.repository.get_challonge_match_for_match(match)
        if cmatch is None:
            raise ValueError("This match is not linked to a Challonge match.")

        await match.fetch_related('players__user', 'tournament')
        ranked = [p for p in match.players if p.finish_rank is not None]
        winner = next((p for p in ranked if p.finish_rank == 1), None)
        if winner is None:
            raise ValueError("No winner has been recorded for this match yet.")
        losers = [p for p in match.players if p.id != winner.id]
        if not losers:
            raise ValueError("Cannot push a result without an opponent.")
        loser = losers[0]

        winner_pid = self._participant_id_for_user(cmatch, winner.user_id)
        loser_pid = self._participant_id_for_user(cmatch, loser.user_id)
        if winner_pid is None or loser_pid is None:
            raise ValueError("Could not map both players to Challonge participants; re-sync the bracket.")

        if not cmatch.tournament.challonge_tournament_id:
            raise ValueError("The tournament is no longer linked to Challonge.")

        await self._api_client().update_match(
            tournament_id=cmatch.tournament.challonge_tournament_id,
            match_id=cmatch.challonge_match_id,
            winner_participant_id=winner_pid,
            loser_participant_id=loser_pid,
            community=cmatch.tournament.challonge_community,
        )
        await self.audit_service.write_log(
            actor, AuditActions.CHALLONGE_RESULT_PUSHED,
            {'match_id': match.id, 'challonge_match_id': cmatch.challonge_match_id,
             'winner_participant_id': winner_pid},
        )

        # The push advanced the bracket (next-round matches now open). Re-sync
        # once so those newly-open matches surface locally without a manual
        # Sync. Failures here must not undo the successful push.
        try:
            await self._sync_tournament(cmatch.tournament, actor, force=True)
        except (ValueError, ChallongeAPIError):
            # Logged rather than printed so a silently-stale bracket leaves a
            # diagnostic trail in structured logs and Sentry.
            logger.error(
                "Challonge post-push re-sync failed for tournament %s",
                cmatch.tournament_id, exc_info=True,
            )

    @staticmethod
    def _participant_id_for_user(cmatch: ChallongeMatch, user_id: int) -> Optional[str]:
        for participant in (cmatch.participant1, cmatch.participant2):
            if participant is not None and participant.user_id == user_id:
                return participant.challonge_participant_id
        return None
