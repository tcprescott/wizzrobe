"""
Timezone Utilities

All datetimes are stored in UTC. Which wall clock a human reads them on is a
presentation choice that varies per viewer, so every function here takes an
optional ``tz`` and otherwise resolves the ambient display zone from
``application/timezone_context.py``.

Two rules keep this honest:

- **Never store a localized datetime.** :func:`parse_local_datetime` is the only
  wall-clock → UTC ingress; it returns UTC-aware.
- **Never render a raw UTC one.** The ``format_local_*`` builders are the only
  UTC → human egress.

``tz=None`` (the default) means "the current viewer's zone". Pass an explicit
``tz`` whenever the output is *not* for the current request's viewer — a shared
or cached artifact, or a notification rendered for someone else. See
``docs/timezone-handling.md``.
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo

from application.timezone_context import (
    FALLBACK_TIMEZONE,
    current_timezone,
    get_zone,
    hour_cycle_scope,
    is_valid_timezone,
    uses_12_hour,
)

# The app's historical fixed zone. Retained as a named constant because a few
# call sites legitimately need *this specific* zone rather than the viewer's:
# the fallback when nothing resolves, and tests asserting the default.
EASTERN_TZ = ZoneInfo(FALLBACK_TIMEZONE)

TzArg = Optional[Union[str, ZoneInfo]]


def _resolve(tz: TzArg = None) -> ZoneInfo:
    """Coerce a ``tz`` argument to a :class:`ZoneInfo`.

    ``None`` → the ambient viewer zone. A string is validated, because zone names
    reach here from stored config, a user column, and a browser cookie; an
    unloadable one degrades to the ambient zone rather than raising mid-render.
    """
    if tz is None:
        return current_timezone()
    if isinstance(tz, ZoneInfo):
        return tz
    if is_valid_timezone(tz):
        return get_zone(tz)
    return current_timezone()


def now_local(tz: TzArg = None) -> datetime:
    """Current time as an aware datetime on the display clock.

    Use only when the *wall clock* matters — deriving "today", bucketing by
    calendar day, comparing against an opening time. For a plain instant
    comparison use ``datetime.now(timezone.utc)``: it is unambiguous and needs
    no zone.
    """
    return datetime.now(_resolve(tz))


def next_whole_hour_local(
    lead: timedelta = timedelta(minutes=30), tz: TzArg = None,
) -> datetime:
    """The first whole hour at least ``lead`` from now, on the display clock.

    The default a booking form offers. "Now" is a time that has already passed
    by the time anyone presses Schedule, so a form defaulting to it books a
    match that has already started; 08:48 offers 10:00, 08:20 offers 09:00.

    The step to the next hour is taken in UTC, not on the wall clock: on a
    spring-forward night 01:50 + one wall hour is 02:50, a time that does not
    exist and that :func:`parse_local_datetime` would then refuse. Stepping the
    instant lands on 03:00.
    """
    zone = _resolve(tz)
    start = (datetime.now(timezone.utc) + lead).astimezone(zone)
    hour = start.replace(minute=0, second=0, microsecond=0)
    if hour < start:
        hour = (hour.astimezone(timezone.utc) + timedelta(hours=1)).astimezone(zone)
    return hour


def today_local(tz: TzArg = None) -> date:
    """The current calendar date on the display clock.

    Split out from ``now_local().date()`` because the date is the tz-sensitive
    part: at 21:00 in New York it is already tomorrow in London. Every "today"
    default in the UI goes through here so the answer follows the viewer.
    """
    return now_local(tz).date()


def parse_local_datetime(date_str: str, time_str: str, tz: TzArg = None) -> datetime:
    """Parse ``YYYY-MM-DD`` + ``HH:MM`` as display-local wall clock, return UTC.

    The single wall-clock → UTC ingress for the whole app.

    Raises:
        ValueError: on a malformed date/time, or on a wall clock that **does not
            exist** in the target zone — the hour skipped by a DST spring-forward.
            Silently accepting it (what ``replace(tzinfo=…)`` does on its own)
            stored a time an hour off from the one the user typed, with nothing
            on screen to show it had moved.

    Ambiguous times — the hour repeated by a DST fall-back — resolve to the
    **first** (still-daylight-saving) occurrence, matching ``fold=0``. There is
    no UI to express "the second 01:30", and picking the earlier instant is the
    conventional reading of a schedule entry.
    """
    zone = _resolve(tz)
    try:
        naive_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError as e:
        raise ValueError(f"Invalid date/time format: {e}") from e

    local_dt = naive_dt.replace(tzinfo=zone)

    # A nonexistent wall clock is detectable by round-tripping: for a real time,
    # UTC → back to the zone reproduces the same wall clock. Inside a spring-
    # forward gap it does not, because the zone maps the gap onto a different
    # instant than the one the naive value names.
    if local_dt.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive_dt:
        raise ValueError(
            f"{date_str} {time_str} does not exist in {zone.key} — "
            "the clocks move forward over that time. Pick a different time."
        )

    return local_dt.astimezone(timezone.utc)


def combine_local(day: date, moment: time, tz: TzArg = None) -> datetime:
    """Combine a naive date + time as display-local wall clock, return UTC.

    The programmatic twin of :func:`parse_local_datetime`, for callers that
    already hold ``date``/``time`` objects instead of form strings (report
    windows, availability grids, tournament-hours bounds). ``datetime.combine``
    alone yields a **naive** value that later gets read as UTC — the silent
    whole-day shift this function exists to prevent.

    Unlike :func:`parse_local_datetime` this does not reject a nonexistent wall
    clock: its callers construct range bounds (00:00, 23:59) rather than a time
    a human typed, and a range must always produce a usable instant.
    """
    zone = _resolve(tz)
    return datetime.combine(day, moment).replace(tzinfo=zone).astimezone(timezone.utc)


def local_day_bounds(start: date, end: date, tz: TzArg = None) -> tuple[datetime, datetime]:
    """Half-open UTC bounds covering the local days ``start``..``end`` inclusive.

    The correct way to turn a picked date range into a query filter: the lower
    bound is local midnight on ``start``, the upper is local midnight on the day
    *after* ``end``. Computing the upper as ``end 23:59:59`` loses the final
    second, and computing either with ``datetime.combine`` alone shifts the whole
    window by the UTC offset.
    """
    return (
        combine_local(start, time(0, 0), tz),
        combine_local(end + timedelta(days=1), time(0, 0), tz),
    )


def to_local(dt: Optional[datetime], tz: TzArg = None) -> Optional[datetime]:
    """Convert a datetime to the display zone.

    A naive input is assumed UTC — every naive datetime in this codebase comes
    from a store that dropped its tzinfo, never from a user.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_resolve(tz))


