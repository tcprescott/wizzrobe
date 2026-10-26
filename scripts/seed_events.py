"""Dev seed: a community's events beyond its first.

Every community gets its first event (live, the default) from
``EventService.ensure_first_event``. The default community also gets one
event in each other state, so the switcher, the archived banner and the
draft-visibility rules all have something to show:

- **Winter Online**, a second live event with one tournament and a match for
  player_one and player_two, so their personal pages span two events;
- **SGL 2025**, archived a week after it ended;
- **SGL 2027**, a draft being set up.

Idempotent: rows are keyed by ``(tenant, slug)``.

:func:`install_event_stamping` lets the rest of the seed keep working with
only a tenant in scope, the way the test harness does: service calls run in the
community's default event, and a row an event owns created directly lands in
its parent's event (a match in its tournament's) or else that default.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from tortoise.fields.relational import CASCADE
from tortoise.models import Model
from tortoise.signals import post_save, pre_save

from application.repositories._event import EVENT_PARENTS
from models import Event, EventStatus, Match, MatchPlayers, Tenant, Tournament, TournamentPlayers, User

_defaults: Dict[int, int] = {}


async def _default_event_id(tenant_id: int) -> Optional[int]:
    if tenant_id not in _defaults:
        event = await Event.get_or_none(tenant_id=tenant_id, is_default=True)
        if event is None:
            return None
        _defaults[tenant_id] = event.id
    return _defaults[tenant_id]


async def _remember_default(sender: Any, instance: Event, created: bool, using_db: Any, update_fields: Any) -> None:
    if instance.is_default:
        _defaults[instance.tenant_id] = instance.id  # type: ignore[attr-defined]


async def _stamp_event(sender: type[Model], instance: Model, using_db: Any, update_fields: Any) -> None:
    if getattr(instance, 'event_id', None) is not None:
        return
    parent = EVENT_PARENTS.get(sender.__name__)
    parent_id = getattr(instance, f'{parent}_id', None) if parent else None
    if parent and parent_id is not None:
        parent_model = sender._meta.fields_map[parent].related_model  # type: ignore[attr-defined]
        row = await parent_model.get(id=parent_id)
        instance.event_id = row.event_id  # type: ignore[attr-defined]
        return
    instance.event_id = await _default_event_id(instance.tenant_id)  # type: ignore[attr-defined]


async def install_event_stamping() -> None:
    """Run the seed in each community's default event, and stamp rows with it."""
    from tortoise import Tortoise

    from application import event_context

    for tenant_id, event_id in await Event.filter(is_default=True).values_list('tenant_id', 'id'):
        _defaults[tenant_id] = event_id  # type: ignore[index]
    post_save(Event)(_remember_default)
    event_context._fallback_event_id = _defaults.get  # type: ignore[assignment]

    owned = [
        model for model in Tortoise.apps.get('models', {}).values()
        if 'event' in model._meta.fields_map
        and getattr(model._meta.fields_map['event'], 'related_model', None) is Event
        and getattr(model._meta.fields_map['event'], 'on_delete', None) == CASCADE
    ]
    pre_save(*owned)(_stamp_event)


async def seed_events_for_tenant(tenant: Tenant, users: Dict[str, User]) -> None:
    today = datetime.now(timezone.utc).date()
    winter, _ = await Event.get_or_create(
        tenant=tenant, slug='winter',
        defaults={
            'name': 'Winter Online', 'status': EventStatus.LIVE, 'is_default': False,
            'starts_on': today, 'ends_on': today + timedelta(days=30),
        },
    )
    cup, _ = await Tournament.get_or_create(
        tenant=tenant, event=winter, name='Winter Async Cup',
        defaults={'is_active': True, 'average_match_duration': 60},
    )
    players = [users[k] for k in ('player_one', 'player_two') if k in users]
    for player in players:
        await TournamentPlayers.get_or_create(tenant=tenant, tournament=cup, user=player)
    match, created = await Match.get_or_create(
        tenant=tenant, tournament=cup, title='Winter Cup Round 1',
        defaults={'scheduled_at': datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=2)},
    )
    if created:
        for player in players:
            await MatchPlayers.get_or_create(tenant=tenant, match=match, user=player)
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
