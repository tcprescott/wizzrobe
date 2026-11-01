"""
Webhook Service - Business Logic Layer

Staff-managed outbound webhooks: CRUD (all staff-gated and audited) plus the
delivery path that subscribes to the event bus and POSTs a signed JSON payload
to each subscribed endpoint.

Delivery is signed with HMAC-SHA256 over ``"{timestamp}.{body}"`` using the
per-webhook secret (GitHub-style ``sha256=`` header); the signed timestamp lets a
receiver reject replays. The secret is never returned by list/GET nor written to
logs.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Collection, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from application.errors import NotFoundError, require_found
from application.events import Event, EventType, webhook_queue
from application.repositories import WebhookDeliveryRepository, WebhookRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.services.event_service import EventService
from application.tenant_context import require_tenant_id, tenant_scope
from application.utils.environment import is_production
from application.utils.ssrf import ensure_public_host, pin_public_url
from models import User, Webhook, WebhookDelivery

logger = logging.getLogger(__name__)


async def _deliver_scoped(coro, tenant_id) -> None:
    """Await a deferred delivery coroutine with its webhook's tenant bound.

    ``deliver_event`` enqueues each outbox row's send as its own queue item,
    which runs after the subscriber's tenant scope has exited; this rebinds the
    tenant so the claim and the finished row land under the right tenant.
    """
    with tenant_scope(tenant_id):
        await coro


class WebhookService:
    """CRUD + delivery for staff-managed outbound webhooks."""

    MAX_ATTEMPTS = 3
    DELIVERY_TIMEOUT = 10.0
    RETRY_BACKOFF_BASE = 2
    # Long enough for every attempt, its timeout and the backoff between them, so a live send is never taken over.
    CLAIM_SECONDS = 120

    @staticmethod
    def build_delivery_headers(
        *, event_type: str, delivery_id: str, timestamp: str, signature: str
    ) -> dict[str, str]:
        """The exact header set sent with every delivery.

        Single source of truth shared by ``_deliver_one`` and the in-app format
        reference (:meth:`format_reference`) so the two cannot drift.
        """
        return {
            'Content-Type': 'application/json',
            'User-Agent': 'wizzrobe-webhook',
            'X-Wizzrobe-Event': event_type,
            'X-Wizzrobe-Delivery': delivery_id,
            'X-Wizzrobe-Timestamp': timestamp,
            'X-Wizzrobe-Signature': f'sha256={signature}',
        }

    @staticmethod
    def format_reference() -> dict[str, Any]:
        """Code-derived reference for the webhook delivery format.

        Every drift-prone value — payload keys, header names, retry constants and
        the event list — is sourced from the live code objects so the in-app docs
        stay correct by construction. Only the per-header prose is manual.
        """
        example_payload = Event.create(
            EventType.MATCH_CREATED,
            {'match_id': 5, 'tournament_id': 2, 'player_ids': [1, 2]},
        ).to_wire({'id': 3, 'slug': 'sgl26', 'name': 'SG Live 2026'})
        descriptions = {
            'Content-Type': 'Always application/json.',
            'User-Agent': 'Identifies the sender.',
            'X-Wizzrobe-Event': 'The event name (e.g. match.created).',
            'X-Wizzrobe-Delivery': 'Unique id for this delivery attempt.',
            'X-Wizzrobe-Timestamp': 'Unix seconds; part of the signed string (replay defense).',
            'X-Wizzrobe-Signature': 'sha256=<hex>: HMAC-SHA256 of "{timestamp}.{body}" using the secret.',
        }
        sample = WebhookService.build_delivery_headers(
            event_type='match.created', delivery_id='<uuid>',
            timestamp='<unix-seconds>', signature='<hex>',
        )
        headers = [{'name': name, 'description': descriptions.get(name, '')} for name in sample]
        events_by_group: dict[str, list[str]] = {}
        for name in sorted(EventType.ALL):
            events_by_group.setdefault(name.split('.', 1)[0], []).append(name)
        return {
            'example_payload': example_payload,
            'headers': headers,
            'constants': {
                'timeout_seconds': WebhookService.DELIVERY_TIMEOUT,
                'max_attempts': WebhookService.MAX_ATTEMPTS,
                'backoff_base': WebhookService.RETRY_BACKOFF_BASE,
            },
            'events': events_by_group,
            'wildcard': EventType.WILDCARD,
        }

    def __init__(self) -> None:
        self.repository = WebhookRepository()
        self.delivery_repository = WebhookDeliveryRepository()
        self.audit_service = AuditService()

    async def list_webhooks(self, actor: User) -> List[Webhook]:
        await AuthService.ensure(await AuthService.is_staff(actor), "Only Staff can view webhooks")
        return await self.repository.list_all()

    async def get_webhook(self, actor: User, webhook_id: int) -> Webhook:
        await AuthService.ensure(await AuthService.is_staff(actor), "Only Staff can view webhooks")
        return await self._require(webhook_id)

    async def create_webhook(
        self,
        actor: User,
        *,
        name: str,
        url: str,
        event_types: List[str],
        is_active: bool = True,
        event_ids: Optional[List[int]] = None,
    ) -> Webhook:
        """``event_ids`` limits it to those community events; None sends every event's."""
        await AuthService.ensure(await AuthService.is_staff(actor), "Only Staff can create webhooks")
        name = (name or '').strip()
        if not name:
            raise ValueError("Webhook name is required")
        await self._validate_url(url)
        self._validate_event_types(event_types)
        event_ids = await self._clean_event_ids(event_ids)
        webhook = await self.repository.create(
            name=name,
            url=url.strip(),
            secret=secrets.token_urlsafe(32),
            event_types=list(event_types),
            event_ids=event_ids,
            is_active=is_active,
        )
        await self.audit_service.write_log(
            actor,
            AuditActions.WEBHOOK_CREATED,
            {
                'webhook_id': webhook.id, 'name': name, 'url': webhook.url,
                'event_types': list(event_types), 'event_ids': event_ids,
            },
        )
        return webhook

    async def update_webhook(
        self,
        actor: User,
        webhook_id: int,
        *,
        name: Optional[str] = None,
        url: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        is_active: Optional[bool] = None,
        event_ids: Any = ...,
    ) -> Webhook:
        """``event_ids``: a list of community events, None for every event, or left out to keep it."""
        await AuthService.ensure(await AuthService.is_staff(actor), "Only Staff can modify webhooks")
        webhook = await self._require(webhook_id)
        changes: dict[str, Any] = {}
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Webhook name is required")
            changes['name'] = name
        if url is not None:
            await self._validate_url(url)
            changes['url'] = url.strip()
        if event_types is not None:
            self._validate_event_types(event_types)
            changes['event_types'] = list(event_types)
        if is_active is not None:
            changes['is_active'] = is_active
        if event_ids is not ...:
            cleaned = await self._clean_event_ids(event_ids, kept=webhook.event_ids or ())
            if cleaned != webhook.event_ids:
                changes['event_ids'] = cleaned
        if changes:
            webhook = await self.repository.update(webhook, **changes)
            await self.audit_service.write_log(
                actor,
                AuditActions.WEBHOOK_UPDATED,
                {'webhook_id': webhook.id, 'changes': {k: v for k, v in changes.items()}},
            )
        return webhook

    async def delete_webhook(self, actor: User, webhook_id: int) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), "Only Staff can delete webhooks")
        webhook = await self._require(webhook_id)
        await self.audit_service.write_log(
            actor,
            AuditActions.WEBHOOK_DELETED,
            {'webhook_id': webhook.id, 'name': webhook.name},
        )
        await self.repository.delete(webhook)

    async def regenerate_secret(self, actor: User, webhook_id: int) -> str:
        await AuthService.ensure(await AuthService.is_staff(actor), "Only Staff can rotate webhook secrets")
        webhook = await self._require(webhook_id)
        new_secret = secrets.token_urlsafe(32)
        await self.repository.update(webhook, secret=new_secret)
        await self.audit_service.write_log(
            actor,
            AuditActions.WEBHOOK_SECRET_REGENERATED,
            {'webhook_id': webhook.id},
        )
        return new_secret

    async def list_deliveries(
        self, actor: User, webhook_id: int, limit: int = 50, offset: int = 0
    ) -> List[WebhookDelivery]:
        await AuthService.ensure(await AuthService.is_staff(actor), "Only Staff can view webhook deliveries")
        webhook = await self._require(webhook_id)
        return await self.delivery_repository.list_for_webhook(webhook, limit=limit, offset=offset)

    HEALTH_WINDOW = timedelta(hours=24)

    async def recent_health(self, actor: User) -> dict:
        """``{webhook id: {'ok': n, 'failed': n, 'last': datetime|None}}``.

        The webhooks list showed an Active toggle and nothing else, so a webhook
        whose last twenty deliveries all 5xx'd looked exactly like a healthy one
        — the failures were real, recorded, and two clicks away behind the
        per-row history dialog. This is the summary that belongs on the row.

        One aggregate query for every webhook, so the list does not gain a query
        per row (see ``tests/test_query_budget.py`` for why that matters).
        """
        await AuthService.ensure(await AuthService.is_staff(actor), "Only Staff can view webhooks")
        cutoff = datetime.now(timezone.utc) - self.HEALTH_WINDOW
        summary: dict = {}
        for row in await self.delivery_repository.outcome_counts_since(cutoff):
            entry = summary.setdefault(
                row['webhook_id'], {'ok': 0, 'failed': 0, 'last': None},
            )
            entry['ok' if row['success'] else 'failed'] += row['n']
            last = row['last']
            if last is not None and (entry['last'] is None or last > entry['last']):
                entry['last'] = last
        return summary

    async def deliver_event(self, event: Event) -> None:
        """Event-bus subscriber: write an outbox row for every webhook that wants this event, then queue its send.

        Runs under ``tenant_scope(event.tenant_id)`` (the bus wraps async
        subscribers), so ``list_active()`` returns only this tenant's webhooks. A
        platform-level event (``tenant_id`` None) reaches no webhook — they are
        all tenant-scoped — so short-circuit rather than trip the required-tenant
        guard in ``list_active``. The row is the durable part: if this process
        dies before the queued send runs, ``webhook_outbox_worker`` sends it.
        """
        if event.tenant_id is None:
            return
        webhooks = await self.repository.list_active()
        if not webhooks:
            return
        event_ref = await self.event_ref(event)
        for webhook in webhooks:
            subscribed = webhook.event_types or []
            if not (EventType.WILDCARD in subscribed or event.event_type in subscribed):
                continue
            if webhook.event_ids is not None and event.event_id not in (None, *webhook.event_ids):
                continue
            delivery = await self._queue_row(
                webhook, event.event_type, self._body(event, event_ref), str(uuid.uuid4()),
            )
            webhook_queue.enqueue(_deliver_scoped(self.attempt(webhook, delivery), webhook.tenant_id))

    @staticmethod
    async def event_ref(event: Event) -> Optional[Dict[str, Any]]:
        """``{id, slug, name}`` of the community event a message is from.

        A deleted draft is gone by delivery time, so ``event.deleted`` falls back
        to the slug and name its own payload carries.
        """
        if event.event_id is None or event.tenant_id is None:
            return None
        ref = await EventService.ref(event.tenant_id, event.event_id)
        if ref is not None:
            return {'id': ref.id, 'slug': ref.slug, 'name': ref.name}
        return {
            'id': event.event_id,
            'slug': event.payload.get('slug'),
            'name': event.payload.get('name'),
        }

    @staticmethod
    def _body(event: Event, event_ref: Optional[Dict[str, Any]]) -> str:
        return json.dumps(event.to_wire(event_ref), default=str, sort_keys=True)

    async def _queue_row(
        self, webhook: Webhook, event_type: str, body: str, delivery_uuid: str,
        claimed_until: Optional[datetime] = None,
    ) -> WebhookDelivery:
        return await self.delivery_repository.create(
            webhook=webhook, event_type=event_type, payload=body,
            pending=True, claimed_until=claimed_until, delivery_uuid=delivery_uuid,
        )

    async def _deliver_one(
        self, webhook: Webhook, event: Event, event_ref: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write the outbox row already claimed by this sender, then send it now."""
        body, delivery_uuid = self._body(event, event_ref), str(uuid.uuid4())
        claimed_until = datetime.now(timezone.utc) + timedelta(seconds=self.CLAIM_SECONDS)
        delivery = await self._queue_row(webhook, event.event_type, body, delivery_uuid, claimed_until)
        await self._send(webhook, delivery, body=body, event_type=event.event_type, delivery_uuid=delivery_uuid)

    async def attempt(self, webhook: Webhook, delivery: WebhookDelivery) -> bool:
        """Send a pending outbox row unless another sender holds it; True when this call sent it."""
        now = datetime.now(timezone.utc)
        if not await self.delivery_repository.claim(
            delivery, now, now + timedelta(seconds=self.CLAIM_SECONDS),
        ):
            return False
        await self._send(
            webhook, delivery, body=delivery.payload, event_type=delivery.event_type,
            delivery_uuid=delivery.delivery_uuid or str(uuid.uuid4()),
            prior_attempts=delivery.attempt_count,
        )
        return True

    async def give_up(self, delivery: WebhookDelivery, reason: str) -> None:
        """Close a pending outbox row that will never be sent."""
        await self.delivery_repository.finish(delivery, success=False, error=reason)

    async def _send(
        self, webhook: Webhook, delivery: Optional[WebhookDelivery], *, body: str, event_type: str,
        delivery_uuid: str, prior_attempts: int = 0,
    ) -> None:
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        signature = hmac.new(
            webhook.secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
        ).hexdigest()
        headers = self.build_delivery_headers(
            event_type=event_type,
            delivery_id=delivery_uuid,
            timestamp=timestamp,
            signature=signature,
        )
        status: Optional[int] = None
        error: Optional[str] = None
        success = False
        attempts = 0

        # Connect to the address that passed, not a fresh resolve, or DNS rebinding slips past the check.
        async with httpx.AsyncClient(timeout=self.DELIVERY_TIMEOUT, follow_redirects=False) as client:
            for attempt in range(self.MAX_ATTEMPTS):
                target: str = webhook.url
                extra_headers: Dict[str, str] = {}
                extensions: Dict[str, str] = {}
                if is_production():
                    try:
                        target, extra_headers, extensions = await pin_public_url(
                            webhook.url, subject='Webhook URL',
                        )
                    except ValueError as exc:
                        logger.warning('Webhook %s delivery blocked by SSRF check: %s', webhook.id, exc)
                        await self.delivery_repository.finish(
                            delivery,
                            response_status=None,
                            attempt_count=prior_attempts + attempts,
                            success=False,
                            error=f"Blocked by SSRF check: {exc}",
                            delivered_at=None,
                        )
                        return
                attempts = attempt + 1
                # follow_redirects=False, so a 3xx to an internal Location is never chased past the host check.
                try:
                    response = await client.post(
                        target, content=body, headers={**headers, **extra_headers},
                        extensions=extensions,
                    )
                    status = response.status_code
                    if 200 <= status < 300:
                        success = True
                        break
                    error = f"HTTP {status}"
                except httpx.HTTPError as exc:
                    error = str(exc) or type(exc).__name__
                if attempt < self.MAX_ATTEMPTS - 1:
                    await asyncio.sleep(self.RETRY_BACKOFF_BASE ** attempt)
        if not success:
            # Warning, not error: a community's own endpoint failing isn't a Sentry event.
            logger.warning(
                'Webhook %s gave up on %s after %d attempt(s): %s',
                webhook.id, event_type, attempts, error,
            )
        await self.delivery_repository.finish(
            delivery,
            response_status=status,
            attempt_count=prior_attempts + attempts,
            success=success,
            error=None if success else error,
            delivered_at=datetime.now(timezone.utc) if success else None,
        )

    async def _require(self, webhook_id: int) -> Webhook:
        return require_found(await self.repository.get_by_id(webhook_id), "Webhook")

    @staticmethod
    async def _clean_event_ids(
        event_ids: Optional[List[int]], kept: Collection[int] = (),
    ) -> Optional[List[int]]:
        """None (every event) or the sorted, distinct ids of this community's live or draft events.

        An event archived since it was picked (in ``kept``) may stay, so editing
        the webhook's name doesn't make staff drop it first.
        """
        if event_ids is None:
            return None
        if not event_ids:
            raise ValueError("Pick at least one event, or send every event's updates.")
        events = {e.id: e for e in await EventService.events_for_tenant(require_tenant_id())}
        for event_id in event_ids:
            event = events.get(event_id)
            if event is None:
                raise NotFoundError(f'Event {event_id} not found')
            if event.is_archived and event_id not in kept:
                raise ValueError(f"{event.name} is archived, so it never sends anything.")
        return sorted(set(event_ids))

    def _validate_event_types(self, event_types: List[str]) -> None:
        if not event_types:
            raise ValueError("Select at least one event type")
        invalid = [t for t in event_types if not EventType.is_valid(t)]
        if invalid:
            raise ValueError(f"Unknown event type(s): {', '.join(invalid)}")

    async def _validate_url(self, url: str) -> None:
        parsed = urlparse((url or '').strip())
        allow_http = not is_production()
        if parsed.scheme != 'https' and not (allow_http and parsed.scheme == 'http'):
            raise ValueError("Webhook URL must use https://")
        if not parsed.hostname:
            raise ValueError("Webhook URL must include a host")
        if is_production():
            await self._ensure_public_host(parsed.hostname)

    async def _ensure_public_host(self, hostname: str) -> None:
        await ensure_public_host(hostname, subject='Webhook URL')
