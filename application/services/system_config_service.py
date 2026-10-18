"""
SystemConfig Service - Business Logic Layer

Typed accessors over the SystemConfiguration key/value table.
"""

import json
import re
from datetime import date, time
from typing import Dict, List, Optional, Tuple

from application.repositories._save import save_fields
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import require_tenant_id
from application.utils.timezone import to_local, today_local
from models import Match, Stage, StationFormat, SystemConfiguration, Tournament, User

KEY_EVENT_START_DATE = 'event_start_date'
KEY_EVENT_END_DATE = 'event_end_date'
KEY_MAX_CONCURRENT_PLAYERS = 'max_concurrent_players'
KEY_MAX_CONCURRENT_STAGES = 'max_concurrent_stages'
KEY_VOLUNTEER_REMINDER_LEAD_MINUTES = 'volunteer_reminder_lead_minutes'
KEY_VOLUNTEER_COMP_TIERS = 'volunteer_comp_tiers'
KEY_COMMUNITY_EVENT_REMINDER_LEAD_MINUTES = 'community_event_reminder_lead_minutes'
# Whether a member's community event waits for staff before it's listed.
KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL = 'community_event_require_approval'
KEY_TOURNAMENT_HOURS = 'tournament_hours_by_date'
KEY_STATION_FORMAT = 'station_format'
# Whether anyone in the community's linked Discord server gets in at the join
# door without asking. Default off: a server is often wider than the community
# (a shared server, a public one), so opening the door to all of it is a staff
# decision, like the preview above.
KEY_DISCORD_AUTO_JOIN = 'discord_auto_join'
# Whether non-members may file a join request at the door. Default on (read with
# ``default=True``): turning it off closes that way in without touching Discord
# auto-join, so a community can admit its server and nobody else.
KEY_JOIN_REQUESTS = 'join_requests_enabled'
# Whether anyone who signs in becomes a member without asking — no server, no
# staff decision. Default off; set through ``TenantMembershipService.set_join_mode``
# so it and the two keys above always describe one coherent rule.
KEY_OPEN_MEMBERSHIP = 'open_membership'
# An invite to the linked server, offered on the join door. Typed in by staff:
# the bot never mints one.
KEY_DISCORD_INVITE_URL = 'discord_invite_url'
# The Watch page (/live): 'live' (a NiceGUI page that updates itself) or
# 'static' (one cached render shared by every reader). Default live.
KEY_WATCH_MODE = 'watch_page_mode'
WATCH_MODES = ('live', 'static')
# Whether a rush of arrivals switches the page to static on its own (default
# on), and how many arrivals in a minute count as a rush.
KEY_WATCH_AUTO_STATIC = 'watch_page_auto_static'
KEY_WATCH_AUTO_THRESHOLD = 'watch_page_auto_threshold'
DEFAULT_WATCH_AUTO_THRESHOLD = 300

_DISCORD_INVITE_RE = re.compile(
    r'^(?:https?://)?(?:www\.)?(?:discord\.gg|(?:discord|discordapp)\.com/invite)/([A-Za-z0-9-]{2,64})/?$'
)


