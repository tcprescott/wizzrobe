"""One event's own settings, one group per pane of Admin → Event settings.

The typed counterpart of ``/api/events/{slug}/settings``. Like every
event-owned router it works in the event ``?event=`` names (the default event
when left out), and a call about a station or room screen works in that row's
event. Staff only; the services check again.
"""

import json
from datetime import timedelta
from typing import List

from fastapi import APIRouter, Depends, Response, status

from api.dependencies import ServiceErrorRoute, event_binding, require_feature, require_staff, require_staff_write
from api.schemas.event_settings import (
    CapacitySettings,
    CrewSettings,
    DayHours,
    EventDatesResponse,
    EventDatesUpdate,
    EventHoursUpdate,
    EventTimezoneResponse,
    EventTimezoneUpdate,
    RewardTiersResponse,
    RewardTiersUpdate,
    RoomScreenCreate,
    RoomScreenIssued,
    RoomScreenResponse,
    StationCreate,
    StationFormatOption,
    StationFormatResponse,
    StationFormatSettings,
    StationResponse,
    StationUpdate,
)
from application.services import RoomTokenService, StationService, SystemConfigService, TenantService, TimezoneService
from application.services.room_token_service import room_seeds_path
from application.services.station_service import STATION_FORMAT_LABELS
from application.services.system_config_service import (
    KEY_BROADCASTERS,
    KEY_EVENT_END_DATE,
    KEY_EVENT_START_DATE,
    KEY_MAX_CONCURRENT_PLAYERS,
    KEY_MAX_CONCURRENT_STAGES,
    KEY_STATION_FORMAT,
    KEY_TOURNAMENT_HOURS,
    KEY_VOLUNTEER_COMP_TIERS,
)
from application.services.timezone_service import COMMON_TIMEZONES
from application.tenant_context import require_tenant_id
from application.timezone_context import tz_scope
from application.utils.tenant_urls import tenant_url
from application.utils.timezone import timezone_label
from models import FeatureFlag, RoomToken, Station, User

router = APIRouter(
    prefix="/event-settings",
    tags=["Event settings"],
    route_class=ServiceErrorRoute,
    dependencies=[Depends(event_binding(station_id=Station, screen_id=RoomToken))],
)

STAFF_ONLY = "**Who can call this:** Staff."


async def _dates() -> EventDatesResponse:
    zone = await TimezoneService.tenant_timezone_name()
    with tz_scope(zone):
        start, end = await SystemConfigService.get_event_window()
    hours = await SystemConfigService.get_tournament_hours()
    days: List[DayHours] = []
    day = start
    while day <= end:
        window = hours.get(day)
        days.append(DayHours(
            day=day,
            opens=window[0].strftime('%H:%M') if window else None,
            closes=window[1].strftime('%H:%M') if window else None,
        ))
        day += timedelta(days=1)
    return EventDatesResponse(
        starts_on=await SystemConfigService.get_date(KEY_EVENT_START_DATE),
        ends_on=await SystemConfigService.get_date(KEY_EVENT_END_DATE),
        days=days,
        timezone_label=timezone_label(zone),
    )


@router.get(
    "/dates",
    response_model=EventDatesResponse,
    summary="Get the event's dates and daily hours",
    description="When the event runs, and the hours matches can start on each of its days.\n\n" + STAFF_ONLY,
)
async def get_dates(actor: User = Depends(require_staff)):
    return await _dates()


@router.put(
    "/dates",
    response_model=EventDatesResponse,
    summary="Set the event's dates",
    description="Null leaves a bound blank, and the event then runs from its first match to its last.\n\n" + STAFF_ONLY,
)
async def set_dates(body: EventDatesUpdate, actor: User = Depends(require_staff_write)):
    if body.starts_on and body.ends_on and body.ends_on < body.starts_on:
        raise ValueError("The event can't end before it starts.")
    await SystemConfigService.set_validated(KEY_EVENT_START_DATE, body.starts_on.isoformat() if body.starts_on else '', actor)
    await SystemConfigService.set_validated(KEY_EVENT_END_DATE, body.ends_on.isoformat() if body.ends_on else '', actor)
    return await _dates()


@router.put(
    "/hours",
    response_model=EventDatesResponse,
    summary="Set the event's daily hours",
    description=(
        "Replaces every day's hours. A day left out, or with a time blank, allows any time. "
        "Fails with 400 when a day closes before it opens.\n\n" + STAFF_ONLY
    ),
)
async def set_hours(body: EventHoursUpdate, actor: User = Depends(require_staff_write)):
    blob = {
        d.day.isoformat(): {'open': d.opens, 'close': d.closes}
        for d in body.days if d.opens and d.closes
    }
    await SystemConfigService.set_validated(KEY_TOURNAMENT_HOURS, json.dumps(blob), actor)
    return await _dates()


