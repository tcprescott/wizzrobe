"""Sent messages endpoint (read, staff only)."""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from api.dependencies import ServiceErrorRoute, event_filter, require_staff
from api.schemas.sent_messages import SentMessageCounts, SentMessageEntry, SentMessagePage
from application.services import SentMessageService
from models import SentMessage, User

router = APIRouter(prefix="/sent-messages", tags=["Sent messages"], route_class=ServiceErrorRoute)


def _entry(m: SentMessage) -> SentMessageEntry:
    return SentMessageEntry(
        id=m.id,
        recipient_id=m.recipient_id,
        recipient_name=m.recipient.preferred_name if m.recipient else None,
        discord_id=int(m.discord_id),
        title=m.title,
        body=m.body,
        link_label=m.link_label,
        link_url=m.link_url,
        silent=m.silent,
        notice_key=m.notice_key,
        audience=m.audience,
        status=m.status.value,
        error=m.error,
        push_devices=m.push_devices,
        email_sent=m.email_sent,
        telegram_sent=m.telegram_sent,
        created_at=m.created_at,
    )


@router.get(
    "",
    response_model=SentMessagePage,
    summary="List sent messages",
    description=(
        "Returns every DM Wizzrobe sent in the community, newest first, with what Discord did with it "
        "and whether the device, email and Telegram copies went out. Page through with `limit` and "
        "`offset`; `counts` totals the whole time window.\n\n"
        "**Who can call this:** Staff."
    ),
)
async def list_sent_messages(
    start: Optional[datetime] = Query(None, description="Only messages sent at or after this time (UTC ISO 8601)."),
    end: Optional[datetime] = Query(None, description="Only messages sent before this time (UTC ISO 8601)."),
    user_id: Optional[int] = Query(None, description="Only messages to this user ID."),
    search: Optional[str] = Query(None, description="Only messages whose text or recipient name contains this, ignoring case."),
    problems_only: bool = Query(False, description="Only messages Discord refused or failed to deliver."),
    limit: int = Query(50, ge=1, le=500, description="Maximum messages to return."),
    offset: int = Query(0, ge=0, description="Messages skipped before this page."),
    actor: User = Depends(require_staff),
    event_id: Optional[int] = Depends(event_filter),
):
    service = SentMessageService()
    query: dict[str, Any] = dict(
        actor=actor, start=start, end=end, problems_only=problems_only,
        search=search or None, recipient_id=user_id, event_id=event_id,
    )
    total = await service.count_messages(**query)
    messages = await service.list_messages(**query, limit=limit, offset=offset)
    counts = await service.totals(actor=actor, start=start, end=end, event_id=event_id)
    return SentMessagePage(
        total=total, limit=limit, offset=offset,
        counts=SentMessageCounts(**counts), items=[_entry(m) for m in messages],
    )
