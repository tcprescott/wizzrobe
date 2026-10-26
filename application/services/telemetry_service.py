"""
Telemetry Service - Business Logic Layer

Engagement telemetry: capturing *how* people use the tool (page views, feature
interactions) plus a mirror of every domain event on the bus, so post-event
analysis can go beyond in-app feedback.

Capture entry points, all best-effort and non-blocking (a telemetry
failure must never break a page render or a mutating call):

* :meth:`record_event` — subscribed to the event bus; mirrors every published
  domain :class:`~application.events.event.Event` into a ``domain`` row.
* :meth:`track_page_view` — called from the ``protected_page`` decorator on
  every authenticated page load.
* :meth:`track_interaction` — called from presentation code for specific
  high-value interactions (report opened, export downloaded, ...).
* :meth:`track_tab_view` — every tab switch on a tabbed hub page.
* :meth:`track_ui_batch` — clicks, dialog opens/closes and field use, batched
  by ``static/js/usage.js`` in the browser.
* :meth:`track_api_call` — every MCP tool call and authenticated REST request,
  so staff can see who uses the integrations and for what.

Reads (the admin engagement report) are Staff-gated at the service boundary,
mirroring :class:`~application.services.webhook_service.WebhookService`, because
the data is cross-user behavioral signal.

Capture honours the ``TELEMETRY_ENABLED`` kill-switch
(:func:`application.utils.environment.telemetry_enabled`).
"""

import json
import logging
from datetime import datetime
from typing import Any, List, Mapping, Optional

from application.events.event import Event
from application.repositories.telemetry_repository import TelemetryRepository
from application.services.auth_service import AuthService
from application.utils.environment import telemetry_enabled
from models import TelemetryEvent, User

logger = logging.getLogger(__name__)


class TelemetryCategory:
    """Coarse buckets stored on ``TelemetryEvent.category`` for fast rollups."""
    PAGE = 'page'
    INTERACTION = 'interaction'
    DOMAIN = 'domain'
    API = 'api'


class TelemetryEventType:
    """Engagement event names not sourced from the domain event bus.

    Domain rows reuse their ``EventType`` string verbatim; these name the
    behavioral signals telemetry itself originates.
    """
    PAGE_VIEW = 'page.view'
    REPORT_VIEWED = 'report.viewed'
    REPORT_EXPORTED = 'report.exported'
    TAB_VIEW = 'tab.view'
    # Reported in batches by static/js/usage.js (see track_ui_batch).
    UI_CLICK = 'ui.click'
    UI_DIALOG_OPENED = 'ui.dialog_opened'
    UI_DIALOG_CLOSED = 'ui.dialog_closed'
    UI_FIELD_USED = 'ui.field_used'
    # One row per MCP tool call / authenticated REST request (track_api_call).
    MCP_TOOL_CALL = 'mcp.tool_call'
    REST_REQUEST = 'rest.request'


#: event_type -> the surface name the API usage reads report.
API_SURFACES = {
    TelemetryEventType.MCP_TOOL_CALL: 'mcp',
    TelemetryEventType.REST_REQUEST: 'rest',
}


#: The client-reported kinds track_ui_batch accepts. The browser is not trusted:
#: anything else in a batch is dropped rather than stored under a made-up name.
UI_EVENT_TYPES = frozenset({
    TelemetryEventType.UI_CLICK,
    TelemetryEventType.UI_DIALOG_OPENED,
    TelemetryEventType.UI_DIALOG_CLOSED,
    TelemetryEventType.UI_FIELD_USED,
})
UI_DEVICES = frozenset({'phone', 'tablet', 'desktop'})
UI_BATCH_MAX = 50
_UI_TEXT_MAX = 120
_UI_DETAIL_KEYS = ('kind', 'dialog', 'menu', 'table', 'via', 'width')


def _clip(value: Any, limit: int = _UI_TEXT_MAX) -> Optional[str]:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = ' '.join(str(value).split())
    return text[:limit] or None