def to_utc_aware(dt: datetime) -> datetime:
    """Return a UTC tz-aware datetime.

    A naive input is interpreted as UTC and stamped with ``timezone.utc``;
    an already-aware input is converted to UTC. Use to normalize datetimes
    that may have lost their tzinfo (e.g. after a round-trip through a store
    that strips it) before comparison or storage.

    Deliberately takes no ``tz``: this is normalization, not presentation.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_CLOCK = re.compile(r'%H:%M(:%S)?')


def _clock_12(local: datetime, fmt: str) -> str:
    """``fmt`` with each ``%H:%M`` (and ``%H:%M:%S``) spelled as ``9:30 PM``.

    The hour and suffix are built here rather than with ``%-I``/``%p``: ``%-I``
    is glibc-only and ``%p`` follows the process locale. A no-break space keeps
    ``PM`` from wrapping onto a line of its own.
    """
    hour = str(local.hour % 12 or 12)
    suffix = 'AM' if local.hour < 12 else 'PM'
    return _CLOCK.sub(lambda m: f"{hour}:%M{m.group(1) or ''}\u00a0{suffix}", fmt)


def strftime_local(local: datetime, fmt: str) -> str:
    """``local.strftime(fmt)`` on the viewer's 12- or 24-hour clock.

    For a datetime already on the right wall clock. Only display formats go
    through here: a value for a native ``<input type=time>`` must stay ``%H:%M``.
    """
    if uses_12_hour() and 'T%H' not in fmt:
        fmt = _clock_12(local, fmt)
    return local.strftime(fmt)


def format_local_datetime(
    dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M", tz: TzArg = None
) -> str:
    """Format a datetime on the display clock. ``''`` when ``dt`` is None.

    ``%H:%M`` follows the viewer's 12/24-hour choice (``9:30 PM`` or ``21:30``).
    """
    if dt is None:
        return ''
    return strftime_local(to_utc_aware(dt).astimezone(_resolve(tz)), fmt)


def format_local_date(dt: Optional[Union[datetime, date]], tz: TzArg = None) -> str:
    """Format the date portion on the display clock, ``YYYY-MM-DD``.

    A ``datetime`` is converted first, so which date you get depends on the zone
    — that is the point. A plain ``date`` carries no instant to convert and is
    formatted as-is.
    """
    if dt is None:
        return ''
    if isinstance(dt, datetime):
        return format_local_datetime(dt, "%Y-%m-%d", tz)
    return dt.strftime("%Y-%m-%d")


def format_local_time(dt: Optional[datetime], tz: TzArg = None) -> str:
    """Format the time portion on the display clock, ``21:30`` or ``9:30 PM``."""
    return format_local_datetime(dt, "%H:%M", tz)


def format_local_hour(dt: Optional[datetime], tz: TzArg = None) -> str:
    """An hour-scale axis label: ``21:00``, or ``9 PM`` on a 12-hour clock."""
    if dt is None:
        return ''
    local = to_utc_aware(dt).astimezone(_resolve(tz))
    if uses_12_hour():
        return f"{local.hour % 12 or 12}\u00a0{'AM' if local.hour < 12 else 'PM'}"
    return local.strftime("%H:%M")


def format_local_time_range(
    start: Optional[datetime], end: Optional[datetime], tz: TzArg = None
) -> str:
    """``HH:MM–HH:MM`` on the display clock, captioned with the zone: ``09:00–13:00 EDT``.

    The label is taken at ``start``, so a range on a DST-change day carries the
    abbreviation in force when it begins.
    """
    if start is None or end is None:
        return ''
    return f"{format_local_time(start, tz)}–{format_local_time(end, tz)} {timezone_label(tz, start)}"


def format_local_display(dt: Optional[datetime], tz: TzArg = None) -> str:
    """Format for display with a zone label: ``2025-01-15 14:30 EST``.

    ``%Z`` yields whatever label tzdata carries for the zone at that instant —
    an abbreviation where one exists (``EST``, ``AEDT``), otherwise a numeric
    form (``+05:30``). Both are unambiguous to a reader, which is the job.
    """
    if dt is None:
        return ''
    return strftime_local(to_utc_aware(dt).astimezone(_resolve(tz)), "%Y-%m-%d %H:%M %Z")


def format_local_short(dt: Optional[datetime], tz: TzArg = None) -> str:
    """A compact stamp for a busy cell, ``Oct 20 23:59``: no year, no zone.

    Only for surfaces that state the zone once nearby (a caption or header),
    since a bare time is otherwise ambiguous.
    """
    if dt is None:
        return ''
    month, day, clock = format_local_datetime(dt, "%b %d %H:%M", tz).split(maxsplit=2)
    return f"{month} {int(day)} {clock}"


def timezone_label(tz: TzArg = None, at: Optional[datetime] = None) -> str:
    """The zone's short label at ``at`` (default now) — ``EST``, ``AEDT``, ``+05:30``.

    For captions and column headers that state which clock a whole table is on,
    so individual cells need not repeat it.
    """
    zone = _resolve(tz)
    moment = (at or datetime.now(timezone.utc)).astimezone(zone)
    return moment.strftime("%Z")


_DISCORD_TIME = re.compile(r'<t:(\d{1,12})(?::[tTdDfFR])?>')
# time_field's "<t:X:F> · <t:X:R>": the countdown only means something live in Discord.
_DISCORD_TIME_PAIR = re.compile(r'<t:(\d{1,12}):[tTdDfF]> · <t:\1:R>')


def readable_discord_times(text: str, tz: TzArg = None) -> str:
    """Discord's ``<t:…>`` timestamp markup as plain local times.

    For every copy of a DM that isn't Discord (device, email and Telegram
    notifications, the Sent messages log), which would otherwise show the raw
    ``<t:1792593000:F>``. A time with its countdown becomes the one time.
    """
    if '<t:' not in (text or ''):
        return text or ''
    text = _DISCORD_TIME_PAIR.sub(r'<t:\1:F>', text)
    # Notification copies stay 24-hour on purpose: one may be rendered
    # inside a staff member's UI handler, which would otherwise lend it their clock.
    with hour_cycle_scope(False):
        return _DISCORD_TIME.sub(
            lambda m: format_local_display(datetime.fromtimestamp(int(m.group(1)), timezone.utc), tz),
            text,
        )
