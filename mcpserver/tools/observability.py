"""Audit history, engagement telemetry, integrations, and system configuration.

Gates mirror ``api/routers/audit.py`` (staff), ``api/routers/system_config.py``
(staff), ``api/routers/webhooks.py`` (staff), ``api/routers/service_health.py``
(staff for the tenant subset), and the telemetry service's own staff check.
``runtime_stats`` describes the whole process, so it names no community and
``RuntimeStatsService`` refuses anyone but a super admin.

``list_feedback`` mirrors ``api/routers/feedback.py``'s staff queue and carries
that router's ``FEEDBACK`` flag. ``FeedbackService`` refuses a community with
the feature off regardless, but declaring the flag here is what hides the tool
instead of letting a model discover the refusal a round-trip later.
"""

from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer

from api.schemas.notification_settings import NotificationSettingResponse
from api.schemas.notifications import MyNotificationsResponse
from application.services import (
    AuditService,
    FeedbackService,
    RuntimeStatsService,
    SentMessageService,
    ServiceHealthService,
    TelemetryService,
    WebhookService,
)
from application.tenant_context import require_tenant_id
from application.utils.serialization import decode_json_details
from mcpserver.auth import Gate, current_actor
from mcpserver.registry import register
from mcpserver.schemas import (
    AuditEntry,
    AuditPage,
    FeedbackEntry,
    RuntimeStats,
    SentMessageInfo,
    SentMessagePage,
    ServiceHealthProbe,
    TenantArg,
    WebhookDeliveryInfo,
    WebhookInfo,
)
from mcpserver.tools._args import choose, clamp, parse_ts
from models import FeatureFlag

MAX_ROWS = 500