def _encode_details(details: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not details:
        return None
    return json.dumps(dict(details), default=str, sort_keys=True)


class TelemetryService:
    """Capture + Staff-gated read of engagement telemetry."""

    def __init__(self) -> None:
        self.repository = TelemetryRepository()

    # -------------------------------------------------------------- capture

    async def record_event(self, event: Event) -> None:
        """Event-bus subscriber: mirror a published domain event into telemetry.

        Registered with ``event_bus.subscribe_async`` (runs on the dispatch
        worker), so it never blocks the mutating service call. The dispatch
        worker already logs failures, but we guard defensively so a bad row can
        never bubble up and stall the queue.
        """
        if not telemetry_enabled():
            return
        try:
            details: dict[str, Any] = dict(event.payload) if event.payload else {}
            if event.actor_username is not None:
                details.setdefault('actor_username', event.actor_username)
            await self.repository.create(
                category=TelemetryCategory.DOMAIN,
                event_type=event.event_type,
                user_id=event.actor_id,
                details=_encode_details(details),
            )
        except Exception:
            logger.exception("telemetry record_event failed for %s", event.event_type)

    async def track_page_view(
        self,
        *,
        path: str,
        discord_id: Optional[str] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        session_id: Optional[str] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Record an authenticated page load. Best-effort; never raises."""
        await self._capture(
            category=TelemetryCategory.PAGE,
            event_type=TelemetryEventType.PAGE_VIEW,
            path=path,
            discord_id=discord_id,
            user_id=user_id,
            username=username,
            session_id=session_id,
            details=dict(params) if params else None,
        )

    async def track_interaction(
        self,
        *,
        event_type: str,
        path: Optional[str] = None,
        discord_id: Optional[str] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        session_id: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Record a specific UI interaction. Best-effort; never raises."""
        await self._capture(
            category=TelemetryCategory.INTERACTION,
            event_type=event_type,
            path=path,
            discord_id=discord_id,
            user_id=user_id,
            username=username,
            session_id=session_id,
            details=dict(details) if details else None,
        )

    async def track_tab_view(
        self,
        *,
        path: str,
        tab: str,
        discord_id: Optional[str] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Record a switch to a tab on a tabbed hub page (Admin, Home, Volunteer).

        A tab switch rewrites the URL in place rather than loading a page, so
        without this row the hub's page views only say which tab someone landed
        on. ``path`` is the hub plus the tab (``/admin/schedule``).
        """
        if not telemetry_enabled():
            return
        try:
            user_id, username = await self._resolve_user(discord_id, username, user_id)
            await self.repository.create(
                category=TelemetryCategory.INTERACTION,
                event_type=TelemetryEventType.TAB_VIEW,
                user_id=user_id,
                path=_clip(path, 512),
                session_id=session_id,
                target=_clip(tab, 200),
                details=_encode_details({'actor_username': username} if username else None),
            )
        except Exception:
            logger.exception("telemetry tab view failed")

    async def track_ui_batch(
        self,
        events: Any,
        *,
        discord_id: Optional[str] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """Store a batch of client-reported UI interactions; returns rows kept.

        The batch comes from the browser, so every field is bounded and anything
        malformed is dropped. Never raises.
        """
        if not telemetry_enabled() or not isinstance(events, list):
            return 0
        try:
            user_id, username = await self._resolve_user(discord_id, username, user_id)
            rows: list[dict] = []
            for raw in events[:UI_BATCH_MAX]:
                if not isinstance(raw, dict) or raw.get('type') not in UI_EVENT_TYPES:
                    continue
                target = _clip(raw.get('target'), 200)
                if target is None:
                    continue
                details: dict[str, Any] = {
                    key: _clip(raw.get(key)) for key in _UI_DETAIL_KEYS
                    if _clip(raw.get(key)) is not None
                }
                if username is not None:
                    details['actor_username'] = username
                device = raw.get('device')
                rows.append({
                    'category': TelemetryCategory.INTERACTION,
                    'event_type': raw['type'],
                    'user_id': user_id,
                    'path': _clip(raw.get('path'), 512),
                    'session_id': session_id,
                    'target': target,
                    'device': device if device in UI_DEVICES else None,
                    'details': _encode_details(details),
                })
            if rows:
                await self.repository.bulk_create(rows)
            return len(rows)
        except Exception:
            logger.exception("telemetry ui batch failed")
            return 0

    async def track_api_call(
        self,
        *,
        event_type: str,
        target: str,
        path: str,
        user_id: int,
        username: Optional[str],
        outcome: str,
    ) -> None:
        """Record one MCP tool call or authenticated REST request. Never raises.

        ``target`` is the tool name or ``METHOD /route/{template}`` (never the
        filled-in path, so ids don't split a route into one row per record);
        ``outcome`` is ``ok`` or the error code / HTTP status. Arguments and
        bodies are never stored. The caller binds the community.
        """
        if not telemetry_enabled() or event_type not in API_SURFACES:
            return
        try:
            details: dict[str, Any] = {'outcome': outcome}
            if username is not None:
                details['actor_username'] = username
            await self.repository.create(
                category=TelemetryCategory.API,
                event_type=event_type,
                user_id=user_id,
                path=_clip(path, 512),
                target=_clip(target, 200),
                details=_encode_details(details),
            )
        except Exception:
            logger.exception("telemetry api call failed for %s", target)

    @staticmethod
    async def _resolve_user(
        discord_id: Optional[str], username: Optional[str], user_id: Optional[int] = None,
    ) -> tuple[Optional[int], Optional[str]]:
        # Resolve to a User for the FK when possible, but attribute even
        # deactivated accounts (unlike get_user_from_discord_id, which hides
        # them) — telemetry is about who did what, not who may act now.
        if discord_id:
            user = await User.get_or_none(discord_id=discord_id)
        elif user_id:
            user = await User.get_or_none(id=user_id)
        else:
            return None, username
        if user is None:
            return None, username
        return user.id, username or getattr(user, 'username', None)

    async def _capture(
        self,
        *,
        category: str,
        event_type: str,
        path: Optional[str],
        discord_id: Optional[str],
        username: Optional[str],
        session_id: Optional[str],
        details: Optional[dict],
        user_id: Optional[int] = None,
    ) -> None:
        if not telemetry_enabled():
            return
        try:
            user_id, username = await self._resolve_user(discord_id, username, user_id)
            enriched = dict(details) if details else {}
            if username is not None:
                enriched.setdefault('actor_username', username)
            await self.repository.create(
                category=category,
                event_type=event_type,
                user_id=user_id,
                path=path,
                session_id=session_id,
                details=_encode_details(enriched),
            )
        except Exception:
            logger.exception("telemetry capture failed for %s", event_type)

    # ---------------------------------------------------------------- reads

    async def list_events(
        self,
        actor: User,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_id: Optional[int] = None,
        category: Optional[str] = None,
        event_type: Optional[str] = None,
        user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        path_contains: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[TelemetryEvent]:
        await self._ensure_staff(actor)
        return await self.repository.list(
            start=start, end=end, event_id=event_id, category=category, event_type=event_type,
            user_id=user_id, session_id=session_id, path_contains=path_contains,
            limit=limit, offset=offset,
        )

    async def count_events(
        self,
        actor: User,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_id: Optional[int] = None,
        category: Optional[str] = None,
        event_type: Optional[str] = None,
        user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        path_contains: Optional[str] = None,
    ) -> int:
        await self._ensure_staff(actor)
        return await self.repository.count(
            start=start, end=end, event_id=event_id, category=category, event_type=event_type,
            user_id=user_id, session_id=session_id, path_contains=path_contains,
        )

    async def engagement_summary(
        self,
        actor: User,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_id: Optional[int] = None,
    ) -> dict:
        """Top-line KPIs for the window: totals + distinct reach."""
        await self._ensure_staff(actor)
        api_users = await self.repository.count_distinct_users_by_event_type(
            start=start, end=end, event_id=event_id, category=TelemetryCategory.API,
        )
        return {
            'total_events': await self.repository.count(start=start, end=end, event_id=event_id),
            'unique_users': await self.repository.count_distinct_users(start=start, end=end, event_id=event_id),
            'unique_sessions': await self.repository.count_distinct_sessions(start=start, end=end, event_id=event_id),
            'page_views': await self.repository.count(
                start=start, end=end, event_id=event_id, category=TelemetryCategory.PAGE,
            ),
            'mcp_users': api_users.get(TelemetryEventType.MCP_TOOL_CALL, 0),
            'rest_users': api_users.get(TelemetryEventType.REST_REQUEST, 0),
        }

    async def top_paths(
        self,
        actor: User,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_id: Optional[int] = None,
        user_id: Optional[int] = None,
        limit: int = 15,
    ) -> List[dict]:
        await self._ensure_staff(actor)
        return await self.repository.top_paths(
            start=start, end=end, event_id=event_id, category=TelemetryCategory.PAGE, user_id=user_id, limit=limit,
        )

    async def top_event_types(
        self,
        actor: User,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_id: Optional[int] = None,
        user_id: Optional[int] = None,
        limit: int = 20,
    ) -> List[dict]:
        await self._ensure_staff(actor)
        return await self.repository.top_event_types(start=start, end=end, event_id=event_id, user_id=user_id, limit=limit)

    async def top_controls(
        self,
        actor: User,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_id: Optional[int] = None,
        user_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[dict]:
        """Most-used tabs, buttons, menu items, dialogs and fields, with how
        many distinct people used each and how many uses came from a phone."""
        await self._ensure_staff(actor)
        return await self.repository.top_controls(
            start=start, end=end, event_id=event_id, category=TelemetryCategory.INTERACTION, user_id=user_id, limit=limit,
        )

    async def top_api_calls(
        self,
        actor: User,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_id: Optional[int] = None,
        user_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[dict]:
        """Most-called MCP tools and REST routes, with distinct callers and failures."""
        await self._ensure_staff(actor)
        rows = await self.repository.top_api_targets(
            start=start, end=end, event_id=event_id, category=TelemetryCategory.API, user_id=user_id, limit=limit,
        )
        return [
            {
                'surface': API_SURFACES.get(r['event_type'], r['event_type']),
                'target': r['target'],
                'calls': r['calls'],
                'users': r['users'],
                'errors': r['errors'],
            }
            for r in rows
        ]

    async def top_api_users(
        self,
        actor: User,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_id: Optional[int] = None,
        user_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[dict]:
        """Who calls the MCP server and REST API: one row per person per surface."""
        await self._ensure_staff(actor)
        rows = await self.repository.top_api_users(
            start=start, end=end, event_id=event_id, category=TelemetryCategory.API, user_id=user_id, limit=limit,
        )
        names = await self._user_names([r['user_id'] for r in rows])
        return [
            {
                'user_id': r['user_id'],
                'user': names.get(r['user_id'], f"User {r['user_id']}"),
                'surface': API_SURFACES.get(r['event_type'], r['event_type']),
                'calls': r['calls'],
                'targets': r['targets'],
                'last_call': r['last_call'],
            }
            for r in rows
        ]

    async def top_users(
        self,
        actor: User,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_id: Optional[int] = None,
        user_id: Optional[int] = None,
        limit: int = 15,
    ) -> List[dict]:
        """Busiest users with their display names resolved for the report."""
        await self._ensure_staff(actor)
        rows = await self.repository.top_users(start=start, end=end, event_id=event_id, user_id=user_id, limit=limit)
        names = await self._user_names([r['user_id'] for r in rows])
        return [
            {
                'user_id': r['user_id'],
                'user': names.get(r['user_id'], f"User {r['user_id']}"),
                'events': r['events'],
                'sessions': r['sessions'],
            }
            for r in rows
        ]

    @staticmethod
    async def _user_names(user_ids: List[int]) -> dict[int, str]:
        names: dict[int, str] = {}
        if user_ids:
            for u in await User.filter(id__in=user_ids):
                names[u.id] = getattr(u, 'preferred_name', None) or u.username or f'User {u.id}'
        return names

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can view engagement telemetry",
        )
