"""Discord Events reconciler (PR 8) — mirror Wizzrobe's schedule into Discord.

Keeps each tenant guild's **Discord Scheduled Events** in sync with the tenant's
schedule. This is *idempotent reconciliation*, not fire-and-forget creation: for
every scheduled match in an opted-in tournament we create / update / cancel a
Discord event so the guild reflects the current schedule, and a ``content_hash``
makes an unchanged pass a cheap no-op.

**Shared-guild safety is the sharp edge.** Since PR #85/#86 a Discord guild is no
longer unique to one tenant — several communities may share one server, so its
Scheduled Events list can hold *sibling* tenants' events. The reconciler's working
set is therefore **only this tenant's own ``DiscordScheduledEvent`` rows** (the
repository is tenant-scoped, so a query can't even see a sibling's row). It never
enumerates "every event in the guild" and never cancels an event it didn't create.
A ``discord_event_id`` present in the guild but absent from this tenant's link
table belongs to someone else and is left untouched.

The target guild is the **verified** ``Tenant.discord_guild_id`` (established by
``DiscordLinkService``), never a claimed id. Runs as the reserved system ``User``
on the worker, or as the acting staff member on an on-demand sync; either way
inside a ``tenant_scope`` the caller establishes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from application.events import EventType
from application.repositories import DiscordScheduledEventRepository, MatchRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.discord.discord_scheduled_events import SETUP_FAILURES
from application.services.discord.discord_service import DiscordService
from application.utils.hashing import stable_content_hash
from models import DiscordEventSource, DiscordScheduledEvent, Match, Tenant, User

logger = logging.getLogger(__name__)

# How far ahead to mirror matches, and a small grace on the lower bound so a match
# that just started still keeps its event until it finishes / ages out.
LOOKAHEAD_DAYS = 14
BACKFILL_GRACE_HOURS = 2

DEFAULT_TITLE_TEMPLATE = '{match}'
DEFAULT_DESCRIPTION_TEMPLATE = '{tournament}: {players}'


@dataclass
class ReconcileResult:
    """Tally of one tenant's reconcile pass (surfaced in the admin UI + audit)."""

    created: int = 0
    updated: int = 0
    cancelled: int = 0
    unchanged: int = 0
    errors: int = 0
    error_messages: List[str] = field(default_factory=list)
    #: Set when the pass stopped on a problem staff fix in Discord (the bot's
    #: permissions, or it isn't in the server), which every event would hit.
    setup_error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            'created': self.created,
            'updated': self.updated,
            'cancelled': self.cancelled,
            'unchanged': self.unchanged,
            'errors': self.errors,
            'setup_error': self.setup_error,
        }


class DiscordEventSetupError(RuntimeError):
    """The guild isn't set up for the bot to manage events; see :data:`SETUP_FAILURES`."""


def _failure(message: str) -> RuntimeError:
    return DiscordEventSetupError(message) if message in SETUP_FAILURES else RuntimeError(message)