class SystemConfigService:
    """Typed get/set for SystemConfiguration entries used by reports."""

    @staticmethod
    async def get_raw(key: str) -> Optional[str]:
        # SystemConfiguration is per-tenant (unique on (tenant, name)).
        config = await SystemConfiguration.get_or_none(name=key, tenant_id=require_tenant_id())
        return config.value if config else None

    @staticmethod
    async def list_entries(actor: Optional[User]) -> List[Tuple[str, str]]:
        """Every configuration entry in this community as ``(name, value)``, for staff."""
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can read system configuration",
        )
        entries = await SystemConfiguration.filter(tenant_id=require_tenant_id()).order_by('name')
        return [(e.name, e.value) for e in entries]

    @staticmethod
    async def set_raw(key: str, value: str, actor: User) -> SystemConfiguration:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can modify system configuration",
        )
        tenant_id = require_tenant_id()
        config = await SystemConfiguration.get_or_none(name=key, tenant_id=tenant_id)
        old_value = config.value if config else None
        if config:
            config.value = value
            await save_fields(config, 'value')
            result = config
        else:
            result = await SystemConfiguration.create(name=key, value=value, tenant_id=tenant_id)
        await AuditService().write_log(
            actor,
            AuditActions.SYSTEM_CONFIG_UPDATED,
            {'key': key, 'old_value': old_value, 'new_value': value},
        )
        return result

    @staticmethod
    async def get_int(key: str, default: Optional[int] = None) -> Optional[int]:
        raw = await SystemConfigService.get_raw(key)
        if raw is None or raw == '':
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    async def get_bool(key: str, default: bool = False) -> bool:
        """A stored flag, defaulting when the key was never written.

        Anything other than the values ``set_raw`` writes for a switch reads as
        the default rather than as ``True`` — a half-written key must not turn a
        display toggle on by accident.
        """
        raw = await SystemConfigService.get_raw(key)
        if raw is None or raw == '':
            return default
        normalized = raw.strip().lower()
        if normalized in ('true', '1', 'yes', 'on'):
            return True
        if normalized in ('false', '0', 'no', 'off'):
            return False
        return default

    @staticmethod
    async def get_date(key: str, default: Optional[date] = None) -> Optional[date]:
        raw = await SystemConfigService.get_raw(key)
        if not raw:
            return default
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return default

    @staticmethod
    async def get_event_window(
        tournament: Optional[Tournament] = None,
    ) -> tuple[date, date]:
        """Return (start_date, end_date) for the event.

        When ``tournament`` sets its own ``event_start_date`` / ``event_end_date``
        those win (each bound independently); any bound the tournament leaves
        unset falls back to the tenant-wide setting. Passing no tournament (the
        default) preserves the tenant-only behavior every tenant-wide caller
        relies on.

        Falls back to the min/max Match.scheduled_at across all matches
        when SystemConfiguration values are missing. If there are no
        scheduled matches either, falls back to today.
        """
        start = tournament.event_start_date if tournament else None
        end = tournament.event_end_date if tournament else None

        if start is None:
            start = await SystemConfigService.get_date(KEY_EVENT_START_DATE)
        if end is None:
            end = await SystemConfigService.get_date(KEY_EVENT_END_DATE)

        if start is None or end is None:
            first = await Match.filter(tenant_id=require_tenant_id()).order_by('scheduled_at').first()
            last = await Match.filter(tenant_id=require_tenant_id()).order_by('-scheduled_at').first()
            derived_start = (
                to_local(first.scheduled_at).date()
                if first and first.scheduled_at else None
            )
            derived_end = (
                to_local(last.scheduled_at).date()
                if last and last.scheduled_at else None
            )
            if start is None:
                start = derived_start or today_local()
            if end is None:
                end = derived_end or start

        if end < start:
            end = start
        return start, end

    @staticmethod
    async def get_max_concurrent_players(default: int = 60) -> int:
        value = await SystemConfigService.get_int(KEY_MAX_CONCURRENT_PLAYERS)
        return value if value is not None and value > 0 else default

    @staticmethod
    async def get_max_concurrent_stages(default: Optional[int] = None) -> int:
        value = await SystemConfigService.get_int(KEY_MAX_CONCURRENT_STAGES)
        if value is not None and value > 0:
            return value
        if default is not None:
            return default
        return await Stage.filter(is_active=True, tenant_id=require_tenant_id()).count()

    @staticmethod
    async def get_volunteer_reminder_lead_minutes(default: int = 60) -> int:
        value = await SystemConfigService.get_int(KEY_VOLUNTEER_REMINDER_LEAD_MINUTES)
        return value if value is not None and value > 0 else default

    @staticmethod
    async def get_community_event_reminder_lead_minutes(default: int = 30) -> int:
        value = await SystemConfigService.get_int(KEY_COMMUNITY_EVENT_REMINDER_LEAD_MINUTES)
        return value if value is not None and value > 0 else default

    @staticmethod
    async def community_events_require_approval() -> bool:
        return await SystemConfigService.get_bool(KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL, default=True)

    @staticmethod
    async def get_volunteer_comp_tiers(
        default: Optional[List[float]] = None,
    ) -> List[float]:
        """Hour thresholds at which a volunteer earns something, ascending.

        SGL comps a badge at 8 hours with more at 12 and 16, which is the
        default when the key was never set. Stored as a comma-separated list of
        hours; a single ``0`` means the community awards nothing by hours.

        A malformed value falls back to the default rather than raising — a
        typo in this box must not take the volunteer roster down.
        """
        fallback = [8.0, 12.0, 16.0] if default is None else sorted(default)
        raw = await SystemConfigService.get_raw(KEY_VOLUNTEER_COMP_TIERS)
        if raw is None or not raw.strip():
            return fallback
        tiers: set[float] = set()
        for token in raw.split(','):
            token = token.strip()
            if not token:
                continue
            try:
                value = float(token)
            except ValueError:
                return fallback
            if value > 0:
                tiers.add(value)
        return sorted(tiers)

    @staticmethod
    def _parse_hours_blob(data: Dict) -> Dict[date, Tuple[time, time]]:
        """Parse a ``{date_iso: {'open', 'close'}}`` blob into typed windows.

        Malformed entries are skipped rather than raising — the same forgiving
        parse the tenant blob has always used, shared now with the per-tournament
        override which stores the identical shape.
        """
        result: Dict[date, Tuple[time, time]] = {}
        if not isinstance(data, dict):
            return result
        for date_str, window in data.items():
            try:
                d = date.fromisoformat(date_str)
                open_t = time.fromisoformat(window['open'])
                close_t = time.fromisoformat(window['close'])
                result[d] = (open_t, close_t)
            except (KeyError, ValueError, TypeError):
                continue
        return result

    @staticmethod
    async def get_tournament_hours(
        tournament: Optional[Tournament] = None,
    ) -> Dict[date, Tuple[time, time]]:
        """Return {date: (open_time, close_time)} for all configured days.

        When ``tournament`` carries its own ``tournament_hours`` blob it fully
        replaces the tenant schedule (a date absent means "unrestricted", the
        same semantics the tenant blob has); otherwise the tenant-wide setting is
        used. Passing no tournament preserves the tenant-only behavior.
        """
        if tournament is not None and tournament.tournament_hours is not None:
            return SystemConfigService._parse_hours_blob(tournament.tournament_hours)
        raw = await SystemConfigService.get_raw(KEY_TOURNAMENT_HOURS)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return SystemConfigService._parse_hours_blob(data)

    @staticmethod
    async def get_tournament_window_for_date(
        d: date, tournament: Optional[Tournament] = None,
    ) -> Optional[Tuple[time, time]]:
        """Return (open_time, close_time) for the given date, or None if not configured."""
        hours = await SystemConfigService.get_tournament_hours(tournament)
        return hours.get(d)

    @staticmethod
    async def get_station_format(default: StationFormat = StationFormat.FREE) -> StationFormat:
        raw = await SystemConfigService.get_raw(KEY_STATION_FORMAT)
        if not raw:
            return default
        try:
            return StationFormat(raw)
        except ValueError:
            return default

    @staticmethod
    async def get_discord_invite_url() -> Optional[str]:
        """The community's Discord invite, or None when staff never set one."""
        return await SystemConfigService.get_raw(KEY_DISCORD_INVITE_URL) or None

    @staticmethod
    def normalize_discord_invite_url(raw: Optional[str]) -> str:
        """A Discord invite in its canonical ``https://discord.gg/<code>`` form.

        Blank clears the setting. Anything that is not a Discord invite is
        refused: the join door renders this as a button, and a link to some
        other site behind a "Join the Discord server" label is the one thing it
        must never be.
        """
        text = (raw or '').strip()
        if not text:
            return ''
        match = _DISCORD_INVITE_RE.match(text)
        if match is None:
            raise ValueError(
                'That isn’t a Discord invite link. Use one that looks like '
                'https://discord.gg/abc123.'
            )
        return f'https://discord.gg/{match.group(1)}'

    @staticmethod
    async def set_discord_invite_url(raw: Optional[str], actor: User) -> None:
        url = SystemConfigService.normalize_discord_invite_url(raw)
        await SystemConfigService.set_raw(KEY_DISCORD_INVITE_URL, url, actor)

    @staticmethod
    def validate_hours_mapping(
        mapping: Dict[date, Tuple[str, str]],
    ) -> Dict[str, Dict[str, str]]:
        """Validate {date: (open_HH_MM, close_HH_MM)} into a storable blob.

        Blank days are dropped; a bad time or a non-increasing window raises a
        user-facing ``ValueError``. Shared by the tenant-wide setter and the
        per-tournament override so both enforce the identical rules.
        """
        data: Dict[str, Dict[str, str]] = {}
        for d, (open_str, close_str) in mapping.items():
            open_str = open_str.strip()
            close_str = close_str.strip()
            if not open_str or not close_str:
                continue
            try:
                open_t = time.fromisoformat(open_str)
                close_t = time.fromisoformat(close_str)
            except ValueError:
                raise ValueError(f"Tournament hours for {d} must be in HH:MM format.") from None
            if close_t <= open_t:
                raise ValueError(f"Close time must be after open time for {d}.")
            data[d.isoformat()] = {'open': open_str, 'close': close_str}
        return data

    @staticmethod
    async def set_tournament_hours(
        mapping: Dict[date, Tuple[str, str]], actor: User,
    ) -> None:
        """Persist per-day tournament hours. mapping is {date: (open_HH_MM, close_HH_MM)}."""
        data = SystemConfigService.validate_hours_mapping(mapping)
        await SystemConfigService.set_raw(KEY_TOURNAMENT_HOURS, json.dumps(data), actor)

    @staticmethod
    def normalize_value(key: str, raw: str) -> str:
        """The value ``key`` may store, or a ``ValueError`` saying what is wrong.

        The admin page validates each field before it calls :meth:`set_raw`; a
        caller writing a key by name (``PUT /api/config/{key}``) gets the same
        rules here. Unknown keys are refused rather than stored: nothing reads
        them, and an open-ended key space is a place to park content that a
        later feature might render without expecting it.
        """
        normalizer = _NORMALIZERS.get(key)
        if normalizer is None:
            raise ValueError(f"'{key}' isn't a configuration setting.")
        return normalizer((raw or '').strip())

    @staticmethod
    async def set_validated(key: str, raw: str, actor: User) -> SystemConfiguration:
        await AuthService.ensure(
            await AuthService.is_staff(actor),
            "Only Staff can modify system configuration",
        )
        return await SystemConfigService.set_raw(
            key, SystemConfigService.normalize_value(key, raw), actor,
        )


