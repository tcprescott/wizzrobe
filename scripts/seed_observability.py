"""Dev-seed fixtures for one tenant's outbound + telemetry surfaces.

Split out of ``seed_dev.py`` when that module crossed the 800-line budget,
following the ``seed_brackets_for_tenant`` / ``seed_challonge_for_tenant``
convention already used there: one module per domain, called from inside the
caller's ``tenant_scope``. Idempotent (``get_or_create`` / existence-guarded)
and tenant-stamped like every other seeded row.

The audit-log fixtures deliberately stayed behind in ``seed_dev.py``: they
reference half a dozen other seeded rows (a proctor, an equipment card, a
tournament), and threading all of those through here would cost more clarity
than the split buys.
"""

import json

from models import (
    Event,
    Feedback,
    FeedbackCategory,
    FeedbackStatus,
    Match,
    PersonalWebhook,
    TelemetryEvent,
    Tenant,
    User,
    Webhook,
    WebhookDelivery,
)


async def seed_observability_for_tenant(
    tenant: Tenant, staff: User, finished_match: Match, now_utc,
    users: dict[str, User] | None = None,
) -> None:
    """Seed the dev webhook + deliveries, the feedback queue, and telemetry rows."""
    # Inactive so a dev session never attempts outbound deliveries.
    webhook, _ = await Webhook.get_or_create(
        name="Dev Webhook (inactive)", tenant=tenant,
        defaults={
            "url": "http://127.0.0.1:9/dev-webhook",
            "secret": "dev-webhook-secret-not-real",
            "event_types": ["*"],
            "is_active": False,
        },
    )
    default_event = await Event.filter(tenant=tenant, is_default=True).first()
    if default_event is not None:
        await Webhook.get_or_create(
            name="Dev Webhook (one event)", tenant=tenant,
            defaults={
                "url": "http://127.0.0.1:9/dev-webhook-one-event",
                "secret": "dev-webhook-secret-not-real",
                "event_types": ["match.rescheduled", "match.stage_assigned", "crew.approval_changed"],
                "event_ids": [default_event.id],
                "is_active": False,
            },
        )
    if not await WebhookDelivery.filter(webhook=webhook, tenant=tenant).exists():
        payload = json.dumps({"match_id": finished_match.id}, sort_keys=True)
        await WebhookDelivery.create(
            tenant=tenant, webhook=webhook, event_type="match.created",
            payload=payload, response_status=200, attempt_count=1, success=True,
            delivered_at=now_utc,
        )
        # Keep both inside WebhookService.HEALTH_WINDOW.
        for event_type, status in (("match.started", 500), ("match.finished", 502)):
            await WebhookDelivery.create(
                tenant=tenant, webhook=webhook, event_type=event_type,
                payload=payload, response_status=status, attempt_count=3,
                success=False, error=f"HTTP {status} from receiver",
            )
    await Webhook.get_or_create(
        name="Schedule feed (inactive)", tenant=tenant,
        defaults={
            "url": "http://127.0.0.1:9/dev-schedule-feed",
            "secret": "dev-webhook-secret-not-real",
            "event_types": [
                "match.rescheduled", "match.stage_assigned", "match.stage_cleared",
                "match.deleted", "match.cancelled", "crew.approval_changed",
            ],
            "is_active": False,
        },
    )
    print(f"    [{tenant.slug}] webhooks ok (1 delivered, 2 failed in the health window)")

    # Pointed at a closed localhost port, so a dev event fails fast and never leaves the machine.
    owner = (users or {}).get("player_one")
    if owner is not None:
        await PersonalWebhook.get_or_create(
            name="Stream overlay", tenant=tenant, user=owner,
            defaults={
                "url": "http://127.0.0.1:9/dev-personal-hook",
                "secret": "dev-personal-secret-not-real",
                "event_types": ["match.started", "match.finished", "match.result_recorded"],
                "only_mine": False,
                "delivered_count": 12,
                "last_delivery_at": now_utc,
                "last_status": 204,
            },
        )
        await PersonalWebhook.get_or_create(
            name="My match pings", tenant=tenant, user=owner,
            defaults={
                "url": "https://discord.com/api/webhooks/000000000000/dev-not-real",
                "secret": "dev-personal-secret-not-real",
                "event_types": ["match.created", "match.rescheduled", "crew.approval_changed"],
                "only_mine": True,
                "is_active": False,
                "paused_reason": "Paused: Discord says this webhook no longer exists.",
                "consecutive_failures": 1,
                "failed_count": 1,
                "last_delivery_at": now_utc,
                "last_status": 404,
                "last_error": "HTTP 404",
            },
        )
        print(f"    [{tenant.slug}] personal webhooks ok (player_one: one live, one paused)")

    feedback_specs = [] if not users else [
        ("player_one", FeedbackCategory.BUG, FeedbackStatus.NEW,
         "Schedule times looked off on mobile.", "/home/schedule"),
        ("player_two", FeedbackCategory.SUGGESTION, FeedbackStatus.REVIEWED,
         "Would love a dark mode toggle.", "/"),
        ("sm_user", FeedbackCategory.PRAISE, FeedbackStatus.NEW,
         "The new crew view is great, thanks!", "/admin/schedule"),
        ("player_three", FeedbackCategory.OTHER, FeedbackStatus.REVIEWED,
         "Who do I ask about getting my racetime name fixed?", "/home/profile"),
        ("player_one", FeedbackCategory.SUGGESTION, FeedbackStatus.REVIEWED,
         "Could the bracket view remember my zoom level?", "/brackets/1"),
    ]
    for uname, category, status, message, page_url in feedback_specs:
        if not await Feedback.filter(user=users[uname], message=message, tenant=tenant).exists():
            await Feedback.create(
                user=users[uname], category=category, status=status,
                message=message, page_url=page_url, tenant=tenant,
            )
    if feedback_specs:
        print(f"    [{tenant.slug}] feedback ok (new + reviewed, both states on player_one)")

    telemetry_specs = [
        ("page", "page.view", f"/t/{tenant.slug}/", "sess-dev-1"),
        ("interaction", "report.exported", f"/t/{tenant.slug}/admin", "sess-dev-1"),
        ("domain", "match.created", None, None),
    ]
    if await TelemetryEvent.filter(tenant=tenant).count() == 0:
        for category, event_type, path, session_id in telemetry_specs:
            await TelemetryEvent.create(
                tenant=tenant, user=staff, category=category,
                event_type=event_type, path=path, session_id=session_id,
                details=json.dumps({"seed": True}),
            )
    usage_specs = [
        ("tab.view", "/admin/schedule", "schedule", None),
        ("ui.click", "/admin/schedule", "New match", "desktop"),
        ("ui.dialog_opened", "/admin/schedule", "Create Match", "desktop"),
        ("ui.dialog_closed", "/admin/schedule", "Create Match", "phone"),
    ]
    if not await TelemetryEvent.filter(tenant=tenant, target__isnull=False).exists():
        for event_type, path, target, device in usage_specs:
            await TelemetryEvent.create(
                tenant=tenant, user=staff, category="interaction",
                event_type=event_type, path=path, session_id="sess-dev-1",
                target=target, device=device, details=json.dumps({"seed": True}),
            )
    print(f"    [{tenant.slug}] telemetry ok")