async def _capacity() -> CapacitySettings:
    return CapacitySettings(
        max_players=await SystemConfigService.get_int(KEY_MAX_CONCURRENT_PLAYERS),
        max_stages=await SystemConfigService.get_int(KEY_MAX_CONCURRENT_STAGES),
    )


@router.get(
    "/capacity",
    response_model=CapacitySettings,
    summary="Get how much runs at once",
    description=STAFF_ONLY,
)
async def get_capacity(actor: User = Depends(require_staff)):
    return await _capacity()


@router.put(
    "/capacity",
    response_model=CapacitySettings,
    summary="Set how much runs at once",
    description="Null puts a limit back to its default.\n\n" + STAFF_ONLY,
)
async def set_capacity(body: CapacitySettings, actor: User = Depends(require_staff_write)):
    for key, value in ((KEY_MAX_CONCURRENT_PLAYERS, body.max_players), (KEY_MAX_CONCURRENT_STAGES, body.max_stages)):
        await SystemConfigService.set_validated(key, '' if value is None else str(value), actor)
    return await _capacity()


async def _timezone() -> EventTimezoneResponse:
    community = await TimezoneService.community_settings()
    own = await TimezoneService.event_override()
    current = own or community
    options = list(COMMON_TIMEZONES)
    if current['name'] not in options:
        options.insert(0, current['name'])
    return EventTimezoneResponse(
        follows_community=own is None,
        mode=current['mode'],
        name=current['name'],
        community_mode=community['mode'],
        community_name=community['name'],
        options=options,
    )


@router.get(
    "/timezone",
    response_model=EventTimezoneResponse,
    summary="Get the event's clock",
    description="Whether the event follows the community's clock or keeps one of its own.\n\n" + STAFF_ONLY,
)
async def get_timezone(actor: User = Depends(require_staff)):
    return await _timezone()


@router.put(
    "/timezone",
    response_model=EventTimezoneResponse,
    summary="Set the event's clock",
    description="Fails with 400 on an unknown mode or zone.\n\n" + STAFF_ONLY,
)
async def set_timezone(body: EventTimezoneUpdate, actor: User = Depends(require_staff_write)):
    if body.follows_community:
        await TimezoneService.set_event_override(actor, None)
    else:
        if not body.mode:
            raise ValueError("Pick how this event's times are shown.")
        await TimezoneService.set_event_override(actor, body.mode, body.name)
    return await _timezone()


@router.get(
    "/crew",
    response_model=CrewSettings,
    summary="Get whether matches take broadcasters",
    description=STAFF_ONLY,
)
async def get_crew(actor: User = Depends(require_staff)):
    return CrewSettings(broadcasters=await SystemConfigService.get_bool(KEY_BROADCASTERS))


@router.put(
    "/crew",
    response_model=CrewSettings,
    summary="Set whether matches take broadcasters",
    description=STAFF_ONLY,
)
async def set_crew(body: CrewSettings, actor: User = Depends(require_staff_write)):
    await SystemConfigService.set_validated(KEY_BROADCASTERS, 'true' if body.broadcasters else 'false', actor)
    return CrewSettings(broadcasters=await SystemConfigService.get_bool(KEY_BROADCASTERS))


async def _tiers() -> RewardTiersResponse:
    return RewardTiersResponse(
        text=await SystemConfigService.get_raw(KEY_VOLUNTEER_COMP_TIERS) or '',
        tiers=await SystemConfigService.get_volunteer_comp_tiers(),
    )


@router.get(
    "/reward-tiers",
    response_model=RewardTiersResponse,
    summary="Get the volunteer reward tiers",
    description="The hour totals that earn a volunteer something at this event.\n\n" + STAFF_ONLY,
    dependencies=[Depends(require_feature(FeatureFlag.VOLUNTEERS))],
)
async def get_reward_tiers(actor: User = Depends(require_staff)):
    return await _tiers()


@router.put(
    "/reward-tiers",
    response_model=RewardTiersResponse,
    summary="Set the volunteer reward tiers",
    description="Fails with 400 on anything but non-negative numbers.\n\n" + STAFF_ONLY,
    dependencies=[Depends(require_feature(FeatureFlag.VOLUNTEERS))],
)
async def set_reward_tiers(body: RewardTiersUpdate, actor: User = Depends(require_staff_write)):
    await SystemConfigService.set_validated(KEY_VOLUNTEER_COMP_TIERS, body.text, actor)
    return await _tiers()


