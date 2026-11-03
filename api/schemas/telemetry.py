"""Schemas for the engagement telemetry endpoints."""

from datetime import datetime
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class TelemetrySummaryResponse(BaseModel):
    total_events: int = Field(..., description="All telemetry rows recorded in the window.")
    unique_users: int = Field(..., description="Distinct signed-in people seen in the window.")
    unique_sessions: int = Field(..., description="Distinct browser sessions seen in the window.")
    page_views: int = Field(..., description="Page loads recorded in the window.")
    mcp_users: int = Field(..., description="Distinct people who called an MCP tool in the window.")
    rest_users: int = Field(..., description="Distinct people who made an authenticated REST API request in the window.")
    graphql_users: int = Field(..., description="Distinct people who ran a GraphQL query in the window.")


class _Row(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TopPathRow(_Row):
    path: str = Field(..., description="Page path that was loaded.")
    views: int = Field(..., description="Page loads of that path.")
    users: int = Field(..., description="Distinct people who loaded it.")


class TopEventTypeRow(_Row):
    category: str = Field(
        ...,
        description=(
            "Row category: `page` (page loads), `interaction` (clicks, tab switches and other UI use) "
            "or `domain` (a copy of an event published by the app, such as a match being created)."
        ),
    )
    event_type: str = Field(..., description="Event name, such as `page.view` or `match.created`.")
    count: int = Field(..., description="Rows with this category and event name.")


class TopUserRow(_Row):
    user_id: int = Field(..., description="ID of the person.")
    user: str = Field(..., description="Display name of the person.")
    events: int = Field(..., description="Telemetry rows attributed to them.")
    sessions: int = Field(..., description="Distinct browser sessions they used.")


class TopControlRow(_Row):
    path: Optional[str] = Field(None, description="Page the control is on.")
    event_type: str = Field(
        ...,
        description=(
            "Kind of use: `tab.view`, `ui.click`, `ui.dialog_opened`, `ui.dialog_closed` or `ui.field_used`."
        ),
    )
    target: str = Field(..., description="The tab, button, menu item, dialog or field that was used.")
    uses: int = Field(..., description="How many times it was used.")
    users: int = Field(..., description="Distinct people who used it.")
    phone: int = Field(..., description="Uses that came from a phone.")


class TopApiCallRow(_Row):
    surface: str = Field(..., description="`mcp` for an MCP tool call, `rest` for a REST API request.")
    target: str = Field(..., description="The MCP tool name, or the REST method and route template.")
    calls: int = Field(..., description="How many times it was called.")
    users: int = Field(..., description="Distinct people who called it.")
    errors: int = Field(..., description="Calls that failed (an MCP error or an HTTP status of 400 or above).")


class TopApiUserRow(_Row):
    user_id: int = Field(..., description="ID of the person.")
    user: str = Field(..., description="Display name of the person.")
    surface: str = Field(..., description="`mcp` or `rest`.")
    calls: int = Field(..., description="Calls they made on that surface.")
    targets: int = Field(..., description="Distinct tools or routes they called.")
    last_call: datetime = Field(..., description="When they last called it (UTC).")


TelemetryTopRow = Union[
    TopPathRow, TopEventTypeRow, TopUserRow, TopControlRow, TopApiCallRow, TopApiUserRow,
]
