"""A community's own settings, one group per pane of Admin → Settings.

The typed counterpart of ``/api/config``: each group reads and writes the
settings its pane shows, through the same services the website uses, so the
validation and audit rows are the same whichever surface saved them. Staff
only. Integrations and Discord live under their own routers.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Request, Response, status

from api.dependencies import ServiceErrorRoute, require_feature, require_staff, require_staff_write
from api.schemas.community_settings import (
    AppearanceResponse,
    BlockAppResponse,
    ConnectedApp,
    ContrastRequest,
    ContrastResponse,
    FeatureRow,
    FeaturesResponse,
    FeatureUpdate,
    JoiningSettingsResponse,
    JoiningSettingsUpdate,
    JoinModeOption,
    MeetupSettings,
    ProfileReminder,
    ProfileRemindersResponse,
    ProfileRemindersUpdate,
    ThemeColours,
    ThemeColoursUpdate,
    ThemePreset,
    TimezoneSettingsResponse,
    TimezoneSettingsUpdate,
    VolunteerSettings,
    WatchAudience,
    WatchSettings,
    WatchSettingsResponse,
)
from application.services import (
    FeatureFlagService,
    OAuthGrantService,
    ProfileNudgeService,
    SystemConfigService,
    TenantLogoService,
    TenantMembershipService,
    TenantService,
    TenantThemeService,
    TimezoneService,
)
from application.services.join_mode_copy import join_mode_help, offered_join_modes
from application.services.profile_nudge_service import CHECKS, MAX_NOTE_LENGTH
from application.services.system_config_service import (
    DEFAULT_WATCH_AUTO_THRESHOLD,
    KEY_COMMUNITY_EVENT_REMINDER_LEAD_MINUTES,
    KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL,
    KEY_VOLUNTEER_REMINDER_LEAD_MINUTES,
    KEY_WATCH_AUTO_STATIC,
    KEY_WATCH_AUTO_THRESHOLD,
    KEY_WATCH_MODE,
    KEY_WATCH_STATIC_SIGNED_OUT,
)
from application.services.tenant_logo_service import MAX_UPLOAD_BYTES
from application.services.tenant_membership_service import JoinMode
from application.services.tenant_theme_service import DEFAULT_THEME
from application.services.timezone_service import COMMON_TIMEZONES
from application.tenant_context import require_tenant_id
from application.utils import live_audience
from models import FeatureFlag, User

router = APIRouter(prefix="/settings", tags=["Community settings"], route_class=ServiceErrorRoute)

STAFF_ONLY = "**Who can call this:** Staff."


def _colours(palette: dict) -> ThemeColours:
    return ThemeColours(**{key: palette[key] for key in ThemeColours.model_fields})


async def _appearance() -> AppearanceResponse:
    palette = await TenantThemeService.get_current_theme()
    checked = {k: v for k, v in palette.items() if k in ThemeColours.model_fields}
    if palette.get('logo'):
        checked.pop('header', None)
    return AppearanceResponse(
        colours=_colours(palette),
        defaults=_colours(DEFAULT_THEME),
        presets=[ThemePreset(name=name, colours=_colours(c)) for name, c in TenantThemeService.list_presets().items()],
        issues=TenantThemeService.contrast_issues(checked),
        logo_url=palette.get('logo'),
    )


@router.get(
    "/appearance",
    response_model=AppearanceResponse,
    summary="Get the community's colours and logo",
    description="The brand colours in use (defaults where none are set), the presets, contrast warnings and the logo.\n\n" + STAFF_ONLY,
)
async def get_appearance(actor: User = Depends(require_staff)):
    return await _appearance()


@router.put(
    "/appearance",
    response_model=AppearanceResponse,
    summary="Set the community's colours",
    description=(
        "Saves all four colours at once; a blank one goes back to its default. Fails with 400 on a value "
        "that isn't a 6-digit hex. Contrast warnings never block saving.\n\n" + STAFF_ONLY
    ),
)
async def set_appearance(body: ThemeColoursUpdate, actor: User = Depends(require_staff_write)):
    await TenantThemeService.set_theme(actor, body.model_dump())
    return await _appearance()


@router.post(
    "/appearance/contrast",
    response_model=ContrastResponse,
    summary="Check colours for contrast",
    description="Contrast warnings for colours that aren't saved yet, for warnings while someone edits. Saves nothing.\n\n" + STAFF_ONLY,
)
async def check_contrast(body: ContrastRequest, actor: User = Depends(require_staff_write)):
    return ContrastResponse(issues=TenantThemeService.contrast_issues(body.colours))


@router.put(
    "/logo",
    response_model=AppearanceResponse,
    summary="Upload the community's logo",
    description=(
        "The request body is the image itself: an SVG, or a square PNG, JPEG or WebP at least 64×64, up to "
        "1 MB. An SVG is sanitised and a bitmap re-encoded. Fails with 400 on anything else.\n\n" + STAFF_ONLY
    ),
)
async def upload_logo(request: Request, actor: User = Depends(require_staff_write)):
    data = await request.body()
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError('Logos can be up to 1 MB. Try exporting a smaller file.')
    await TenantLogoService.set_logo(actor, data)
    return await _appearance()


@router.delete(
    "/logo",
    response_model=AppearanceResponse,
    summary="Remove the community's logo",
    description="The coloured brand mark shows in its place.\n\n" + STAFF_ONLY,
)
async def remove_logo(actor: User = Depends(require_staff_write)):
    await TenantLogoService.remove_logo(actor)
    return await _appearance()


async def _timezone() -> TimezoneSettingsResponse:
    settings = await TimezoneService.community_settings()
    options = list(COMMON_TIMEZONES)
    if settings['name'] not in options:
        options.insert(0, settings['name'])
    return TimezoneSettingsResponse(mode=settings['mode'], name=settings['name'], options=options)


@router.get(
    "/timezone",
    response_model=TimezoneSettingsResponse,
    summary="Get the community's clock",
    description="Whether everyone sees one zone or their own, and the zone used.\n\n" + STAFF_ONLY,
)
async def get_timezone(actor: User = Depends(require_staff)):
    return await _timezone()


@router.put(
    "/timezone",
    response_model=TimezoneSettingsResponse,
    summary="Set the community's clock",
    description="Fails with 400 on an unknown mode or zone.\n\n" + STAFF_ONLY,
)
async def set_timezone(body: TimezoneSettingsUpdate, actor: User = Depends(require_staff_write)):
    await TimezoneService.set_settings(actor, body.mode, body.name)
    return await _timezone()


async def _joining() -> JoiningSettingsResponse:
    mode = await TenantMembershipService.get_join_mode()
    tenant = await TenantService.get_by_id(require_tenant_id())
    linked = bool(tenant and tenant.discord_guild_id)
    return JoiningSettingsResponse(
        join_mode=mode.value,
        modes=[
            JoinModeOption(value=m.value, label=label, help=join_mode_help(m, linked))
            for m, label in offered_join_modes(mode, linked).items()
        ],
        guild_linked=linked,
        discord_invite_url=await SystemConfigService.get_discord_invite_url() or '',
    )


@router.get(
    "/joining",
    response_model=JoiningSettingsResponse,
    summary="Get who can join",
    description="The join mode, the modes on offer and the Discord invite link the join page shows.\n\n" + STAFF_ONLY,
)
async def get_joining(actor: User = Depends(require_staff)):
    return await _joining()


@router.put(
    "/joining",
    response_model=JoiningSettingsResponse,
    summary="Set who can join",
    description="Fails with 400 on an unknown mode or a link that isn't a Discord invite.\n\n" + STAFF_ONLY,
)
async def set_joining(body: JoiningSettingsUpdate, actor: User = Depends(require_staff_write)):
    try:
        mode = JoinMode(body.join_mode)
    except ValueError:
        raise ValueError(f"'{body.join_mode}' isn't a join mode.") from None
    invite = SystemConfigService.normalize_discord_invite_url(body.discord_invite_url)
    await TenantMembershipService.set_join_mode(actor, mode)
    await SystemConfigService.set_discord_invite_url(invite, actor)
    return await _joining()


async def _reminders() -> ProfileRemindersResponse:
    service = ProfileNudgeService()
    rules = {r['check']: r for r in await service.get_rules()}
    unavailable = await service.unavailable_reasons()
    reminders: List[ProfileReminder] = []
    for spec in CHECKS:
        rule = rules.get(spec.check)
        reminders.append(ProfileReminder(
            check=spec.check.value, label=spec.label, prompt=spec.prompt, enabled=rule is not None,
            dismissible=rule['dismissible'] if rule else True, note=(rule or {}).get('note') or '',
            unavailable=unavailable.get(spec.check),
        ))
    return ProfileRemindersResponse(reminders=reminders, max_note_length=MAX_NOTE_LENGTH)


@router.get(
    "/profile-reminders",
    response_model=ProfileRemindersResponse,
    summary="Get the profile reminders",
    description="Every reminder members can be shown, with whether it's on and why it can't apply here.\n\n" + STAFF_ONLY,
)
async def get_profile_reminders(actor: User = Depends(require_staff)):
    return await _reminders()


@router.put(
    "/profile-reminders",
    response_model=ProfileRemindersResponse,
    summary="Set the profile reminders",
    description="Replaces the reminders that are on. Dismissals are kept, so switching one back on doesn't nag again.\n\n" + STAFF_ONLY,
)
async def set_profile_reminders(body: ProfileRemindersUpdate, actor: User = Depends(require_staff_write)):
    await ProfileNudgeService().set_rules(actor, [r.model_dump() for r in body.rules])
    return await _reminders()


async def _watch(tenant_id: int) -> WatchSettingsResponse:
    snap = live_audience.snapshot(tenant_id)
    return WatchSettingsResponse(
        mode=(await SystemConfigService.get_raw(KEY_WATCH_MODE)) or 'live',
        static_signed_out=await SystemConfigService.get_bool(KEY_WATCH_STATIC_SIGNED_OUT, default=False),
        auto_static=await SystemConfigService.get_bool(KEY_WATCH_AUTO_STATIC, default=True),
        threshold=await SystemConfigService.get_int(KEY_WATCH_AUTO_THRESHOLD, DEFAULT_WATCH_AUTO_THRESHOLD)
        or DEFAULT_WATCH_AUTO_THRESHOLD,
        audience=WatchAudience(
            live_viewers=snap.live_viewers, static_readers=snap.static_readers,
            arrivals_last_minute=snap.arrivals_last_minute, admissions_per_minute=live_audience.ADMISSIONS_PER_MINUTE,
            peak=snap.peak, peak_at=snap.peak_at, tripped=snap.tripped, tripped_at=snap.tripped_at,
        ),
    )


@router.get(
    "/watch",
    response_model=WatchSettingsResponse,
    summary="Get the Watch page settings",
    description="Live or static, the rush switch, and who is on the page right now.\n\n" + STAFF_ONLY,
)
async def get_watch(actor: User = Depends(require_staff)):
    return await _watch(require_tenant_id())


@router.put(
    "/watch",
    response_model=WatchSettingsResponse,
    summary="Set the Watch page settings",
    description="Fails with 400 on an unknown mode or a threshold under 1.\n\n" + STAFF_ONLY,
)
async def set_watch(body: WatchSettings, actor: User = Depends(require_staff_write)):
    await SystemConfigService.set_validated(KEY_WATCH_MODE, body.mode, actor)
    await SystemConfigService.set_validated(KEY_WATCH_AUTO_STATIC, 'true' if body.auto_static else 'false', actor)
    await SystemConfigService.set_validated(KEY_WATCH_AUTO_THRESHOLD, str(body.threshold), actor)
    await SystemConfigService.set_validated(KEY_WATCH_STATIC_SIGNED_OUT, 'true' if body.static_signed_out else 'false', actor)
    return await _watch(require_tenant_id())


@router.post(
    "/watch/back-to-live",
    response_model=WatchSettingsResponse,
    summary="End a rush switch",
    description="Puts the Watch page back to live now, after it switched itself to static.\n\n" + STAFF_ONLY,
)
async def watch_back_to_live(actor: User = Depends(require_staff_write)):
    tenant_id = require_tenant_id()
    live_audience.clear_trip(tenant_id)
    return await _watch(tenant_id)


def _lead(value: Optional[int]) -> str:
    return '' if value is None else str(value)


@router.get(
    "/volunteers",
    response_model=VolunteerSettings,
    summary="Get volunteer settings",
    description="When volunteers hear about their next shift.\n\n" + STAFF_ONLY,
    dependencies=[Depends(require_feature(FeatureFlag.VOLUNTEERS))],
)
async def get_volunteer_settings(actor: User = Depends(require_staff)):
    return VolunteerSettings(reminder_lead_minutes=await SystemConfigService.get_int(KEY_VOLUNTEER_REMINDER_LEAD_MINUTES))


@router.put(
    "/volunteers",
    response_model=VolunteerSettings,
    summary="Set volunteer settings",
    description="Fails with 400 on a lead under 1 minute.\n\n" + STAFF_ONLY,
    dependencies=[Depends(require_feature(FeatureFlag.VOLUNTEERS))],
)
async def set_volunteer_settings(body: VolunteerSettings, actor: User = Depends(require_staff_write)):
    await SystemConfigService.set_validated(KEY_VOLUNTEER_REMINDER_LEAD_MINUTES, _lead(body.reminder_lead_minutes), actor)
    return VolunteerSettings(reminder_lead_minutes=await SystemConfigService.get_int(KEY_VOLUNTEER_REMINDER_LEAD_MINUTES))


async def _meetups() -> MeetupSettings:
    return MeetupSettings(
        require_approval=await SystemConfigService.community_events_require_approval(),
        reminder_lead_minutes=await SystemConfigService.get_int(KEY_COMMUNITY_EVENT_REMINDER_LEAD_MINUTES),
    )


@router.get(
    "/meetups",
    response_model=MeetupSettings,
    summary="Get meetup settings",
    description="Whether staff approve meetups first, and how early subscribers get a reminder.\n\n" + STAFF_ONLY,
    dependencies=[Depends(require_feature(FeatureFlag.COMMUNITY_EVENTS))],
)
async def get_meetup_settings(actor: User = Depends(require_staff)):
    return await _meetups()


@router.put(
    "/meetups",
    response_model=MeetupSettings,
    summary="Set meetup settings",
    description="Fails with 400 on a lead under 1 minute.\n\n" + STAFF_ONLY,
    dependencies=[Depends(require_feature(FeatureFlag.COMMUNITY_EVENTS))],
)
async def set_meetup_settings(body: MeetupSettings, actor: User = Depends(require_staff_write)):
    await SystemConfigService.set_validated(
        KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL, 'true' if body.require_approval else 'false', actor,
    )
    await SystemConfigService.set_validated(KEY_COMMUNITY_EVENT_REMINDER_LEAD_MINUTES, _lead(body.reminder_lead_minutes), actor)
    return await _meetups()


async def _features(actor: User) -> FeaturesResponse:
    service = FeatureFlagService()
    rows = await service.list_for_tenant_admin(actor)
    return FeaturesResponse(
        tier=await service.current_tenant_group_name(),
        features=[
            FeatureRow(
                flag=r['flag'], label=r['label'], description=r['description'], category=r['category'],
                available=r['available'], enabled=r['enabled'],
            )
            for r in rows
        ],
    )


@router.get(
    "/features",
    response_model=FeaturesResponse,
    summary="List the community's features",
    description="Every feature, whether this community's tier makes it available, and whether staff have it on.\n\n" + STAFF_ONLY,
)
async def get_features(actor: User = Depends(require_staff)):
    return await _features(actor)


@router.put(
    "/features/{flag}",
    response_model=FeaturesResponse,
    summary="Turn a feature on or off",
    description="Fails with 400 on an unknown feature or one the tier doesn't make available.\n\n" + STAFF_ONLY,
)
async def set_feature(flag: str, body: FeatureUpdate, actor: User = Depends(require_staff_write)):
    try:
        feature = FeatureFlag(flag)
    except ValueError:
        raise ValueError(f"'{flag}' isn't a feature.") from None
    await FeatureFlagService().set_tenant_enabled(actor, feature, body.enabled)
    return await _features(actor)


@router.get(
    "/apps",
    response_model=List[ConnectedApp],
    summary="List connected apps",
    description="Outside apps members of this community have connected, with how many members, never who.\n\n" + STAFF_ONLY,
    dependencies=[Depends(require_feature(FeatureFlag.THIRD_PARTY_APPS))],
)
async def list_connected_apps(actor: User = Depends(require_staff)):
    return [ConnectedApp(**vars(a)) for a in await OAuthGrantService().list_community_apps(actor)]


@router.post(
    "/apps/{app_id}/block",
    response_model=BlockAppResponse,
    summary="Block an app here",
    description=(
        "Disconnects everyone in this community from the app and stops new connections here. Other "
        "communities aren't affected.\n\n" + STAFF_ONLY
    ),
    dependencies=[Depends(require_feature(FeatureFlag.THIRD_PARTY_APPS))],
)
async def block_app(app_id: int, actor: User = Depends(require_staff_write)):
    return BlockAppResponse(disconnected=await OAuthGrantService().block_app_here(actor, app_id))


@router.delete(
    "/apps/{app_id}/block",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unblock an app here",
    description="Members can connect it again.\n\n" + STAFF_ONLY,
    dependencies=[Depends(require_feature(FeatureFlag.THIRD_PARTY_APPS))],
)
async def unblock_app(app_id: int, actor: User = Depends(require_staff_write)):
    await OAuthGrantService().unblock_app_here(actor, app_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