async def _station_format() -> StationFormatResponse:
    return StationFormatResponse(
        format=await SystemConfigService.get_station_format(),
        options=[StationFormatOption(value=f, label=label) for f, label in STATION_FORMAT_LABELS.items()],
    )


@router.get(
    "/station-format",
    response_model=StationFormatResponse,
    summary="Get what a proctor may type as a station",
    description=STAFF_ONLY,
)
async def get_station_format(actor: User = Depends(require_staff)):
    return await _station_format()


@router.put(
    "/station-format",
    response_model=StationFormatResponse,
    summary="Set what a proctor may type as a station",
    description=STAFF_ONLY,
)
async def set_station_format(body: StationFormatSettings, actor: User = Depends(require_staff_write)):
    await SystemConfigService.set_validated(KEY_STATION_FORMAT, body.format.value, actor)
    return await _station_format()


def _station(s: Station) -> StationResponse:
    return StationResponse(
        id=s.id, name=s.name, section=s.section, side=s.side, position=s.position,
        sort_order=s.sort_order, is_active=s.is_active,
    )


@router.get(
    "/stations",
    response_model=List[StationResponse],
    summary="List the venue's stations",
    description="In display order, retired ones included.\n\n" + STAFF_ONLY,
)
async def list_stations(actor: User = Depends(require_staff)):
    return [_station(s) for s in await StationService().list_stations()]


@router.post(
    "/stations",
    response_model=StationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a station",
    description="Fails with 400 when the name is blank or taken.\n\n" + STAFF_ONLY,
)
async def create_station(body: StationCreate, actor: User = Depends(require_staff_write)):
    station = await StationService().create_station(
        body.name, actor, section=body.section, side=body.side, position=body.position, sort_order=body.sort_order,
    )
    return _station(station)


@router.patch(
    "/stations/{station_id}",
    response_model=StationResponse,
    summary="Change a station",
    description="Fields left out stay as they are; `side` or `position` sent as null clears it.\n\n" + STAFF_ONLY,
)
async def update_station(station_id: int, body: StationUpdate, actor: User = Depends(require_staff_write)):
    sent = body.model_fields_set
    station = await StationService().update_station(
        station_id, actor,
        name=body.name, section=body.section, side=body.side, position=body.position,
        sort_order=body.sort_order, is_active=body.is_active,
        clear_side='side' in sent and body.side is None,
        clear_position='position' in sent and body.position is None,
    )
    return _station(station)


@router.delete(
    "/stations/{station_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a station",
    description="Past matches keep the station's name. Turning it off keeps it for later instead.\n\n" + STAFF_ONLY,
)
async def delete_station(station_id: int, actor: User = Depends(require_staff_write)):
    await StationService().delete_station(station_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _screen(t: RoomToken) -> RoomScreenResponse:
    issuer = t.created_by
    return RoomScreenResponse(
        id=t.id, label=t.label, revoked=t.revoked_at is not None, last_used_at=t.last_used_at,
        issued_by=issuer.preferred_name if issuer else None,
    )


@router.get(
    "/room-screens",
    response_model=List[RoomScreenResponse],
    summary="List the room screens",
    description="Machines in the room showing the seeds board without signing in, revoked ones last.\n\n" + STAFF_ONLY,
)
async def list_room_screens(actor: User = Depends(require_staff)):
    return [_screen(t) for t in await RoomTokenService().list_tokens(actor)]


@router.post(
    "/room-screens",
    response_model=RoomScreenIssued,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a room screen",
    description="Returns the machine's URL once; only its hash is stored.\n\n" + STAFF_ONLY,
)
async def issue_room_screen(body: RoomScreenCreate, actor: User = Depends(require_staff_write)):
    token, raw = await RoomTokenService().issue(actor, body.label)
    await token.fetch_related('created_by')
    tenant = await TenantService.get_by_id(require_tenant_id())
    return RoomScreenIssued(screen=_screen(token), url=tenant_url(tenant, room_seeds_path(raw)))


@router.delete(
    "/room-screens/{screen_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a room screen",
    description="The machine gets a 404 on its next refresh, and the URL can't be brought back.\n\n" + STAFF_ONLY,
)
async def revoke_room_screen(screen_id: int, actor: User = Depends(require_staff_write)):
    await RoomTokenService().revoke(actor, screen_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
