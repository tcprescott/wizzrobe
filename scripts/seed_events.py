"""Dev seed: a community's events beyond its first.

Every community gets its first event (live, the default) from
``EventService.ensure_first_event``. The default community also gets one
event in each other state, so the switcher, the archived banner and the
draft-visibility rules all have something to show:

- **SGL 2025**, archived a week after it ended;
- **SGL 2027**, a draft being set up.

Idempotent: rows are keyed by ``(tenant, slug)``.
"""

from datetime import date, datetime, timezone

from models import Event, EventStatus, Tenant


async def seed_events_for_tenant(tenant: Tenant) -> None:
    await Event.get_or_create(
        tenant=tenant, slug='sgl25',
        defaults={
            'name': 'SGL 2025', 'status': EventStatus.ARCHIVED, 'is_default': False,
            'starts_on': date(2025, 10, 23), 'ends_on': date(2025, 10, 26),
            'archived_at': datetime(2025, 11, 2, 17, 0, tzinfo=timezone.utc),
        },
    )
    await Event.get_or_create(
        tenant=tenant, slug='sgl27',
        defaults={
            'name': 'SGL 2027', 'status': EventStatus.DRAFT, 'is_default': False,
            'starts_on': date(2027, 10, 21), 'ends_on': date(2027, 10, 24),
        },
    )
