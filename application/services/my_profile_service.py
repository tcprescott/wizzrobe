"""A member's own profile, as the app's Me screen draws it.

``overview`` is the settings list: one row per section with a one-line
summary, and only the sections this community has switched on. The other
methods cover the reads the sections need that no other service shapes yet
(connected accounts, tournaments to follow, staff alerts). Writes go to the
services that own each setting.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, List, Optional

from application.services.accommodation_service import AccommodationService
from application.services.api_token_service import ApiTokenService
from application.services.challonge_service import ChallongeService
from application.services.emergency_contact_service import EmergencyContactService
from application.services.equipment_service import EquipmentService
from application.services.feature_flag_service import FeatureFlagService
from application.services.feedback_service import FeedbackService
from application.services.notification_channel_service import email_available, telegram_available
from application.services.oauth_app_service import OAuthAppService
from application.services.personal_webhook_service import PersonalWebhookService
from application.services.player_availability_service import PlayerAvailabilityService
from application.services.racetime_service import RacetimeService
from application.services.staff_alert_service import StaffAlertService
from application.services.startgg_link_service import StartggLinkService
from application.services.timezone_service import MODE_PINNED, TimezoneService
from application.services.tournament_notification_service import TournamentNotificationService
from application.services.twitch_service import TwitchService
from application.services.user_service import UserService
from application.services.web_push_service import WebPushService
from application.timezone_context import is_valid_timezone
from application.utils.discord_avatar import avatar_url
from models import AccommodationStatus, FeatureFlag, User


@dataclass(frozen=True)
class _Provider:
    key: str
    title: str
    description: str
    link_path: str
    id_attr: str
    name_attr: str
    linked_at_attr: str
    service: Callable[[], Any]
    flag: Optional[FeatureFlag] = None
    sign_in_first_url: Optional[str] = None
    unlink_warning: Optional[str] = None


PROVIDERS: tuple[_Provider, ...] = (
    _Provider(
        'challonge', 'Challonge',
        'Link your Challonge account so we can find your bracket matches for scheduling.',
        '/challonge/link', 'challonge_user_id', 'challonge_username', 'challonge_linked_at',
        ChallongeService, FeatureFlag.CHALLONGE,
        sign_in_first_url='https://challonge.com/user_session/new',
        unlink_warning=(
            "We'll stop matching you to Challonge bracket matches until you link again. "
            'Matches already booked stay booked.'
        ),
    ),
    _Provider(
        'startgg', 'start.gg',
        'Link your start.gg account so we can find your bracket matches for scheduling.',
        '/startgg/link', 'startgg_user_id', 'startgg_username', 'startgg_linked_at',
        StartggLinkService, FeatureFlag.STARTGG,
        unlink_warning=(
            "We'll stop matching you to start.gg bracket matches until you link again. "
            'Matches already booked stay booked.'
        ),
    ),
    _Provider(
        'twitch', 'Twitch',
        'Link your Twitch account so restreams and overlays can show your channel.',
        '/twitch/link', 'twitch_user_id', 'twitch_username', 'twitch_linked_at', TwitchService,
    ),
    _Provider(
        'racetime', 'racetime.gg',
        'Link your racetime.gg account so we can attribute your race results and check '
        'auto-open eligibility.',
        '/racetime/link', 'racetime_user_id', 'racetime_username', 'racetime_linked_at',
        RacetimeService,
    ),
)
PROVIDERS_BY_KEY = {p.key: p for p in PROVIDERS}


@dataclass
class ProfileSection:
    key: str
    title: str
    summary: str


@dataclass
class ProfileOverview:
    display_name: Optional[str]
    preferred_name: str
    pronouns: Optional[str]
    discord_username: Optional[str]
    avatar_url: Optional[str]
    timezone: Optional[str]
    hour_cycle: Optional[str]
    community_zone: str
    zone_pinned: bool
    matcherino_username: Optional[str]
    payouts: bool
    dm_notifications: bool
    sections: List[ProfileSection] = field(default_factory=list)


@dataclass
class ConnectedAccount:
    key: str
    title: str
    description: str
    linked: bool
    username: Optional[str]
    linked_at: Optional[datetime]
    link_path: str
    sign_in_first_url: Optional[str]
    unlink_warning: Optional[str]


@dataclass
class FollowableTournament:
    id: int
    name: str
    playing: bool
    level: str


@dataclass
class StaffAlertTournament:
    id: int
    name: str
    on: bool


@dataclass
class StaffAlerts:
    duty_position: Optional[str]
    on_duty_now: bool
    next_duty_start: Optional[datetime]
    tournaments: List[StaffAlertTournament]


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f'{n} {one if n == 1 else (many or one + "s")}'


ACCOMMODATION_LABELS = {
    AccommodationStatus.NEW: 'Asked for, not seen yet',
    AccommodationStatus.ACKNOWLEDGED: 'Staff have seen it',
    AccommodationStatus.ARRANGED: 'Arranged',
}


class MyProfileService:
    """Reads for the Me screen. Every method is about the caller only."""

    @staticmethod
    def accounts_for(user: User, live: set) -> List[ConnectedAccount]:
        out: List[ConnectedAccount] = []
        for p in PROVIDERS:
            if p.flag is not None and p.flag not in live:
                continue
            if not p.service().is_configured():
                continue
            linked_id = getattr(user, p.id_attr, None)
            out.append(ConnectedAccount(
                key=p.key, title=p.title, description=p.description, linked=bool(linked_id),
                username=(getattr(user, p.name_attr, None) or (str(linked_id) if linked_id else None)),
                linked_at=getattr(user, p.linked_at_attr, None), link_path=p.link_path,
                sign_in_first_url=p.sign_in_first_url, unlink_warning=p.unlink_warning,
            ))
        return out

    async def accounts(self, user: User) -> List[ConnectedAccount]:
        return self.accounts_for(user, await FeatureFlagService().enabled_flags())

    @staticmethod
    async def unlink_account(user: User, key: str) -> None:
        provider = PROVIDERS_BY_KEY.get(key)
        if provider is None:
            raise ValueError('That account type is not one you can link here.')
        await provider.service().unlink_player(user, actor=user)

    @staticmethod
    async def followable_tournaments(user: User) -> List[FollowableTournament]:
        prefs = {
            p.tournament_id: p.match_notifications  # type: ignore[attr-defined]
            for p in await TournamentNotificationService().get_user_preferences(user)
        }
        playing = {
            tp.tournament_id  # type: ignore[attr-defined]
            for tp in await UserService().get_user_tournament_registrations(user)
        }
        return [
            FollowableTournament(id=t.id, name=t.name, playing=t.id in playing, level=prefs.get(t.id, 'none'))
            for t in await TournamentNotificationService().get_active_tournaments()
        ]

    @staticmethod
    async def staff_alerts(user: User) -> Optional[StaffAlerts]:
        """``None`` when the person neither runs a tournament nor holds an alert shift."""
        duty = await StaffAlertService.duty_summary(user)
        tournaments = await StaffAlertService.admin_tournaments(user)
        if duty is None and not tournaments:
            return None
        return StaffAlerts(
            duty_position=duty.position_name if duty else None,
            on_duty_now=bool(duty and duty.on_now),
            next_duty_start=duty.next_start if duty else None,
            tournaments=[StaffAlertTournament(id=t.id, name=t.name, on=on) for t, on in tournaments],
        )

    async def overview(self, user: User) -> ProfileOverview:
        live = await FeatureFlagService().enabled_flags()
        settings = await TimezoneService.get_settings()
        pinned = settings.get('mode') == MODE_PINNED
        payouts = FeatureFlag.PAYOUTS in live
        tz = user.timezone if is_valid_timezone(user.timezone) else None
        out = ProfileOverview(
            display_name=user.display_name, preferred_name=user.preferred_name,
            pronouns=user.pronouns,
            discord_username=user.username if user.discord_id else None,
            avatar_url=avatar_url(user.discord_id, user.discord_avatar) if user.discord_id else None,
            timezone=tz, hour_cycle=user.hour_cycle,
            community_zone=settings.get('name') or 'UTC', zone_pinned=pinned,
            matcherino_username=user.matcherino_username if payouts else None, payouts=payouts,
            dm_notifications=bool(user.dm_notifications),
        )
        add = out.sections.append

        about = [user.preferred_name]
        if tz and not pinned:
            about.append(tz.replace('_', ' '))
        add(ProfileSection('about', 'About you', ' · '.join(about)))

        channels: List[str] = []
        if user.discord_id and user.dm_notifications:
            channels.append('Discord DMs')
        if WebPushService.is_configured():
            devices = len(await WebPushService().list_subscriptions(user))
            if devices:
                channels.append(_plural(devices, 'device'))
        if FeatureFlag.NOTIFICATION_CHANNELS in live:
            if email_available() and user.email and user.email_notifications:
                channels.append('email')
            if telegram_available() and user.telegram_chat_id and user.telegram_notifications:
                channels.append('Telegram')
        add(ProfileSection(
            'notifications', 'Notifications',
            ', '.join(channels) if channels else "Nothing turned on, so you won't hear from us",
        ))

        blocked = len(await PlayerAvailabilityService().availability_for(user))
        add(ProfileSection(
            'availability', 'Availability',
            _plural(blocked, 'blocked time') if blocked else 'Free all event',
        ))

        accounts = self.accounts_for(user, live)
        if accounts:
            linked = [a.title for a in accounts if a.linked]
            add(ProfileSection('accounts', 'Connected accounts', ', '.join(linked) if linked else 'None linked'))

        alerts = await self.staff_alerts(user)
        if alerts is not None:
            if alerts.on_duty_now and alerts.duty_position:
                summary = f'On duty now: {alerts.duty_position}'
            else:
                on = sum(1 for t in alerts.tournaments if t.on)
                summary = f'{on} of {_plural(len(alerts.tournaments), "tournament")}' if alerts.tournaments \
                    else f'Duty shifts: {alerts.duty_position}'
            add(ProfileSection('staff-alerts', 'Staff alerts', summary))

        if FeatureFlag.EQUIPMENT in live:
            loans = len(await EquipmentService().my_checkouts(user))
            add(ProfileSection('equipment', 'Equipment', f'{loans} checked out' if loans else 'Nothing checked out'))

        if FeatureFlag.ADA_ACCOMMODATIONS in live:
            request = await AccommodationService().get_mine(user)
            label = ACCOMMODATION_LABELS.get(request.status) if request else None
            add(ProfileSection('accommodation', 'Accommodations', label or 'None asked for'))

        if FeatureFlag.EMERGENCY_CONTACTS in live:
            contact = await EmergencyContactService().get_mine(user)
            add(ProfileSection('emergency-contact', 'Emergency contact', contact.name if contact else 'Not added'))

        sent = len(await FeedbackService().list_mine(user, limit=100))
        add(ProfileSection('feedback', 'My feedback', _plural(sent, 'note') + ' sent' if sent else 'None sent'))

        if FeatureFlag.PERSONAL_WEBHOOKS in live:
            hooks = len(await PersonalWebhookService().list_mine(user))
            add(ProfileSection('webhooks', 'Webhooks', _plural(hooks, 'webhook') if hooks else 'None'))

        if FeatureFlag.THIRD_PARTY_APPS in live:
            apps = len(await OAuthAppService().list_my_apps(user))
            add(ProfileSection('apps', 'Developer apps', _plural(apps, 'app') if apps else 'None'))

        tokens = len(await ApiTokenService().list_tokens(user))
        add(ProfileSection('tokens', 'API tokens', _plural(tokens, 'token') if tokens else 'None'))
        return out


__all__ = [
    'ConnectedAccount',
    'FollowableTournament',
    'MyProfileService',
    'ProfileOverview',
    'ProfileSection',
    'StaffAlerts',
]
