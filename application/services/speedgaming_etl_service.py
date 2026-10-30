"""SpeedGaming ETL service (PR 7) — one-way SG → Wizzrobe schedule sync.

Ported from sahabot2's ``speedgaming_etl_service``, adapted to Wizzrobe's
three-layer + multitenant shape. The pipeline is:

- **extract** — the sync worker polls the SG schedule API per active event link
  over a forward window (:class:`SpeedGamingClient`);
- **transform** — normalize each episode to UTC and resolve every player to a
  ``User`` via the **placeholder pattern** (:meth:`_find_or_create_user`);
- **load** — upsert a :class:`SpeedGamingEpisode` staging row, then
  materialize/refresh its ``Match`` + ``MatchPlayers``.

Two guards keep the sync from stomping race-day work (the hybrid read-only
contract): a re-sync **skips** a match that is finished / manually progressed /
racetime-room-linked, and **auto-finishes** SG-sourced matches more than 4h past
their scheduled time (unless a room is linked). The per-field read-only lock — a
staff edit to an ETL-owned field on a sourced match — lives in
``MatchService.update_match``; this service owns the sync side.

The sync runs as the reserved **system ``User``** (audit/event actor), always
inside a ``tenant_scope`` established by the worker.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from application.events import EventType
from application.repositories import (
    MatchAcknowledgmentRepository,
    MatchRepository,
    SpeedGamingEpisodeRepository,
    SpeedGamingEventLinkRepository,
    TournamentRepository,
    UserRepository,
)
from application.repositories.racetime_room_repository import RacetimeRoomRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.match.match_participants import MatchParticipants
from application.services.tenant_membership_service import TenantMembershipService
from application.utils.clients.speedgaming_client import (
    SpeedGamingAPIError,
    SpeedGamingClient,
    get_speedgaming_client,
)
from application.utils.hashing import stable_content_hash
from models import Match, MembershipSource, SpeedGamingEventLink, SyncStatus, User

logger = logging.getLogger(__name__)

# A match more than this many hours past its scheduled start with no linked room
# and no manual close is auto-finished on the next sync (sahabot2 behaviour).
AUTO_FINISH_HOURS = 4
# Small backfill grace on the lower bound so a just-passed episode is still
# pulled (e.g. after a brief worker outage).
BACKFILL_GRACE_HOURS = 1
# Width of ``Match.title`` / ``SpeedGamingEpisode.title``. SG's titles are
# unbounded free text, so an over-long one would fail the whole episode.
TITLE_MAX_LENGTH = 255


@dataclass
class SyncResult:
    """Tally of one event-link sync pass (surfaced in the admin UI + audit)."""

    imported: int = 0
    unchanged: int = 0
    skipped: int = 0
    cancelled: int = 0
    auto_finished: int = 0
    errors: int = 0
    error_messages: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            'imported': self.imported,
            'unchanged': self.unchanged,
            'skipped': self.skipped,
            'cancelled': self.cancelled,
            'auto_finished': self.auto_finished,
            'errors': self.errors,
        }


class SpeedGamingETLService:
    """Extract/transform/load SpeedGaming episodes into Wizzrobe ``Match`` rows."""

    def __init__(self, client: Optional[SpeedGamingClient] = None) -> None:
        self.client = client or get_speedgaming_client()
        self.episode_repo = SpeedGamingEpisodeRepository()
        self.event_link_repo = SpeedGamingEventLinkRepository()
        self.room_repo = RacetimeRoomRepository()
        self.audit_service = AuditService()

    async def sync_event_link(
        self,
        link: SpeedGamingEventLink,
        *,
        actor: User,
        now: Optional[datetime] = None,
    ) -> SyncResult:
        """Sync one active event link. Assumes the ambient tenant is ``link.tenant``.

        Extracts the window, imports every returned episode, soft-detaches
        episodes that vanished upstream, auto-finishes stale matches, and records
        the link's observability fields (`last_synced_at` / `last_status` /
        `last_error`). Never raises for a per-episode failure — those are tallied.
        """
        now = now or datetime.now(timezone.utc)
        result = SyncResult()
        window_start = now - timedelta(hours=BACKFILL_GRACE_HOURS)
        window_end = now + timedelta(hours=link.lookahead_hours or 72)

        try:
            raw_episodes = await self.client.fetch_schedule(
                link.event_slug, window_start, window_end, link.content_type
            )
        except SpeedGamingAPIError as e:
            logger.error('SpeedGaming fetch for %s failed: %s', link.event_slug, e)
            await self.event_link_repo.update(
                link, last_synced_at=now, last_status='error', last_error=str(e)[:1000]
            )
            await self.audit_service.write_log(
                actor, AuditActions.SG_SYNC_FAILED,
                {'event_link_id': link.id, 'event_slug': link.event_slug, 'error': str(e)[:500]},
            )
            result.errors += 1
            result.error_messages.append(str(e))
            return result

        seen_sg_ids: set[str] = set()
        for raw in raw_episodes:
            sg_id = self._episode_id(raw)
            if sg_id is None:
                continue
            seen_sg_ids.add(sg_id)
            try:
                outcome = await self.import_episode(link, raw, actor=actor, now=now)
                setattr(result, outcome, getattr(result, outcome) + 1)
            except Exception as e:  # per-episode isolation
                logger.exception('SG import failed for episode %s (link %s)', sg_id, link.id)
                result.errors += 1
                result.error_messages.append(f'{sg_id}: {e}')

        # An empty answer is far likelier an SG hiccup than every episode being
        # cancelled at once; detaching them all would spray cancel webhooks.
        if raw_episodes:
            result.cancelled += await self._detect_deleted_episodes(link, seen_sg_ids, actor=actor, now=now)
        result.auto_finished += await self._auto_finish_stale(link, actor=actor, now=now)

        status = 'ok' if result.errors == 0 else 'partial'
        await self.event_link_repo.update(
            link,
            last_synced_at=now,
            last_status=status,
            last_error=None if result.errors == 0 else '; '.join(result.error_messages[:3])[:1000],
        )
        await self.audit_service.write_log(
            actor, AuditActions.SG_SYNC_COMPLETED,
            {'event_link_id': link.id, 'event_slug': link.event_slug, **result.as_dict()},
        )
        return result

    async def import_episode(
        self,
        link: SpeedGamingEventLink,
        raw: Dict[str, Any],
        *,
        actor: User,
        now: Optional[datetime] = None,
    ) -> str:
        """Upsert one raw SG episode into its staging row + ``Match``.

        Returns the outcome key (``'imported'`` / ``'unchanged'`` / ``'skipped'``)
        so the caller can tally it. Raises on a transform/load failure (the caller
        isolates it and marks the episode ERROR).
        """
        now = now or datetime.now(timezone.utc)
        sg_id = self._episode_id(raw)
        when = self._parse_when(raw.get('when'))
        title = self._episode_title(raw)
        content_hash = stable_content_hash(self._sync_fingerprint(raw))

        episode = await self.episode_repo.get_by_sg_id(sg_id)
        if episode is None:
            episode = await self.episode_repo.create(
                sg_episode_id=sg_id,
                event_link_id=link.id,
                title=title,
                scheduled_at=when,
                payload=raw,
                content_hash=content_hash,
                sync_status=SyncStatus.PENDING,
            )

        match = await MatchRepository.get_by_speedgaming_episode(episode.id)

        # Lifecycle guard: never overwrite race-day work on a sourced match.
        if match is not None:
            reason = await self._skip_reason(match)
            if reason is not None:
                await self.episode_repo.update(
                    episode, sync_status=SyncStatus.SKIPPED, synced_at=now,
                    payload=raw, content_hash=content_hash, sync_error=None,
                )
                await self.audit_service.write_log(
                    actor, AuditActions.SG_EPISODE_SKIPPED,
                    {'episode_id': episode.id, 'sg_episode_id': sg_id,
                     'match_id': match.id, 'reason': reason},
                )
                return 'skipped'

        # Unchanged shortcut: nothing the loader reads moved, and the Match is
        # already materialized. The raw snapshot is still refreshed — the
        # fingerprint deliberately ignores crew churn, so without this the
        # stored payload would freeze at whatever the last import happened to see.
        if (
            match is not None
            and episode.content_hash == content_hash
            and episode.sync_status == SyncStatus.SYNCED
        ):
            await self.episode_repo.update(episode, synced_at=now, payload=raw)
            return 'unchanged'

        if when is None:
            await self.episode_repo.update(
                episode, sync_status=SyncStatus.ERROR, synced_at=now,
                sync_error='episode has no scheduled time (`when`)',
            )
            raise ValueError(f'SG episode {sg_id} has no scheduled time')

        # Transform: resolve every player to a User (placeholder where unmatched).
        resolved_players = []
        for player in self._extract_players(raw):
            resolved_players.append(await self._find_or_create_user(player, actor=actor))

        created = match is None
        if match is None:
            match = await MatchRepository.create(
                tournament_id=link.tournament_id, scheduled_at=when,
            )
            await MatchRepository.update(match, speedgaming_episode_id=episode.id, title=title)
        else:
            await MatchRepository.update(match, scheduled_at=when, title=title)

        await self._sync_match_players(match, link.tournament_id, resolved_players, actor=actor)

        await self.episode_repo.update(
            episode, sync_status=SyncStatus.SYNCED, synced_at=now,
            payload=raw, content_hash=content_hash, scheduled_at=when,
            title=title, sync_error=None,
        )
        await self.audit_service.write_and_publish(
            actor, AuditActions.SG_EPISODE_IMPORTED,
            {'episode_id': episode.id, 'sg_episode_id': sg_id, 'match_id': match.id,
             'created': created, 'player_count': len(resolved_players)},
            EventType.SG_EPISODE_IMPORTED,
            event_details={
                'match_id': match.id, 'tournament_id': link.tournament_id,
                'sg_episode_id': sg_id, 'created': created,
            },
        )
        return 'imported'

    async def _find_or_create_user(self, player: Dict[str, Any], *, actor: User) -> User:
        """Resolve an SG player to a ``User``, and make them a member here.

        Membership is what every per-community person list joins through, so an
        imported player who is not a member would be invisible in the very
        community whose schedule they appear on. Idempotent and unaudited — the
        import itself is audited.
        """
        user = await self._resolve_sg_user(player, actor=actor)
        await TenantMembershipService.ensure_member(
            user, actor=actor, source=MembershipSource.IMPORT,
        )
        return user

    async def _resolve_sg_user(self, player: Dict[str, Any], *, actor: User) -> User:
        """Resolve an SG player to a ``User`` (placeholder pattern).

        Resolution order (decided, from sahabot2):
        ``discord_id`` → ``discord_username`` → placeholder-by-``speedgaming_id``
        → create a placeholder. When a ``discord_id`` appears for a player that
        previously only had a placeholder, the placeholder is **upgraded in
        place** instead of forking a second row.

        SG expresses "no value" as ``''`` rather than ``null`` on every one of
        these fields, and serves ``discordId`` as a string, so each is read for
        truthiness and the snowflake is only parsed once it looks like one — a
        malformed id degrades to tag/placeholder resolution instead of failing
        the whole episode.
        """
        sg_id = self._opt_str(player.get('id'))
        discord_id = (self._opt_str(player.get('discordId')) or '').strip()
        discord_tag = (self._opt_str(player.get('discordTag')) or '').strip() or None
        display = (self._opt_str(player.get('displayName')) or '').strip() or discord_tag or (f'sg_{sg_id}' if sg_id else 'sg_unknown')

        # 1. Resolve by discord id (and upgrade a matching placeholder in place).
        if discord_id.isdigit():
            did = int(discord_id)
            existing = await UserRepository.get_by_discord_id(did)
            if existing is not None:
                return existing
            if sg_id:
                placeholder = await UserRepository.get_placeholder_by_speedgaming_id(sg_id)
                if placeholder is not None:
                    await UserRepository.upgrade_placeholder(placeholder, did, username=discord_tag or display)
                    await self.audit_service.write_log(
                        actor, AuditActions.SG_PLACEHOLDER_UPGRADED,
                        {'user_id': placeholder.id, 'speedgaming_id': sg_id, 'discord_id': did},
                    )
                    return placeholder
            user, _ = await UserRepository.get_or_create_by_discord_id(did, username=discord_tag or display)
            return user

        # 2. Resolve by discord username (SG discord_tag → existing real account).
        if discord_tag:
            existing = await UserRepository.get_by_username(discord_tag)
            if existing is not None:
                return existing

        # 3/4. Existing placeholder, else create one.
        if sg_id:
            placeholder = await UserRepository.get_placeholder_by_speedgaming_id(sg_id)
            if placeholder is not None:
                return placeholder
            placeholder = await UserRepository.create_placeholder(
                sg_id, username=f'sg_{sg_id}', display_name=display,
            )
            await self.audit_service.write_log(
                actor, AuditActions.SG_PLACEHOLDER_CREATED,
                {'user_id': placeholder.id, 'speedgaming_id': sg_id, 'display_name': display},
            )
            return placeholder

        # No SG id and no discord identity — synthesize a stable placeholder key.
        synthetic = f'anon_{hashlib.sha1(display.encode()).hexdigest()[:16]}'
        placeholder = await UserRepository.get_placeholder_by_speedgaming_id(synthetic)
        if placeholder is not None:
            return placeholder
        placeholder = await UserRepository.create_placeholder(
            synthetic, username=display, display_name=display,
        )
        await self.audit_service.write_log(
            actor, AuditActions.SG_PLACEHOLDER_CREATED,
            {'user_id': placeholder.id, 'speedgaming_id': synthetic, 'display_name': display},
        )
        return placeholder

    def _participants(self) -> MatchParticipants:
        """Roster orchestrator bound to the ETL's repositories."""
        return MatchParticipants(
            match_repository=MatchRepository(),
            user_repository=UserRepository(),
            tournament_repository=TournamentRepository(),
            ack_repository=MatchAcknowledgmentRepository(),
        )

    async def _sync_match_players(
        self, match: Match, tournament_id: int, users: List[User], *, actor: User
    ) -> None:
        """Reconcile a sourced match's players to the resolved SG set.

        The ETL owns the roster on a sourced match, so this is a full replace:
        enroll+add new users, remove players no longer in the episode. Delegates
        to :class:`MatchParticipants` (the shared batch roster syncer), which
        dedupes so a player listed twice upstream yields one row.

        Acknowledgment rows are then *reconciled*, not seeded: a sourced match
        used to carry none at all, which made its players invisible to the
        entire acknowledgment surface — no icon on the board, no Acknowledge
        button for the player, and an admin dialog that reported nobody
        assigned. Reconciling preserves the answers already given, which is what
        lets a poll that runs every few minutes do this at all.

        Hard-preset opt-ins are reconciled on the same footing: these are the
        matches whose rosters change without anybody in the app touching them,
        so a player dropped upstream would otherwise keep counting toward an
        agreement, and one dropped and re-added would come back already opted
        in to a roster they never saw.
        """
        from application.services.match import MatchHardPresetService

        participants = self._participants()
        hard_preset_service = MatchHardPresetService()
        user_ids = [user.id for user in users]
        before = await hard_preset_service.snapshot(match)
        await participants.sync_players(match, user_ids, tournament_id)
        await participants.reconcile_acknowledgments(match, user_ids)
        await hard_preset_service.reconcile_edit(match, before, actor)

    async def _skip_reason(self, match: Match) -> Optional[str]:
        """Why a re-sync must not overwrite this match, or None if it may."""
        if match.finished_at is not None:
            return 'finished'
        if match.seated_at is not None or match.started_at is not None or match.confirmed_at is not None:
            return 'manual_status'
        if await self.room_repo.get_by_match(match) is not None:
            return 'racetime_room_linked'
        return None

    async def _detect_deleted_episodes(
        self, link: SpeedGamingEventLink, seen_sg_ids: set[str], *, actor: User, now: datetime
    ) -> int:
        """Soft-detach episodes that vanished upstream since the last sync.

        A previously-synced episode absent from the current window is marked
        CANCELLED — its ``Match`` and everything Wizzrobe added survive; only the
        staging row's status changes (the ETL-owned fields freeze at last value).
        """
        cancelled = 0
        for episode in await self.episode_repo.list_for_link(link.id):
            if episode.sg_episode_id in seen_sg_ids:
                continue
            if episode.sync_status == SyncStatus.CANCELLED:
                continue
            await self.episode_repo.update(episode, sync_status=SyncStatus.CANCELLED, synced_at=now)
            match = await MatchRepository.get_by_speedgaming_episode(episode.id)
            await self.audit_service.write_and_publish(
                actor, AuditActions.SG_EPISODE_CANCELLED,
                {'episode_id': episode.id, 'sg_episode_id': episode.sg_episode_id,
                 'match_id': match.id if match else None},
                EventType.SG_EPISODE_CANCELLED,
                event_details={
                    'sg_episode_id': episode.sg_episode_id,
                    'match_id': match.id if match else None,
                    'tournament_id': link.tournament_id,
                },
            )
            cancelled += 1
        return cancelled

    async def _auto_finish_stale(
        self, link: SpeedGamingEventLink, *, actor: User, now: datetime
    ) -> int:
        """Auto-finish SG-sourced matches >4h past with no linked room (sahabot2)."""
        cutoff = now - timedelta(hours=AUTO_FINISH_HOURS)
        finished = 0
        for match in await MatchRepository.list_sourced_stale(cutoff, link.tournament_id):
            room = await self.room_repo.get_by_match(match)
            if room is not None:
                continue
            await MatchRepository.update(match, finished_at=now)
            await self.audit_service.write_and_publish(
                actor, AuditActions.SG_MATCH_AUTO_FINISHED,
                {'match_id': match.id, 'tournament_id': link.tournament_id,
                 'scheduled_at': match.scheduled_at.isoformat() if match.scheduled_at else None},
                EventType.SG_MATCH_AUTO_FINISHED,
                event_details={
                    'match_id': match.id, 'tournament_id': link.tournament_id,
                },
            )
            finished += 1
        return finished

    @staticmethod
    def _episode_id(raw: Dict[str, Any]) -> Optional[str]:
        raw_id = raw.get('id')
        return None if raw_id is None else str(raw_id)

    @staticmethod
    def _extract_players(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Flatten SG ``match1``/``match2`` player lists into one list.

        SG models a head-to-head as ``match1``/``match2`` (each a side that may be
        a team); the Wizzrobe bracket match is the union of their players.
        """
        players: List[Dict[str, Any]] = []
        for side_key in ('match1', 'match2'):
            side = raw.get(side_key) or {}
            for player in side.get('players', []) or []:
                if isinstance(player, dict):
                    players.append(player)
        return players

    @classmethod
    def _episode_title(cls, raw: Dict[str, Any]) -> Optional[str]:
        """Best human label for an episode, in SG's own order of specificity.

        SG does serve an episode-level ``title``, but the live feed leaves it
        empty on every episode — the matchup name lives at ``match1.title``.
        When that is empty too (a TBD matchup, or a doubleheader where the label
        is on the slot rather than the pairing), the side's free-text ``note`` is
        the only descriptor left: "Swiss Round 5", "Game 2", "Showcase Race 5 &
        6". Preferring a note over nothing is what keeps those matches from
        showing up on the board as untitled rows.
        """
        candidates = [raw.get('title')]
        sides = [raw.get(key) or {} for key in ('match1', 'match2')]
        candidates.extend(side.get('title') for side in sides)
        candidates.extend(side.get('note') for side in sides)
        for candidate in candidates:
            cleaned = (candidate or '').strip()
            if cleaned:
                # SG's titles are unbounded free text; the column is not.
                return cleaned[:TITLE_MAX_LENGTH]
        return None

    @classmethod
    def _sync_fingerprint(cls, raw: Dict[str, Any]) -> Dict[str, Any]:
        """The subset of an episode the loader actually consumes.

        Hashing the whole payload made ``unchanged`` almost unreachable: crew
        signups carry ``approved`` and ``ready`` flags that flip as people sign
        up and staff approve them, so nearly every poll saw a "new" payload and
        re-materialized the match — a fresh audit row and a published event for
        a change the ETL does not even read. Widen this the moment the loader
        starts reading another field.
        """
        return {
            'when': raw.get('when'),
            'title': cls._episode_title(raw),
            'players': [
                {key: player.get(key)
                 for key in ('id', 'displayName', 'discordId', 'discordTag')}
                for player in cls._extract_players(raw)
            ],
        }

    @staticmethod
    def _parse_when(when: Any) -> Optional[datetime]:
        """Parse SG's ISO ``when`` into a UTC-aware datetime."""
        if not when:
            return None
        try:
            parsed = datetime.fromisoformat(str(when).replace('Z', '+00:00'))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _opt_str(value: Any) -> Optional[str]:
        return None if value is None else str(value)