async def list_audit_log(
    tenant: TenantArg = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    user_id: Optional[int] = None,
    action_contains: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> AuditPage:
    """Search the community's audit history — who changed what, and when.

    Requires Staff. Actions are namespaced `verb.object` strings such as
    `match.created`; `action_contains` does a substring match. Times are UTC.
    """
    limit = clamp(limit, low=1, high=MAX_ROWS)
    offset = max(0, offset)
    start_dt, end_dt = parse_ts('start', start), parse_ts('end', end)

    service = AuditService()
    actor = current_actor().user
    total = await service.count_logs(
        actor=actor, start=start_dt, end=end_dt, user_id=user_id, action_contains=action_contains,
    )
    logs = await service.list_logs(
        actor=actor, start=start_dt, end=end_dt, user_id=user_id, action_contains=action_contains,
        limit=limit, offset=offset,
    )
    return AuditPage(
        total=total, limit=limit, offset=offset,
        items=[
            AuditEntry(
                id=log.id, user_id=log.user_id, action=log.action,
                details=decode_json_details(log.details), created_at=log.created_at,
            )
            for log in logs
        ],
    )


async def list_sent_messages(
    tenant: TenantArg = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    user_id: Optional[int] = None,
    search: Optional[str] = None,
    problems_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> SentMessagePage:
    """Every DM Wizzrobe sent in the community, newest first, and whether it got there.

    Requires Staff. Use it to check whether someone was told about something:
    filter by `user_id`, or `search` the text and recipient names.
    `problems_only` keeps the ones Discord refused or failed to deliver. Each
    row says what Discord did (`status`, `error`) and whether the device, email
    and Telegram copies went out. `counts` totals the whole time window. Times
    are UTC.
    """
    limit = clamp(limit, low=1, high=MAX_ROWS)
    offset = max(0, offset)
    start_dt, end_dt = parse_ts('start', start), parse_ts('end', end)
    service = SentMessageService()
    actor = current_actor().user
    query: Dict[str, Any] = dict(
        actor=actor, start=start_dt, end=end_dt, problems_only=problems_only,
        search=search or None, recipient_id=user_id,
    )
    total = await service.count_messages(**query)
    messages = await service.list_messages(**query, limit=limit, offset=offset)
    counts = await service.totals(actor=actor, start=start_dt, end=end_dt)
    return SentMessagePage(
        total=total, limit=limit, offset=offset, counts=counts,
        items=[
            SentMessageInfo(
                id=m.id, recipient_id=m.recipient_id,
                recipient_name=m.recipient.preferred_name if m.recipient else None,
                discord_id=int(m.discord_id), title=m.title, body=m.body,
                link_label=m.link_label, link_url=m.link_url, silent=m.silent,
                notice_key=m.notice_key, audience=m.audience, status=m.status.value,
                error=m.error, push_devices=m.push_devices, email_sent=m.email_sent,
                telegram_sent=m.telegram_sent, created_at=m.created_at,
            )
            for m in messages
        ],
    )


async def telemetry_summary(
    tenant: TenantArg = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """Engagement totals for a window: events, unique users, sessions, page views.

    Requires staff access.
    """
    actor = current_actor()
    return await TelemetryService().engagement_summary(
        actor.user, start=parse_ts('start', start), end=parse_ts('end', end),
    )


async def telemetry_top(
    dimension: str,
    tenant: TenantArg = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 15,
) -> List[Dict[str, Any]]:
    """Top engagement rows for one dimension: `paths`, `event_types`, `users`,
    or `controls` (the tabs, buttons, menu items, dialogs and fields people used,
    per page, with distinct users and how many uses came from a phone).

    Requires staff access.
    """
    actor = current_actor()
    limit = clamp(limit, low=1, high=100)
    start_dt, end_dt = parse_ts('start', start), parse_ts('end', end)
    service = TelemetryService()
    fn = choose(dimension, {
        'paths': service.top_paths,
        'event_types': service.top_event_types,
        'users': service.top_users,
        'controls': service.top_controls,
    })
    return await fn(actor.user, start=start_dt, end=end_dt, limit=limit)


async def get_system_config(
    tenant: TenantArg = None,
) -> List[Dict[str, Any]]:
    """Read the community's system configuration entries.

    Requires staff access.
    """
    from application.services import SystemConfigService

    entries = await SystemConfigService.list_entries(current_actor().user)
    return [{'name': name, 'value': value} for name, value in entries]


async def list_notification_settings(
    tenant: TenantArg = None,
) -> List[NotificationSettingResponse]:
    """How this community sends every message, once per kind of recipient.

    Each row is `off`, `silent` (arrives without a ping) or `ping`, with the
    default beside it. Mirrors `GET /api/notification-settings`. Requires staff
    access.
    """
    from application.services import NotificationSettingsService

    return [NotificationSettingResponse.from_row(r) for r in await NotificationSettingsService.list_settings(current_actor().user)]


async def my_notification_settings(
    tenant: TenantArg = None,
) -> MyNotificationsResponse:
    """How each message reaches you in this community: your preset, your own
    choices and the community's setting.

    `mode` is what you actually get, the quieter of the community's setting and
    yours. Change it with `set_my_notification_preset` and
    `set_my_notification`. Mirrors `GET /api/notifications/messages`.
    """
    from application.services import NotificationPreferenceService

    return MyNotificationsResponse.from_mine(await NotificationPreferenceService.get_mine(current_actor().user))


async def list_service_health(
    tenant: TenantArg = None,
) -> List[ServiceHealthProbe]:
    """Check the community's own integrations: its racetime bots and Challonge link.

    Requires staff access. Probed live, so this is current rather than cached.
    This is the tenant subset — the platform-wide board is a super-admin surface
    and is not exposed here.
    """
    probes = await ServiceHealthService().tenant_subset(require_tenant_id(), actor=current_actor().user)
    return [
        ServiceHealthProbe(
            key=probe.key,
            label=probe.label,
            category=probe.category,
            status=getattr(probe.status, 'value', probe.status),
            message=probe.message,
            checked_at=probe.checked_at,
        )
        for probe in probes
    ]


async def list_webhooks(
    tenant: TenantArg = None,
) -> List[WebhookInfo]:
    """List the community's outbound webhooks and the events each subscribes to.

    Requires staff access. Signing secrets are never returned.
    """
    hooks = await WebhookService().list_webhooks(current_actor().user)
    return [
        WebhookInfo(
            id=hook.id,
            name=hook.name,
            url=hook.url,
            event_types=list(hook.event_types or []),
            is_active=hook.is_active,
        )
        for hook in hooks
    ]


async def list_webhook_deliveries(
    webhook_id: int,
    tenant: TenantArg = None,
    limit: int = 50,
    offset: int = 0,
) -> List[WebhookDeliveryInfo]:
    """List recent delivery attempts for one webhook — what fired, and what failed.

    Requires staff access. The serialized request payload is omitted; use
    `list_audit_log` for what the underlying change was.
    """
    deliveries = await WebhookService().list_deliveries(
        current_actor().user, webhook_id,
        limit=clamp(limit, low=1, high=MAX_ROWS), offset=max(0, offset),
    )
    return [
        WebhookDeliveryInfo(
            id=row.id,
            event_type=row.event_type,
            success=row.success,
            response_status=row.response_status,
            attempt_count=row.attempt_count,
            error=row.error,
            created_at=row.created_at,
            delivered_at=row.delivered_at,
        )
        for row in deliveries
    ]


async def list_feedback(
    tenant: TenantArg = None,
    limit: int = 50,
) -> List[FeedbackEntry]:
    """List feedback users submitted from the site, most recent first.

    Requires staff access.
    """
    rows = await FeedbackService().list_recent(
        current_actor().user, limit=clamp(limit, low=1, high=MAX_ROWS),
    )
    return [
        FeedbackEntry(
            id=row.id,
            category=getattr(row.category, 'value', row.category),
            status=getattr(row.status, 'value', row.status),
            message=row.message,
            page_url=row.page_url,
            submitted_by=row.user.preferred_name if row.user else None,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def runtime_stats() -> RuntimeStats:
    """Live load on the Wizzrobe process: event-loop lag and stalls, CPU,
    memory, connected tabs, DB pool and background worker timing.

    Requires super admin. Start here when the app feels slow: a high
    `lag_ms_p95` or any `recent_stalls` means something is blocking the one
    event loop every user shares, and each stall names the code that did it.
    Times are Unix seconds, UTC.
    """
    return RuntimeStats.model_validate(
        await RuntimeStatsService.snapshot(current_actor().user)
    )


def register_tools(mcp: MCPServer) -> None:
    register(mcp, list_audit_log, gate=Gate.STAFF, title='Search audit log')
    register(mcp, list_sent_messages, gate=Gate.STAFF, title='List sent messages')
    register(mcp, telemetry_summary, gate=Gate.STAFF, title='Telemetry summary')
    register(mcp, telemetry_top, gate=Gate.STAFF, title='Telemetry leaderboards')
    register(mcp, get_system_config, gate=Gate.STAFF, title='Get system config')
    register(mcp, list_notification_settings, gate=Gate.STAFF, title='List notification settings')
    register(mcp, my_notification_settings, gate=Gate.ACTOR, title='My notification settings')
    register(mcp, list_service_health, gate=Gate.STAFF, title='Service health')
    register(mcp, runtime_stats, gate=Gate.PLATFORM, title='Runtime stats')
    register(mcp, list_webhooks, gate=Gate.STAFF, title='List webhooks')
    register(
        mcp, list_webhook_deliveries, gate=Gate.STAFF,
        title='List webhook deliveries',
    )
    register(
        mcp, list_feedback, gate=Gate.STAFF,
        feature=FeatureFlag.FEEDBACK, title='List feedback',
    )
