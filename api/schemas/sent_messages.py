"""Sent messages schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class SentMessageEntry(BaseModel):
    id: int = Field(..., description="ID of the logged message.")
    recipient_id: Optional[int] = Field(None, description="ID of the person it went to; null when no account matched.")
    recipient_name: Optional[str] = Field(None, description="Display name of the person it went to.")
    discord_id: int = Field(..., description="Discord ID it was sent to.")
    title: Optional[str] = Field(None, description="The card title, or the message's first line.")
    body: str = Field(..., description="The plain text of the message, as mirrored to devices, email and Telegram.")
    link_label: Optional[str] = Field(None, description="Label of the message's link button.")
    link_url: Optional[str] = Field(None, description="Where the link button goes.")
    silent: bool = Field(..., description="Sent without a ping or phone buzz.")
    notice_key: Optional[str] = Field(None, description="Which kind of notification it was, when the sender named one.")
    audience: Optional[str] = Field(None, description="Which recipient group it went to, when the sender named one.")
    status: str = Field(
        ...,
        description=(
            "What Discord did with it: `delivered`, `discord_off` (the person turned Discord DMs off), "
            "`blocked` (Discord refused or the account is gone), `failed`, or `pending` (still sending)."
        ),
    )
    error: Optional[str] = Field(None, description="Why Discord didn't deliver it, for `blocked` and `failed`.")
    push_devices: Optional[int] = Field(None, description="Phones and browsers the device notification reached.")
    email_sent: Optional[bool] = Field(None, description="Whether the email copy went out; null when none was due.")
    telegram_sent: Optional[bool] = Field(None, description="Whether the Telegram copy went out; null when none was due.")
    created_at: datetime = Field(..., description="When it was sent (UTC).")


class SentMessageCounts(BaseModel):
    sent: int = Field(..., description="Messages sent in the window, whatever happened to them.")
    silent: int = Field(..., description="Of those, how many were silent.")
    off_or_blocked: int = Field(..., description="Not delivered on Discord because DMs were off or refused.")
    failed: int = Field(..., description="Discord errors and timeouts.")


class SentMessagePage(BaseModel):
    total: int = Field(..., description="Messages matching the filters, across all pages.")
    limit: int = Field(..., description="Page size used for this response.")
    offset: int = Field(..., description="Messages skipped before this page.")
    counts: SentMessageCounts = Field(..., description="Totals over the time window, ignoring the other filters.")
    items: List[SentMessageEntry] = Field(..., description="This page of messages, newest first.")
