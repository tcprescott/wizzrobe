"""Event Service: a community's events, their lifecycle and which one is the default.

A community holds events (one SGL weekend each). Any number can be live at once;
staff mark one as the default, the target of a bare link. Every community has at
least one event.

The events of each community are cached per process as :class:`EventRef`,
because the page middleware resolves the event a URL names on every request and
the header switcher lists them on every page. Safe under the single-worker
deployment; every write here drops the cache, and so does any ``Event`` save or
delete made elsewhere (the signals at the bottom).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from tortoise.signals import post_delete, post_save
from tortoise.transactions import in_transaction

from application.events import EventType
from application.repositories.event_repository import EventRepository
from application.services.audit_service import AuditActions, AuditService
from application.services.auth_service import AuthService
from application.tenant_context import require_tenant_id
from models import Event, EventStatus, Tenant, User

_events_cache: dict[int, tuple['EventRef', ...]] = {}
_CACHE_MAX = 2048

#: Lowercase letters, digits and hyphens, 2 to 64 characters, starting with a letter or digit.
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,63}$')
#: Slugs that read as something else in a URL.
RESERVED_SLUGS = frozenset({'api', 'static', 'admin', 'new', 'e', 't', 'login', 'logout', 'help'})
NAME_MAX = 120

#: Status order on every list: what's on now, what's coming, what's past.
_STATUS_ORDER = {EventStatus.LIVE: 0, EventStatus.DRAFT: 1, EventStatus.ARCHIVED: 2}


def _clear_cache() -> None:
    _events_cache.clear()


@dataclass(frozen=True)
class EventRef:
    """What the chrome and the router need to know about an event, cached."""

    id: int
    tenant_id: int
    name: str
    slug: str
    status: EventStatus
    is_default: bool
    starts_on: Optional[date]
    ends_on: Optional[date]
    #: The event's own timezone settings (``{mode, name}``), overriding the
    #: community's; None follows the community.
    timezone: Optional[tuple[str, str]] = None

    @property
    def is_live(self) -> bool:
        return self.status == EventStatus.LIVE

    @property
    def is_draft(self) -> bool:
        return self.status == EventStatus.DRAFT

    @property
    def is_archived(self) -> bool:
        return self.status == EventStatus.ARCHIVED

    @classmethod
    def of(cls, event: Event) -> 'EventRef':
        return cls(
            id=event.id, tenant_id=event.tenant_id, name=event.name, slug=event.slug,
            status=EventStatus(event.status), is_default=event.is_default,
            starts_on=event.starts_on, ends_on=event.ends_on, timezone=_timezone_of(event.config),
        )


def _timezone_of(config: Any) -> Optional[tuple[str, str]]:
    stored = config.get('timezone') if isinstance(config, dict) else None
    if not isinstance(stored, dict) or not stored.get('mode') or not stored.get('name'):
        return None
    return (str(stored['mode']), str(stored['name']))


def _sort_key(info: EventRef) -> tuple:
    # Default first, then by status, then newest first.
    newest = -(info.starts_on.toordinal() if info.starts_on else 0)
    return (not info.is_default, _STATUS_ORDER[info.status], newest, -info.id)


def suggest_slug(name: str) -> str:
    """A slug for ``name``: ``SGL 2027`` → ``sgl-2027``."""
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:64].strip('-')
    return slug if len(slug) >= 2 else f'event-{slug}'.strip('-')


@dataclass(frozen=True)
class ArchiveCheck:
    """What is still open in an event, shown before it is archived."""

    open_matches: int
    future_shifts: int
    open_race_rooms: int

    @property
    def anything_open(self) -> bool:
        return bool(self.open_matches or self.future_shifts or self.open_race_rooms)



def with_event_name(label: str, event_id: Optional[int], names: Dict[int, str]) -> str:
    """``SGL 2026 · ALttPR Main`` when ``names`` (from ``names_if_mixed``) has the event, else ``label``."""
    name = names.get(event_id) if event_id is not None else None
    if not name:
        return label
    return f'{name} · {label}' if label else name


class EventService:

    # ------------------------------------------------------------------ reads

    @staticmethod
    async def events_for_tenant(tenant_id: int) -> tuple[EventRef, ...]:
        """Every event of ``tenant_id``: default first, then live, drafts, archived."""
        cached = _events_cache.get(tenant_id)
        if cached is not None:
            return cached
        rows = await EventRepository.list_for_tenant(tenant_id)
        infos = tuple(sorted((EventRef.of(e) for e in rows), key=_sort_key))
        if len(_events_cache) >= _CACHE_MAX:
            _events_cache.pop(next(iter(_events_cache)), None)
        _events_cache[tenant_id] = infos
        return infos

    @staticmethod
    async def ref(tenant_id: int, event_id: Optional[int]) -> Optional[EventRef]:
        if event_id is None:
            return None
        return next((e for e in await EventService.events_for_tenant(tenant_id) if e.id == event_id), None)

    @staticmethod
    async def ref_by_slug(tenant_id: int, slug: str) -> Optional[EventRef]:
        return next((e for e in await EventService.events_for_tenant(tenant_id) if e.slug == slug), None)

    @staticmethod
    async def default_event(tenant_id: int) -> Optional[EventRef]:
        return next((e for e in await EventService.events_for_tenant(tenant_id) if e.is_default), None)

    @staticmethod
    async def default_event_id(tenant_id: int) -> Optional[int]:
        """The community's default event id, or None when it has no events yet."""
        default = await EventService.default_event(tenant_id)
        return default.id if default else None

    @staticmethod
    async def live_events(tenant_id: int) -> List[EventRef]:
        return [e for e in await EventService.events_for_tenant(tenant_id) if e.is_live]

    @staticmethod
    async def visible_events(user: Optional[User]) -> List[EventRef]:
        """The current community's events this viewer may open: drafts only for staff."""
        events = await EventService.events_for_tenant(require_tenant_id())
        if await AuthService.is_staff(user):
            return list(events)
        return [e for e in events if not e.is_draft]

    @staticmethod
    async def names_if_mixed(event_ids: Iterable[Optional[int]]) -> Dict[int, str]:
        """Event names for a personal list's rows, or nothing when they're all one event's.

        A person's own pages span every live event; the name only earns its place
        on a row once two events share the list.
        """
        ids = {eid for eid in event_ids if eid is not None}
        if len(ids) < 2:
            return {}
        events = await EventService.events_for_tenant(require_tenant_id())
        return {e.id: e.name for e in events if e.id in ids}

    @staticmethod
    async def can_view(user: Optional[User], event: Optional[EventRef]) -> bool:
        """A draft is staff's until it goes live; everything else is visible as before."""
        if event is None or not event.is_draft:
            return True
        return await AuthService.is_staff(user)

    @staticmethod
    async def match_event_id(match_id: int) -> Optional[int]:
        """The event a match of this community belongs to: a row decides its own event."""
        return await EventRepository.match_event_id(match_id)

    @staticmethod
    async def list_events() -> List[Event]:
        """The current community's events, default first."""
        return await EventRepository.list_all()

    @staticmethod
    async def get_by_slug(slug: str) -> Optional[Event]:
        return await EventRepository.get_by_slug(slug)

    @staticmethod
    async def get(event_id: int) -> Event:
        event = await EventRepository.get_in_tenant(event_id)
        if event is None:
            raise ValueError('That event no longer exists.')
        return event

    @staticmethod
    async def sizes() -> Dict[int, tuple[int, int]]:
        """``event_id`` → ``(tournaments, matches)`` for the Events list."""
        tournaments = await EventRepository.tournament_counts()
        matches = await EventRepository.match_counts()
        return {
            event_id: (tournaments.get(event_id, 0), matches.get(event_id, 0))
            for event_id in set(tournaments) | set(matches)
        }

    @staticmethod
    async def archive_check(event_id: int) -> ArchiveCheck:
        now = datetime.now(timezone.utc)
        return ArchiveCheck(
            open_matches=await EventRepository.open_matches(event_id),
            future_shifts=await EventRepository.future_shifts(event_id, now),
            open_race_rooms=await EventRepository.open_race_rooms(event_id),
        )

    # ----------------------------------------------------------- first event

    @staticmethod
    async def create_first_event(tenant: Tenant) -> Event:
        """Give a new community its first event: live, the default, named after it.

        A community never has zero events, so every tenant-creation path calls
        this. Callers are the platform surface and seed scripts, which run with
        no tenant in context, hence the explicit tenant.
        """
        if await EventRepository.count_for_tenant(tenant.id):
            raise ValueError(f'{tenant.name} already has an event.')
        event = await EventRepository.create_for_tenant(
            tenant.id, name=tenant.name, slug=tenant.slug,
            status=EventStatus.LIVE, is_default=True,
        )
        _clear_cache()
        return event

    @staticmethod
    async def ensure_first_event(tenant: Tenant) -> Event:
        """Idempotent :meth:`create_first_event` for seed and setup scripts."""
        if await EventRepository.count_for_tenant(tenant.id):
            event = await EventRepository.default_for_tenant(tenant.id)
            if event is not None:
                return event
            raise ValueError(f'{tenant.name} has events but no default event.')
        return await EventService.create_first_event(tenant)

    # ------------------------------------------------------------ lifecycle

    @staticmethod
    async def _ensure_staff(actor: User) -> None:
        await AuthService.ensure(await AuthService.is_staff(actor), 'Only staff can manage events.')

    @staticmethod
    def _clean_name(name: str) -> str:
        name = (name or '').strip()
        if not name:
            raise ValueError('Give the event a name.')
        if len(name) > NAME_MAX:
            raise ValueError(f'Keep the name under {NAME_MAX} characters.')
        return name

    @staticmethod
    async def _clean_slug(slug: str, *, exclude_id: Optional[int] = None) -> str:
        slug = (slug or '').strip().lower()
        if not SLUG_RE.match(slug):
            raise ValueError('The link name takes 2 to 64 lowercase letters, digits and hyphens.')
        if slug in RESERVED_SLUGS:
            raise ValueError(f'"{slug}" is reserved. Pick another link name.')
        if await EventRepository.slug_taken(slug, exclude_id=exclude_id):
            raise ValueError(f'Another event already uses "{slug}".')
        return slug

    @staticmethod
    def _check_dates(starts_on: Optional[date], ends_on: Optional[date]) -> None:
        if starts_on and ends_on and ends_on < starts_on:
            raise ValueError('The event has to end on or after the day it starts.')

    @staticmethod
    async def unique_slug_for(name: str) -> str:
        """:func:`suggest_slug`, numbered until no other event of this community uses it."""
        base = suggest_slug(name)
        if base in RESERVED_SLUGS:
            base = f'{base}-event'
        slug, n = base, 2
        while await EventRepository.slug_taken(slug):
            suffix = f'-{n}'
            slug = f'{base[:64 - len(suffix)]}{suffix}'
            n += 1
        return slug

    @staticmethod
    async def create_event(
        actor: User,
        *,
        name: str,
        slug: Optional[str] = None,
        starts_on: Optional[date] = None,
        ends_on: Optional[date] = None,
        copy_from: Optional[int] = None,
        copy: frozenset[str] = frozenset(),
    ) -> tuple[Event, Dict[str, Any]]:
        """Create a draft event, copying the setup ``copy`` names from ``copy_from``.

        Returns the event and what was copied (counts per group, plus anything
        skipped and why). The copy runs in the same transaction as the create.
        """
        from application.services.event_setup_copy import COPY_GROUPS, copy_setup

        await EventService._ensure_staff(actor)
        name = EventService._clean_name(name)
        slug = await EventService._clean_slug(slug) if slug else await EventService.unique_slug_for(name)
        EventService._check_dates(starts_on, ends_on)
        unknown = set(copy) - set(COPY_GROUPS)
        if unknown:
            raise ValueError(f'Unknown setup to copy: {", ".join(sorted(unknown))}')
        source = await EventService.get(copy_from) if copy_from is not None else None
        result: Dict[str, Any] = {}
        async with in_transaction():
            event = await EventRepository.create(
                name=name, slug=slug, status=EventStatus.DRAFT, is_default=False,
                starts_on=starts_on, ends_on=ends_on,
            )
            if source is not None and copy:
                result = await copy_setup(source, event, copy)
        _clear_cache()
        audit = AuditService()
        await audit.write_and_publish(
            actor, AuditActions.EVENT_CREATED,
            {'event_id': event.id, 'name': event.name, 'slug': event.slug},
            EventType.EVENT_CREATED,
        )
        if source is not None and copy:
            await audit.write_and_publish(
                actor, AuditActions.EVENT_SETUP_COPIED,
                {'event_id': event.id, 'from_event_id': source.id, 'copied': result},
                EventType.EVENT_SETUP_COPIED,
            )
        return event, result

    @staticmethod
    async def update_event(
        actor: User,
        event_id: int,
        *,
        name: Optional[str] = None,
        slug: Optional[str] = None,
        starts_on: Any = ...,
        ends_on: Any = ...,
    ) -> Event:
        """Rename an event, change its dates, or (while it is a draft) its link name."""
        await EventService._ensure_staff(actor)
        event = await EventService.get(event_id)
        changes: Dict[str, Any] = {}
        if name is not None and name.strip() != event.name:
            changes['name'] = EventService._clean_name(name)
        if slug is not None and slug.strip().lower() != event.slug:
            if event.status != EventStatus.DRAFT:
                raise ValueError("A live or archived event's link name can't change: links already point at it.")
            changes['slug'] = await EventService._clean_slug(slug, exclude_id=event.id)
        new_start = event.starts_on if starts_on is ... else starts_on
        new_end = event.ends_on if ends_on is ... else ends_on
        EventService._check_dates(new_start, new_end)
        if new_start != event.starts_on:
            changes['starts_on'] = new_start
        if new_end != event.ends_on:
            changes['ends_on'] = new_end
        if not changes:
            return event
        before = {key: getattr(event, key) for key in changes}
        await EventRepository.update(event, **changes)
        _clear_cache()
        await AuditService().write_and_publish(
            actor, AuditActions.EVENT_UPDATED,
            {'event_id': event.id, 'before': _jsonable(before), 'after': _jsonable(changes)},
            EventType.EVENT_UPDATED,
        )
        return event

    @staticmethod
    async def go_live(actor: User, event_id: int) -> Event:
        await EventService._ensure_staff(actor)
        event = await EventService.get(event_id)
        if event.status != EventStatus.DRAFT:
            raise ValueError(f'{event.name} is not a draft.')
        await EventRepository.update(event, status=EventStatus.LIVE)
        _clear_cache()
        await AuditService().write_and_publish(
            actor, AuditActions.EVENT_WENT_LIVE, {'event_id': event.id, 'name': event.name},
            EventType.EVENT_WENT_LIVE,
        )
        return event

    @staticmethod
    async def make_default(actor: User, event_id: int) -> Event:
        await EventService._ensure_staff(actor)
        event = await EventService.get(event_id)
        if event.is_default:
            return event
        if event.status != EventStatus.LIVE:
            raise ValueError('Only a live event can be the default.')
        previous = await EventRepository.default_for_tenant(event.tenant_id)
        async with in_transaction():
            await EventRepository.clear_default()
            await EventRepository.update(event, is_default=True)
        _clear_cache()
        await AuditService().write_and_publish(
            actor, AuditActions.EVENT_DEFAULT_CHANGED,
            {'event_id': event.id, 'previous_event_id': previous.id if previous else None},
            EventType.EVENT_DEFAULT_CHANGED,
        )
        return event

    @staticmethod
    async def archive(actor: User, event_id: int, *, new_default_id: Optional[int] = None) -> Event:
        """Archive an event. When it was the default, ``new_default_id`` takes over.

        With no other live event the archived one stays the default, read-only,
        until another goes live.
        """
        await EventService._ensure_staff(actor)
        event = await EventService.get(event_id)
        if event.status == EventStatus.ARCHIVED:
            raise ValueError(f'{event.name} is already archived.')
        new_default: Optional[Event] = None
        if event.is_default and new_default_id is not None:
            new_default = await EventService.get(new_default_id)
            if new_default.id == event.id or new_default.status != EventStatus.LIVE:
                raise ValueError('Pick a live event to become the default.')
        elif event.is_default and any(
            ref.is_live and ref.id != event.id for ref in await EventService.events_for_tenant(event.tenant_id)
        ):
            raise ValueError('Pick which live event becomes the default.')
        async with in_transaction():
            await EventRepository.update(
                event, status=EventStatus.ARCHIVED, archived_at=datetime.now(timezone.utc), archived_by=actor,
            )
            if new_default is not None:
                await EventRepository.update(event, is_default=False)
                await EventRepository.update(new_default, is_default=True)
        _clear_cache()
        audit = AuditService()
        await audit.write_and_publish(
            actor, AuditActions.EVENT_ARCHIVED, {'event_id': event.id, 'name': event.name},
            EventType.EVENT_ARCHIVED,
        )
        if new_default is not None:
            await audit.write_and_publish(
                actor, AuditActions.EVENT_DEFAULT_CHANGED,
                {'event_id': new_default.id, 'previous_event_id': event.id},
                EventType.EVENT_DEFAULT_CHANGED,
            )
        return event

    @staticmethod
    async def unarchive(actor: User, event_id: int) -> Event:
        await EventService._ensure_staff(actor)
        event = await EventService.get(event_id)
        if event.status != EventStatus.ARCHIVED:
            raise ValueError(f'{event.name} is not archived.')
        await EventRepository.update(event, status=EventStatus.LIVE, archived_at=None, archived_by=None)
        _clear_cache()
        await AuditService().write_and_publish(
            actor, AuditActions.EVENT_UNARCHIVED, {'event_id': event.id, 'name': event.name},
            EventType.EVENT_UNARCHIVED,
        )
        return event

    @staticmethod
    async def delete_draft(actor: User, event_id: int) -> None:
        """Delete a draft that has no matches. Anything else is archived instead."""
        await EventService._ensure_staff(actor)
        event = await EventService.get(event_id)
        if event.status != EventStatus.DRAFT:
            raise ValueError('Only a draft can be deleted. Archive a live event instead.')
        if await EventRepository.has_matches(event.id):
            raise ValueError(f'{event.name} has matches. Archive it instead.')
        details = {'event_id': event.id, 'name': event.name, 'slug': event.slug}
        await EventRepository.delete(event)
        _clear_cache()
        await AuditService().write_and_publish(
            actor, AuditActions.EVENT_DELETED, details, EventType.EVENT_DELETED,
        )


def _jsonable(values: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v.isoformat() if isinstance(v, date) else v for k, v in values.items()}


@post_save(Event)
async def _event_saved(*_args: Any, **_kwargs: Any) -> None:
    _clear_cache()


@post_delete(Event)
async def _event_deleted(*_args: Any, **_kwargs: Any) -> None:
    _clear_cache()