def _date_value(text: str) -> str:
    if text:
        try:
            date.fromisoformat(text)
        except ValueError:
            raise ValueError('Dates must be in YYYY-MM-DD format.') from None
    return text


def _positive_int_value(text: str) -> str:
    if not text:
        return ''
    try:
        value = int(text)
    except ValueError:
        raise ValueError('This setting must be a whole number.') from None
    if value < 1:
        raise ValueError('This setting must be at least 1.')
    return str(value)


def _bool_value(text: str) -> str:
    lowered = text.lower()
    if lowered in ('true', '1', 'yes', 'on'):
        return 'true'
    if lowered in ('false', '0', 'no', 'off', ''):
        return 'false'
    raise ValueError('This setting must be true or false.')


def _watch_mode_value(text: str) -> str:
    value = text.strip().lower() or WATCH_MODES[0]
    if value not in WATCH_MODES:
        raise ValueError(f'The Watch page mode must be one of: {", ".join(WATCH_MODES)}.')
    return value


def _comp_tiers_value(text: str) -> str:
    if not text:
        return ''
    values: set[float] = set()
    for token in text.split(','):
        token = token.strip()
        if not token:
            continue
        try:
            value = float(token)
        except ValueError:
            raise ValueError('Comp tiers must be numbers of hours, separated by commas.') from None
        if value < 0:
            raise ValueError('Comp tiers cannot be negative.')
        values.add(value)
    return ', '.join(f'{v:g}' for v in sorted(values))


