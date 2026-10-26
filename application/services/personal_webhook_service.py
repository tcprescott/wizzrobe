"""
Personal Webhook Service - Business Logic Layer

Webhooks a member sets up for themselves, from their profile. Three rules set
them apart from the staff webhooks in ``WebhookService``:

* **What goes out is decided per delivery.** The event is checked against what
  the owner can see *when it is sent*, not when they subscribed: a member who
  leaves the community, or a volunteer whose shift is taken away, stops hearing
  about it on the next event.
* **The payload is rebuilt, never forwarded.** Each catalogue event has a view
  that loads the row it is about and copies an allowlist of fields a member can
  already see in the app. Staff notes, comments, reasons and anything else not on
  the allowlist never leave, whatever the published event carried.
* **Only the catalogue.** A member subscribes to the events in :data:`EVENTS`,
  grouped into :data:`AREAS`; the rest of ``EventType`` is staff-only.

Destinations are a Discord webhook URL (a Discord post, no mentions) or any
other HTTPS URL (signed JSON, same signature scheme as staff webhooks). Both are
held to the same SSRF rules as staff webhooks, at save time and again at send.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from application.errors import require_found
from application.event_context import get_current_event_id
from application.events import Event, EventType, webhook_queue
from application.feature_flags import requires_feature
from application.repositories import (
    AsyncQualifierRunRepository,
    BracketRepository,
    MatchRepository,
    PersonalWebhookRepository,
    RacetimeRoomRepository,
    TenantMembershipRepository,
    VolunteerShiftRepository,
)
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService, reset_flag_cache
from application.services.webhook_service import WebhookService
from application.tenant_context import require_tenant_id, tenant_scope
from application.utils.app_links import (
    HOME_SCHEDULE,
    home_url,
    player_match_url,
    qualifier_runs_url,
    volunteer_my_shifts_url,
)
from application.utils.environment import is_production
from application.utils.ssrf import ensure_public_host, pin_public_url
from application.utils.tenant_urls import event_path, tenant_url
from models import FeatureFlag, PersonalWebhook, User

logger = logging.getLogger(__name__)

MAX_PER_USER = 3
NAME_MAX = 80
# Two tries, not the staff three: a member's endpoint is more often a
# half-finished experiment, and every retry is our outbound traffic.
MAX_ATTEMPTS = 2
PAUSE_AFTER_FAILURES = 20
DELIVERIES_PER_MINUTE = 60
SAVES_PER_HOUR = 20
TESTS_PER_WINDOW = 3
TEST_WINDOW_SECONDS = 600


@dataclass(frozen=True)
class WebhookArea:
    key: str
    label: str
    hint: str
    flag: Optional[FeatureFlag] = None


@dataclass(frozen=True)
class PersonalEvent:
    event_type: str
    area: str
    label: str
    # Sent only when it is about the owner, even with "Only things about me" off:
    # someone else's crew signup or qualifier run is not something a member sees.
    mine_only: bool = False


AREAS: Tuple[WebhookArea, ...] = (
    WebhookArea('matches', 'Matches', 'Booked, moved, started, finished'),
    WebhookArea('crew', 'Crew', 'Your signups and approvals'),
    WebhookArea('volunteer', 'Volunteer shifts', 'Assigned, released, called off', FeatureFlag.VOLUNTEERS),
    WebhookArea('brackets', 'Brackets', 'Started, results, finished', FeatureFlag.BRACKETS),
    WebhookArea('qualifiers', 'Qualifiers', 'Your runs reviewed', FeatureFlag.ASYNC_QUALIFIERS),
    WebhookArea('race_rooms', 'Race rooms', 'Opened, started, finished', FeatureFlag.RACETIME_ROOMS),
)
AREAS_BY_KEY = {a.key: a for a in AREAS}

# match.cancelled and match.deleted are left out on purpose: both remove the
# match before a delivery can look at it, so there is no way to tell whose it was.
EVENTS: Tuple[PersonalEvent, ...] = (
    PersonalEvent(EventType.MATCH_CREATED, 'matches', 'Booked'),
    PersonalEvent(EventType.MATCH_RESCHEDULED, 'matches', 'Moved'),
    PersonalEvent(EventType.MATCH_STAGE_ASSIGNED, 'matches', 'Put on a stage'),
    PersonalEvent(EventType.MATCH_STAGE_CLEARED, 'matches', 'Taken off a stage'),
    PersonalEvent(EventType.MATCH_SEATED, 'matches', 'Checked in'),
    PersonalEvent(EventType.MATCH_STARTED, 'matches', 'Started'),
    PersonalEvent(EventType.MATCH_FINISHED, 'matches', 'Finished'),
    PersonalEvent(EventType.MATCH_RESULT_RECORDED, 'matches', 'Result recorded'),
    PersonalEvent(EventType.MATCH_CONFIRMED, 'matches', 'Result confirmed'),
    PersonalEvent(EventType.MATCH_FORFEITED, 'matches', 'Forfeited'),
    PersonalEvent(EventType.MATCH_RESCHEDULE_REQUESTED, 'matches', 'New time asked for', mine_only=True),
    PersonalEvent(EventType.MATCH_RESCHEDULE_AGREED, 'matches', 'New time agreed', mine_only=True),
    PersonalEvent(EventType.MATCH_RESCHEDULE_APPROVED, 'matches', 'New time approved', mine_only=True),
    PersonalEvent(EventType.MATCH_RESCHEDULE_DECLINED, 'matches', 'New time turned down', mine_only=True),
    PersonalEvent(EventType.MATCH_RESCHEDULE_WITHDRAWN, 'matches', 'New time withdrawn', mine_only=True),
    PersonalEvent(EventType.CREW_SIGNUP_CREATED, 'crew', 'Signed up', mine_only=True),
    PersonalEvent(EventType.CREW_SIGNUP_REMOVED, 'crew', 'Signup removed', mine_only=True),
    PersonalEvent(EventType.CREW_APPROVAL_CHANGED, 'crew', 'Approved or turned down', mine_only=True),
    PersonalEvent(EventType.CREW_ACKNOWLEDGED, 'crew', 'Confirmed', mine_only=True),
    PersonalEvent(EventType.VOLUNTEER_ASSIGNED, 'volunteer', 'Given a shift', mine_only=True),
    PersonalEvent(EventType.VOLUNTEER_UNASSIGNED, 'volunteer', 'Taken off a shift', mine_only=True),
    PersonalEvent(EventType.VOLUNTEER_RELEASED, 'volunteer', 'Gave a shift back', mine_only=True),
    PersonalEvent(EventType.VOLUNTEER_CALLED_OFF, 'volunteer', 'Called off', mine_only=True),
    PersonalEvent(EventType.VOLUNTEER_CALL_OFF_UNDONE, 'volunteer', 'Call-off undone', mine_only=True),
    PersonalEvent(EventType.BRACKET_STARTED, 'brackets', 'Bracket started'),
    PersonalEvent(EventType.BRACKET_MATCH_COMPLETED, 'brackets', 'Bracket result'),
    PersonalEvent(EventType.BRACKET_COMPLETED, 'brackets', 'Bracket finished'),
    PersonalEvent(EventType.ASYNC_QUALIFIER_RUN_REVIEWED, 'qualifiers', 'Run reviewed', mine_only=True),
    PersonalEvent(EventType.ASYNC_QUALIFIER_RUN_EXPIRED, 'qualifiers', 'Run expired', mine_only=True),
    PersonalEvent(EventType.RACE_ROOM_OPENED, 'race_rooms', 'Race room opened'),
    PersonalEvent(EventType.RACE_ROOM_STARTED, 'race_rooms', 'Race started'),
    PersonalEvent(EventType.RACE_ROOM_FINISHED, 'race_rooms', 'Race finished'),
    PersonalEvent(EventType.RACE_ROOM_CANCELLED, 'race_rooms', 'Race room closed'),
)
EVENTS_BY_TYPE = {e.event_type: e for e in EVENTS}
SUBSCRIBABLE_EVENT_TYPES = frozenset(EVENTS_BY_TYPE)

_DISCORD_HOSTS = frozenset({'discord.com', 'discordapp.com', 'ptb.discord.com', 'canary.discord.com'})
_DISCORD_MARKDOWN = re.compile(r'([\\*_~`|>#\[\]()-])')

# Single worker, so an in-process window is enough (see notification_channel_service).
_hits: Dict[Tuple[str, int], Deque[float]] = defaultdict(deque)


def _take_slot(kind: str, key: int, limit: int, window: float) -> bool:
    now = time.monotonic()
    hits = _hits[(kind, key)]
    while hits and hits[0] < now - window:
        hits.popleft()
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


def reset_rate_limits() -> None:
    _hits.clear()


def is_discord_url(url: str) -> bool:
    parsed = urlparse((url or '').strip())
    return (
        parsed.scheme == 'https'
        and (parsed.hostname or '').lower() in _DISCORD_HOSTS
        and parsed.path.startswith('/api/webhooks/')
    )


def masked_url(url: str) -> str:
    """The URL with its secret part hidden, for showing on the profile.

    A Discord webhook URL is itself the credential (anyone holding it can post to
    the channel), so the token after the id never goes back to the page.
    """
    parsed = urlparse(url or '')
    host = parsed.hostname or ''
    if is_discord_url(url):
        parts = parsed.path.split('/')
        hook_id = parts[3] if len(parts) > 3 else ''
        return f'{host}/api/webhooks/{hook_id[:6]}…/••••'
    path = parsed.path if len(parsed.path) <= 32 else parsed.path[:31] + '…'
    return f'{host}{path}'


def events_for_areas(area_keys: List[str]) -> List[str]:
    wanted = set(area_keys)
    return [e.event_type for e in EVENTS if e.area in wanted]


def _md(text: Any) -> str:
    return _DISCORD_MARKDOWN.sub(r'\\\1', str(text or ''))


def _iso(when: Optional[datetime]) -> Optional[str]:
    return when.isoformat() if when else None


def _discord_time(when: Optional[datetime]) -> str:
    return f'<t:{int(when.timestamp())}:F>' if when else 'not set'


@dataclass
class _View:
    """What one owner may be told about one event."""

    about_me: bool
    data: Dict[str, Any]
    title: str
    lines: List[str] = field(default_factory=list)
    path: Optional[str] = None
    url: Optional[str] = None


class PersonalWebhookService:
    """Member-owned webhooks: CRUD, the per-delivery visibility check, delivery."""

    DELIVERY_TIMEOUT = 10.0

    def __init__(self) -> None:
        self.repository = PersonalWebhookRepository()
        self.audit_service = AuditService()

    # ------------------------------------------------------------ catalogue

    @staticmethod
    @requires_feature(FeatureFlag.PERSONAL_WEBHOOKS)
    async def available_areas() -> List[WebhookArea]:
        """Areas this community has the features for."""
        live = await FeatureFlagService().enabled_flags()
        return [a for a in AREAS if a.flag is None or a.flag in live]

    # ------------------------------------------------------------ CRUD

    @requires_feature(FeatureFlag.PERSONAL_WEBHOOKS)
    async def list_mine(self, user: User) -> List[PersonalWebhook]:
        return await self.repository.list_for_user(user.id)

    @requires_feature(FeatureFlag.PERSONAL_WEBHOOKS)
    async def create(
        self, user: User, *, name: str, url: str, event_types: List[str], only_mine: bool = True,
    ) -> Tuple[PersonalWebhook, Optional[str]]:
        """Create one. Returns the row and, for a JSON destination, its signing
        secret, which is shown once and never again."""
        await self._ensure_member(user)
        if await self.repository.count_for_user(user.id) >= MAX_PER_USER:
            raise ValueError(f'You can have {MAX_PER_USER} webhooks here. Delete one to add another.')
        self._take_save_slot(user)
        clean_name, clean_url, clean_events = await self._validate(name, url, event_types)
        secret = secrets.token_hex(32)
        webhook = await self.repository.create(
            user_id=user.id, name=clean_name, url=clean_url, secret=secret,
            event_types=clean_events, only_mine=bool(only_mine), is_active=True,
        )
        await self.audit_service.write_log(user, AuditActions.PERSONAL_WEBHOOK_CREATED, {
            'personal_webhook_id': webhook.id, 'event_types': clean_events,
            'only_mine': bool(only_mine), 'discord': is_discord_url(clean_url),
        })
        return webhook, None if is_discord_url(clean_url) else secret

    @requires_feature(FeatureFlag.PERSONAL_WEBHOOKS)
    async def update(
        self, user: User, webhook_id: int, *, name: str, url: Optional[str],
        event_types: List[str], only_mine: bool,
    ) -> PersonalWebhook:
        """Edit one. ``url=None`` keeps the stored URL (the page never holds it)."""
        await self._ensure_member(user)
        webhook = await self._require_own(user, webhook_id)
        self._take_save_slot(user)
        clean_name, clean_url, clean_events = await self._validate(
            name, url if url is not None else webhook.url, event_types,
        )
        fields: Dict[str, Any] = {
            'name': clean_name, 'url': clean_url, 'event_types': clean_events,
            'only_mine': bool(only_mine),
        }
        await self.repository.update(webhook, **fields)
        await self.audit_service.write_log(user, AuditActions.PERSONAL_WEBHOOK_UPDATED, {
            'personal_webhook_id': webhook.id, 'event_types': clean_events,
            'only_mine': bool(only_mine), 'url_changed': url is not None,
        })
        return webhook

    @requires_feature(FeatureFlag.PERSONAL_WEBHOOKS)
    async def set_active(self, user: User, webhook_id: int, active: bool) -> PersonalWebhook:
        webhook = await self._require_own(user, webhook_id)
        fields: Dict[str, Any] = {'is_active': bool(active)}
        if active:
            fields.update(paused_reason=None, consecutive_failures=0)
        await self.repository.update(webhook, **fields)
        await self.audit_service.write_log(user, AuditActions.PERSONAL_WEBHOOK_UPDATED, {
            'personal_webhook_id': webhook.id, 'is_active': bool(active),
        })
        return webhook

    @requires_feature(FeatureFlag.PERSONAL_WEBHOOKS)
    async def delete(self, user: User, webhook_id: int) -> None:
        webhook = await self._require_own(user, webhook_id)
        await self.repository.delete(webhook)
        await self.audit_service.write_log(user, AuditActions.PERSONAL_WEBHOOK_DELETED, {
            'personal_webhook_id': webhook_id,
        })

    @requires_feature(FeatureFlag.PERSONAL_WEBHOOKS)
    async def regenerate_secret(self, user: User, webhook_id: int) -> str:
        webhook = await self._require_own(user, webhook_id)
        if is_discord_url(webhook.url):
            raise ValueError("Discord webhooks aren't signed, so there's no secret to change.")
        secret = secrets.token_hex(32)
        await self.repository.update(webhook, secret=secret)
        await self.audit_service.write_log(user, AuditActions.PERSONAL_WEBHOOK_SECRET_REGENERATED, {
            'personal_webhook_id': webhook.id,
        })
        return secret

    @requires_feature(FeatureFlag.PERSONAL_WEBHOOKS)
    async def send_test(self, user: User, webhook_id: int) -> None:
        """Send a sample message now and wait for the answer. Raises with the
        reason when the destination refuses it."""
        await self._ensure_member(user)
        webhook = await self._require_own(user, webhook_id)
        if not _take_slot('test', user.id, TESTS_PER_WINDOW, TEST_WINDOW_SECONDS):
            raise ValueError('You can send 3 tests every 10 minutes. Try again in a few minutes.')
        view = _View(
            about_me=True,
            data={'message': 'This is a test from Wizzrobe.'},
            title='Test message',
            lines=["If you're reading this, your webhook works."],
        )
        ok, status, error = await self._post(webhook, 'webhook.test', view, datetime.now(timezone.utc))
        if not ok:
            raise ValueError(f"The test didn't go through: {error or f'HTTP {status}'}")

    # ------------------------------------------------------------ delivery

    # feature-gate: exempt — a worker: it skips a tenant with the flag off instead of raising.
    async def deliver_event(self, event: Event) -> None:
        """Event-bus subscriber: send ``event`` to each owner allowed to see it.

        Runs on the dispatch worker under the event's tenant. Each webhook's view
        is built (and its POST queued) separately, because two owners of the same
        event can be told different things.
        """
        spec = EVENTS_BY_TYPE.get(event.event_type)
        if spec is None or event.tenant_id is None:
            return
        # This worker's context outlives any request, so its flag cache would never refresh.
        reset_flag_cache()
        live = await FeatureFlagService().enabled_flags()
        if FeatureFlag.PERSONAL_WEBHOOKS not in live:
            return
        area = AREAS_BY_KEY[spec.area]
        if area.flag is not None and area.flag not in live:
            return
        for webhook in await self.repository.list_active():
            if event.event_type not in (webhook.event_types or []):
                continue
            try:
                view = await self.view_for(webhook, event)
            except Exception:
                logger.exception('personal webhook %s: building %s failed', webhook.id, event.event_type)
                continue
            if view is None:
                continue
            webhook_queue.enqueue(
                self._deliver_scoped(webhook, event.event_type, view, event.occurred_at, event.tenant_id),
            )

    # feature-gate: exempt — called only by deliver_event, after its flag check.
    async def view_for(self, webhook: PersonalWebhook, event: Event) -> Optional[_View]:
        """What ``webhook``'s owner may be told about ``event``, or ``None``.

        ``None`` when the owner is no longer an active member, the row the event
        is about is gone or outside this community, or the event is not about the
        owner and either the webhook or the event says it must be.
        """
        spec = EVENTS_BY_TYPE.get(event.event_type)
        if spec is None:
            return None
        owner: User = webhook.user
        if not owner.is_active or not await self._is_member(owner):
            return None
        builder = _BUILDERS[spec.area]
        view = await builder(self, event, owner)
        if view is None:
            return None
        if not view.about_me and (webhook.only_mine or spec.mine_only):
            return None
        view.title = f'{spec.label} · {view.title}' if view.title else spec.label
        return view

    async def _deliver_scoped(
        self, webhook: PersonalWebhook, event_type: str, view: _View, occurred_at: datetime,
        tenant_id: int,
    ) -> None:
        # Queued items run after the subscriber's tenant scope has exited.
        with tenant_scope(tenant_id):
            await self._deliver(webhook, event_type, view, occurred_at)

    async def _deliver(self, webhook: PersonalWebhook, event_type: str, view: _View, occurred_at: datetime) -> None:
        if not _take_slot('deliver', webhook.id, DELIVERIES_PER_MINUTE, 60):
            await self.repository.record_failure(
                webhook.id, None, 'Skipped: more than 60 messages in a minute', datetime.now(timezone.utc),
            )
            return
        ok, status, error = await self._post(webhook, event_type, view, occurred_at)
        now = datetime.now(timezone.utc)
        if ok:
            await self.repository.record_success(webhook.id, status, now)
            return
        streak = await self.repository.record_failure(webhook.id, status, error or f'HTTP {status}', now)
        if is_discord_url(webhook.url) and status in (401, 404):
            await self.repository.pause(webhook.id, 'Paused: Discord says this webhook no longer exists.')
        elif streak >= PAUSE_AFTER_FAILURES:
            await self.repository.pause(
                webhook.id, f'Paused after {PAUSE_AFTER_FAILURES} failed deliveries in a row.',
            )

    async def _post(
        self, webhook: PersonalWebhook, event_type: str, view: _View, occurred_at: datetime,
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        discord = is_discord_url(webhook.url)
        if discord:
            body = json.dumps(await self._discord_body(view, occurred_at))
            headers = {'Content-Type': 'application/json', 'User-Agent': 'wizzrobe-webhook'}
        else:
            body = json.dumps(await self._json_body(event_type, view, occurred_at), default=str, sort_keys=True)
            timestamp = str(int(datetime.now(timezone.utc).timestamp()))
            signature = hmac.new(
                webhook.secret.encode(), f'{timestamp}.{body}'.encode(), hashlib.sha256,
            ).hexdigest()
            headers = WebhookService.build_delivery_headers(
                event_type=event_type, delivery_id=str(uuid.uuid4()),
                timestamp=timestamp, signature=signature,
            )
        status: Optional[int] = None
        error: Optional[str] = None
        async with httpx.AsyncClient(timeout=self.DELIVERY_TIMEOUT, follow_redirects=False) as client:
            for attempt in range(MAX_ATTEMPTS):
                target = webhook.url
                extra_headers: Dict[str, str] = {}
                extensions: Dict[str, str] = {}
                if is_production():
                    try:
                        target, extra_headers, extensions = await pin_public_url(
                            webhook.url, subject='Webhook URL',
                        )
                    except ValueError as exc:
                        return False, None, f'Blocked: {exc}'
                try:
                    response = await client.post(
                        target, content=body, headers={**headers, **extra_headers}, extensions=extensions,
                    )
                    status = response.status_code
                    if 200 <= status < 300:
                        return True, status, None
                    error = f'HTTP {status}'
                    if 400 <= status < 500 and status != 429:
                        break
                except httpx.HTTPError as exc:
                    error = str(exc) or type(exc).__name__
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(2)
        logger.info('personal webhook %s gave up on %s: %s', webhook.id, event_type, error)
        return False, status, error

    async def _json_body(self, event_type: str, view: _View, occurred_at: datetime) -> Dict[str, Any]:
        return {
            'event_type': event_type,
            'occurred_at': occurred_at.isoformat(),
            'community': await self._community_slug(),
            'about_me': view.about_me,
            'link': await self._link(view),
            'data': view.data,
        }

    async def _discord_body(self, view: _View, occurred_at: datetime) -> Dict[str, Any]:
        embed: Dict[str, Any] = {
            'title': view.title[:256],
            'description': '\n'.join(view.lines)[:4000],
            'timestamp': occurred_at.isoformat(),
            'color': 0x8F6400,
        }
        link = await self._link(view)
        if link:
            embed['url'] = link
        community = await self._community_name()
        if community:
            embed['footer'] = {'text': community[:200]}
        # No pings from text a player wrote: names and titles go out as-is.
        return {'username': 'Wizzrobe', 'embeds': [embed], 'allowed_mentions': {'parse': []}}

    # ------------------------------------------------------------ views

    async def _match_view(self, match_id: Optional[int], owner: User) -> Optional[_View]:
        if not match_id:
            return None
        match = await MatchRepository.get_by_id(int(match_id))
        if match is None:
            return None
        players = list(match.players)  # type: ignore[attr-defined]
        crew = [*match.commentators, *match.trackers]  # type: ignore[attr-defined]
        crew_ids = {c.user_id for c in crew if c.approved}
        about_me = owner.id in {p.user_id for p in players} or owner.id in crew_ids
        tournament = match.tournament
        stage = match.stage
        names = [p.user.preferred_name for p in players]
        data = {
            'match': {
                'id': match.id,
                'title': match.title,
                'tournament': {'id': tournament.id, 'name': tournament.name},
                'scheduled_at': _iso(match.scheduled_at),
                'checked_in_at': _iso(match.seated_at),
                'started_at': _iso(match.started_at),
                'finished_at': _iso(match.finished_at),
                'confirmed_at': _iso(match.confirmed_at),
                'stage': stage.name if stage else None,
                'players': [
                    {'name': p.user.preferred_name, 'finish_rank': p.finish_rank, 'finish_time': p.finish_time}
                    for p in players
                ],
            },
        }
        lines = [f"**{_md(' vs '.join(names) or 'Players to be decided')}**"]
        lines.append(f'When: {_discord_time(match.scheduled_at)}')
        if stage:
            lines.append(f'Stage: {_md(stage.name)}')
        ranked = sorted((p for p in players if p.finish_rank), key=lambda p: p.finish_rank)
        if ranked:
            lines.append('Result: ' + ', '.join(f'{p.finish_rank}. {_md(p.user.preferred_name)}' for p in ranked))
        path = player_match_url(match.id) if about_me else home_url(HOME_SCHEDULE)
        return _View(about_me, data, _md(match.title or tournament.name), lines, path)

    async def _matches(self, event: Event, owner: User) -> Optional[_View]:
        return await self._match_view(event.payload.get('match_id'), owner)

    async def _crew(self, event: Event, owner: User) -> Optional[_View]:
        if event.payload.get('user_id') != owner.id:
            return None
        view = await self._match_view(event.payload.get('match_id'), owner)
        if view is None:
            return None
        view.about_me = True
        role = event.payload.get('crew_type') or event.payload.get('role')
        crew: Dict[str, Any] = {'role': role}
        if 'approved' in event.payload:
            crew['approved'] = bool(event.payload['approved'])
            view.lines.insert(1, 'You were approved.' if crew['approved'] else "You weren't approved this time.")
        view.data['crew'] = crew
        return view

    async def _volunteer(self, event: Event, owner: User) -> Optional[_View]:
        if event.payload.get('user_id') != owner.id:
            return None
        shift_id = event.payload.get('shift_id')
        shift = await VolunteerShiftRepository.get_by_id(int(shift_id)) if shift_id else None
        if shift is None:
            return None
        position = shift.position
        data = {'shift': {
            'id': shift.id, 'position': position.name, 'label': shift.label,
            'starts_at': _iso(shift.starts_at), 'ends_at': _iso(shift.ends_at),
        }}
        lines = [
            f'**{_md(position.name)}**' + (f' · {_md(shift.label)}' if shift.label else ''),
            f'From {_discord_time(shift.starts_at)} to {_discord_time(shift.ends_at)}',
        ]
        return _View(True, data, _md(position.name), lines, volunteer_my_shifts_url())

    async def _qualifiers(self, event: Event, owner: User) -> Optional[_View]:
        if event.payload.get('user_id') != owner.id:
            return None
        run_id = event.payload.get('run_id')
        run = await AsyncQualifierRunRepository().get_by_id(int(run_id)) if run_id else None
        if run is None or run.user_id != owner.id:  # type: ignore[attr-defined]
            return None
        qualifier = run.qualifier
        data = {'run': {
            'id': run.id, 'qualifier': {'id': qualifier.id, 'name': qualifier.name},
            'status': run.status.value, 'review_status': run.review_status.value,
            'finished_at': _iso(run.finished_at),
        }}
        lines = [f'**{_md(qualifier.name)}**', f'Review: {run.review_status.value.replace("_", " ")}']
        return _View(True, data, _md(qualifier.name), lines, qualifier_runs_url(qualifier.id))

    async def _brackets(self, event: Event, owner: User) -> Optional[_View]:
        repo = BracketRepository()
        bracket_id = event.payload.get('bracket_id')
        if event.event_type == EventType.BRACKET_MATCH_COMPLETED:
            match_id = event.payload.get('match_id')
            bracket_match = await repo.get_match_with_entrants(int(match_id)) if match_id else None
            if bracket_match is None:
                return None
            bracket = bracket_match.bracket
            sides = [bracket_match.entry1, bracket_match.entry2]
            entrants = [s.entrant for s in sides if s is not None]
            about_me = any(e.user_id == owner.id for e in entrants)
            winner = next(
                (s.entrant.display_name for s in sides if s is not None and s.id == bracket_match.winner_id),  # type: ignore[attr-defined]
                None,
            )
            scores = [bracket_match.entry1_score, bracket_match.entry2_score]
            data = {
                'bracket': {'id': bracket.id, 'name': bracket.name,
                            'tournament': {'id': bracket.tournament.id, 'name': bracket.tournament.name}},
                'match': {'id': bracket_match.id, 'round': bracket_match.round,
                          'entrants': [e.display_name for e in entrants], 'scores': scores,
                          'winner': winner},
            }
            lines = [f"**{_md(' vs '.join(e.display_name for e in entrants))}**"]
            if winner:
                lines.append(f'Winner: {_md(winner)}')
            if None not in scores:
                lines.append(f'Score: {scores[0]}–{scores[1]}')
            return _View(about_me, data, _md(bracket.tournament.name), lines,
                         f'/brackets/{bracket.id}')
        bracket = await repo.get_bracket(int(bracket_id)) if bracket_id else None
        if bracket is None:
            return None
        await bracket.fetch_related('tournament')
        about_me = await repo.user_has_entry(bracket.id, owner.id)
        data = {'bracket': {'id': bracket.id, 'name': bracket.name, 'state': bracket.state.value,
                            'tournament': {'id': bracket.tournament.id, 'name': bracket.tournament.name}}}
        lines = [f'**{_md(bracket.tournament.name)}** · {_md(bracket.name)}']
        return _View(about_me, data, _md(bracket.tournament.name), lines, f'/brackets/{bracket.id}')

    async def _race_rooms(self, event: Event, owner: User) -> Optional[_View]:
        room_id = event.payload.get('room_id')
        room = await RacetimeRoomRepository.get_by_id(int(room_id)) if room_id else None
        if room is None:
            return None
        view = await self._match_view(room.match_id, owner)  # type: ignore[attr-defined]
        if view is None:
            return None
        view.data['race_room'] = {'url': room.url, 'status': room.status.value}
        view.lines.append(f'Room: {room.url}')
        view.url = room.url
        return view

    # ------------------------------------------------------------ internals

    async def _link(self, view: _View) -> Optional[str]:
        if view.url:
            return view.url
        if not view.path:
            return None
        from application.services.notification_links import event_slug
        from application.services.tenant_service import TenantService

        tenant_id = require_tenant_id()
        tenant = await TenantService.get_by_id(tenant_id)
        slug = await event_slug(tenant_id, get_current_event_id())
        return tenant_url(tenant, event_path(slug, view.path)) if tenant else None

    async def _community_slug(self) -> Optional[str]:
        from application.services.tenant_service import TenantService

        tenant = await TenantService.get_by_id(require_tenant_id())
        return tenant.slug if tenant else None

    async def _community_name(self) -> str:
        from application.services.tenant_service import TenantService

        return await TenantService.community_name(require_tenant_id())

    async def _is_member(self, user: User) -> bool:
        if await TenantMembershipRepository.is_member(user.id, require_tenant_id()):
            return True
        return bool(await AuthService.get_roles(user))

    async def _ensure_member(self, user: User) -> None:
        await AuthService.ensure(
            user.is_active and await self._is_member(user),
            'Join this community to set up webhooks here.',
        )

    async def _require_own(self, user: User, webhook_id: int) -> PersonalWebhook:
        return require_found(await self.repository.get_for_user(webhook_id, user.id), 'Webhook')

    def _take_save_slot(self, user: User) -> None:
        if not _take_slot('save', user.id, SAVES_PER_HOUR, 3600):
            raise ValueError('Too many webhook changes in the last hour. Try again later.')

    async def _validate(self, name: str, url: str, event_types: List[str]) -> Tuple[str, str, List[str]]:
        clean_name = (name or '').strip()
        if not clean_name:
            raise ValueError('Give the webhook a name.')
        if len(clean_name) > NAME_MAX:
            raise ValueError(f'Keep the name under {NAME_MAX} characters.')
        clean_url = (url or '').strip()
        parsed = urlparse(clean_url)
        allow_http = not is_production()
        if parsed.scheme != 'https' and not (allow_http and parsed.scheme == 'http'):
            raise ValueError('The URL has to start with https://')
        if not parsed.hostname:
            raise ValueError("That URL doesn't have a host.")
        if parsed.username or parsed.password:
            raise ValueError("The URL can't include a username or password.")
        if len(clean_url) > 1024:
            raise ValueError('That URL is too long.')
        if (parsed.hostname or '').lower() in _DISCORD_HOSTS and not is_discord_url(clean_url):
            raise ValueError("That isn't a Discord webhook URL. Copy it from Channel settings → Integrations → Webhooks.")
        if is_production():
            await ensure_public_host(parsed.hostname, subject='Webhook URL')
        available = {a.key for a in await self.available_areas()}
        clean_events = sorted({
            e for e in (event_types or [])
            if e in EVENTS_BY_TYPE and EVENTS_BY_TYPE[e].area in available
        })
        if not clean_events:
            raise ValueError('Pick at least one thing to send.')
        return clean_name, clean_url, clean_events


_BUILDERS = {
    'matches': PersonalWebhookService._matches,
    'crew': PersonalWebhookService._crew,
    'volunteer': PersonalWebhookService._volunteer,
    'brackets': PersonalWebhookService._brackets,
    'qualifiers': PersonalWebhookService._qualifiers,
    'race_rooms': PersonalWebhookService._race_rooms,
}