class DiscordEventReconcilerService:
    """Reconcile a tenant's Discord Scheduled Events against its schedule."""

    def __init__(self) -> None:
        self.repo = DiscordScheduledEventRepository()
        self.discord = DiscordService()
        self.audit = AuditService()

    async def reconcile_tenant(
        self, tenant: Tenant, *, actor: User, now: Optional[datetime] = None,
    ) -> ReconcileResult:
        """Reconcile every mirrored event this tenant owns. Assumes ambient tenant.

        Does nothing (and reports zero) when the tenant has no linked guild — the
        mirror requires a verified ``discord_guild_id``. Never raises for a
        per-event Discord failure; those are tallied so one bad event never stops
        the rest.
        """
        result = ReconcileResult()
        guild_id = tenant.discord_guild_id
        if guild_id is None:
            return result

        now = now or datetime.now(timezone.utc)
        window_start = now - timedelta(hours=BACKFILL_GRACE_HOURS)
        window_end = now + timedelta(days=LOOKAHEAD_DAYS)

        matches = await MatchRepository.list_for_discord_sync(window_start, window_end)
        desired: Dict[int, Match] = {m.id: m for m in matches}

        existing = await self.repo.list_for_source_type(DiscordEventSource.MATCH)
        existing_by_source: Dict[int, DiscordScheduledEvent] = {e.source_id: e for e in existing}

        if matches or existing:
            problem = await self.discord.bot_event_problem(guild_id)
            if problem is not None:
                # Paused, not failed: staff fix this in Discord, and the page says so.
                logger.warning('Discord event reconcile paused for tenant %s: %s', tenant.id, problem)
                result.setup_error = problem
                return result

        try:
            for match in matches:
                try:
                    await self._reconcile_match(
                        tenant, guild_id, match, existing_by_source.get(match.id),
                        result, actor=actor, now=now,
                    )
                except DiscordEventSetupError:
                    raise
                except Exception as e:  # per-event isolation
                    logger.exception('Discord event reconcile failed for match %s', match.id)
                    result.errors += 1
                    result.error_messages.append(f'match {match.id}: {e}')

            # Cancel mirrored events whose source is gone from the desired set — only
            # rows THIS tenant owns are ever iterated (repo is tenant-scoped), so a
            # sibling tenant's event in a shared guild is never touched.
            for link in existing:
                if link.source_id in desired:
                    continue
                try:
                    await self._cancel(tenant, guild_id, link, result, actor=actor)
                except DiscordEventSetupError:
                    raise
                except Exception as e:
                    logger.exception('Discord event cancel failed for link %s', link.id)
                    result.errors += 1
                    result.error_messages.append(f'cancel {link.id}: {e}')
        except DiscordEventSetupError as e:
            # Sentry SGLMAN-18: a missing permission is staff's to fix in Discord,
            # and every remaining event would fail the same way.
            logger.warning('Discord event reconcile stopped for tenant %s: %s', tenant.id, e)
            result.errors += 1
            result.setup_error = str(e)
            result.error_messages.append(str(e))

        action = AuditActions.DISCORD_EVENT_SYNC_COMPLETED if result.errors == 0 else AuditActions.DISCORD_EVENT_SYNC_FAILED
        await self.audit.write_log(
            actor, action,
            {'tenant_id': tenant.id, 'guild_id': guild_id, **result.as_dict()},
        )
        return result

    async def _reconcile_match(
        self, tenant: Tenant, guild_id: int, match: Match,
        link: Optional[DiscordScheduledEvent], result: ReconcileResult,
        *, actor: User, now: datetime,
    ) -> None:
        name, description, start, end, location = self._render(match)
        content_hash = self._content_hash(name, description, start, end, location)

        if link is None:
            await self._create(tenant, guild_id, match, name, description, start, end,
                               location, content_hash, result, actor=actor, now=now)
            return

        if link.content_hash == content_hash:
            result.unchanged += 1
            return

        ok, message = await self.discord.edit_scheduled_event(
            guild_id, link.discord_event_id, name=name, start_time=start,
            end_time=end, description=description, location=location,
        )
        if not ok:
            # Self-heal: an event deleted out-of-band in Discord (or lost when a
            # mock/process restarted) can't be edited — re-create it and repoint
            # the link rather than erroring on it forever. Non-recoverable errors
            # (e.g. permission) surface when the re-create also fails.
            if 'not found' not in message.lower():
                raise _failure(message)
            await self.repo.delete(link)
            await self._create(tenant, guild_id, match, name, description, start, end,
                               location, content_hash, result, actor=actor, now=now)
            return
        await self.repo.update(
            link, title=name, scheduled_at=start, content_hash=content_hash, synced_at=now,
        )
        result.updated += 1
        await self.audit.write_and_publish(
            actor, AuditActions.DISCORD_EVENT_UPDATED,
            {'tenant_id': tenant.id, 'guild_id': guild_id, 'match_id': match.id,
             'discord_event_id': link.discord_event_id},
            EventType.DISCORD_EVENT_UPDATED,
            event_details={
                'tenant_id': tenant.id, 'match_id': match.id,
                'discord_event_id': link.discord_event_id,
            },
        )

    async def _create(
        self, tenant: Tenant, guild_id: int, match: Match, name: str, description: str,
        start: datetime, end: datetime, location: str, content_hash: str,
        result: ReconcileResult, *, actor: User, now: datetime,
    ) -> None:
        ok, payload = await self.discord.create_scheduled_event(
            guild_id, name=name, start_time=start, end_time=end,
            description=description, location=location,
        )
        if not ok:
            raise _failure(str(payload))
        created = await self.repo.create(
            guild_id=guild_id, discord_event_id=int(payload),
            source_type=DiscordEventSource.MATCH, source_id=match.id,
            title=name, scheduled_at=start, content_hash=content_hash, synced_at=now,
        )
        result.created += 1
        await self.audit.write_and_publish(
            actor, AuditActions.DISCORD_EVENT_CREATED,
            {'tenant_id': tenant.id, 'guild_id': guild_id, 'match_id': match.id,
             'discord_event_id': created.discord_event_id},
            EventType.DISCORD_EVENT_CREATED,
            event_details={
                'tenant_id': tenant.id, 'match_id': match.id,
                'discord_event_id': created.discord_event_id,
            },
        )

    async def _cancel(
        self, tenant: Tenant, guild_id: int, link: DiscordScheduledEvent,
        result: ReconcileResult, *, actor: User,
    ) -> None:
        ok, message = await self.discord.delete_scheduled_event(guild_id, link.discord_event_id)
        if not ok:
            raise _failure(message)
        await self.repo.delete(link)
        result.cancelled += 1
        await self.audit.write_and_publish(
            actor, AuditActions.DISCORD_EVENT_CANCELLED,
            {'tenant_id': tenant.id, 'guild_id': guild_id, 'source_id': link.source_id,
             'discord_event_id': link.discord_event_id},
            EventType.DISCORD_EVENT_CANCELLED,
            event_details={
                'tenant_id': tenant.id, 'source_id': link.source_id,
                'discord_event_id': link.discord_event_id,
            },
        )

    def _render(self, match: Match) -> tuple[str, str, datetime, datetime, str]:
        """Build (title, description, start, end, location) for a match's event."""
        tournament = match.tournament
        players = ', '.join(
            (p.user.display_name or p.user.username) for p in match.players
        ) or 'TBD'
        match_label = match.title or (f'{tournament.name}: {players}' if players != 'TBD' else tournament.name)

        title_template = tournament.discord_event_title_template or DEFAULT_TITLE_TEMPLATE
        desc_template = tournament.discord_event_description_template or DEFAULT_DESCRIPTION_TEMPLATE
        subs = {'tournament': tournament.name, 'match': match_label, 'players': players}
        title = self._apply(title_template, subs)[:100] or tournament.name[:100]
        description = self._apply(desc_template, subs)[:1000]

        start = match.scheduled_at
        duration = tournament.discord_event_duration_minutes or 60
        end = start + timedelta(minutes=max(1, duration))
        return title, description, start, end, 'Stream'

    @staticmethod
    def _apply(template: str, subs: Dict[str, str]) -> str:
        """Substitute ``{tournament}``/``{match}``/``{players}``; unknown keys stay literal."""
        out = template
        for key, value in subs.items():
            out = out.replace('{' + key + '}', value)
        return out.strip()

    @staticmethod
    def _content_hash(
        name: str, description: str, start: datetime, end: datetime, location: str,
    ) -> str:
        return stable_content_hash({
            'name': name, 'description': description,
            'start': start.isoformat(), 'end': end.isoformat(), 'location': location,
        })