def _station_format_value(text: str) -> str:
    try:
        return StationFormat(text or StationFormat.FREE.value).value
    except ValueError:
        allowed = ', '.join(f.value for f in StationFormat)
        raise ValueError(f'Station format must be one of: {allowed}.') from None


def _tournament_hours_value(text: str) -> str:
    if not text:
        return json.dumps({})
    try:
        blob = json.loads(text)
    except ValueError:
        raise ValueError('Tournament hours must be a JSON object.') from None
    if not isinstance(blob, dict):
        raise ValueError('Tournament hours must be a JSON object.')
    mapping: Dict[date, Tuple[str, str]] = {}
    for day, window in blob.items():
        try:
            d = date.fromisoformat(day)
            mapping[d] = (str(window['open']), str(window['close']))
        except (ValueError, TypeError, KeyError):
            raise ValueError(
                'Tournament hours look like {"2025-10-23": {"open": "09:00", "close": "22:00"}}.'
            ) from None
    return json.dumps(SystemConfigService.validate_hours_mapping(mapping))


_NORMALIZERS = {
    KEY_EVENT_START_DATE: _date_value,
    KEY_EVENT_END_DATE: _date_value,
    KEY_MAX_CONCURRENT_PLAYERS: _positive_int_value,
    KEY_MAX_CONCURRENT_STAGES: _positive_int_value,
    KEY_VOLUNTEER_REMINDER_LEAD_MINUTES: _positive_int_value,
    KEY_VOLUNTEER_COMP_TIERS: _comp_tiers_value,
    KEY_COMMUNITY_EVENT_REMINDER_LEAD_MINUTES: _positive_int_value,
    KEY_COMMUNITY_EVENT_REQUIRE_APPROVAL: _bool_value,
    KEY_TOURNAMENT_HOURS: _tournament_hours_value,
    KEY_STATION_FORMAT: _station_format_value,
    KEY_DISCORD_AUTO_JOIN: _bool_value,
    KEY_JOIN_REQUESTS: _bool_value,
    KEY_OPEN_MEMBERSHIP: _bool_value,
    KEY_DISCORD_INVITE_URL: SystemConfigService.normalize_discord_invite_url,
    KEY_WATCH_MODE: _watch_mode_value,
    KEY_WATCH_AUTO_STATIC: _bool_value,
    KEY_WATCH_AUTO_THRESHOLD: _positive_int_value,
}
