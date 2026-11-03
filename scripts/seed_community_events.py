"""Dev-seed community events: one in each status, plus subscriptions and a full RSVP list.

Times are relative to the run, and ``update_or_create`` keeps them so: a dev
database reseeded next week still has events coming up rather than a listing of
last week's.
"""

from datetime import datetime, timedelta

from models import (
    CommunityEvent,
    CommunityEventSeen,
    CommunityEventStatus,
    CommunityEventSubscription,
    Tenant,
    User,
)

_BOARD_GAMES = """Bring a game or play one of ours. We'll have **Codenames**, *Wingspan* and a few
copies of Love Letter.

- Starts small, gets loud
- Snacks welcome
- Ask for Player One at the door

> The room stays open after the official schedule ends."""


async def seed_community_events_for_tenant(
    tenant: Tenant, users: dict[str, User], now_utc: datetime,
) -> None:
    staff = users['staff_user']
    specs = [
        ('Board game night', 'player_one', CommunityEventStatus.APPROVED, timedelta(hours=3), 240,
         'Board game room, 2nd floor', _BOARD_GAMES, None),
        ('Morning coffee run', 'sm_user', CommunityEventStatus.APPROVED, timedelta(minutes=-30), 90,
         'Lobby by the front desk', 'Walking over to the café on the corner. Join us.', None),
        ('Speedrun route Q&A', 'staff_user', CommunityEventStatus.APPROVED, timedelta(days=1), 90,
         'Panel room B', '## Bring your questions\n\nRunners answer anything about their routes.', None),
        ('Retro trading meetup', 'player_two', CommunityEventStatus.PENDING, timedelta(days=1, hours=2), 120,
         'Food court', 'Swap carts, manuals and stories.', None),
        ('Late-night blind race', 'player_three', CommunityEventStatus.REJECTED, timedelta(days=1, hours=6), 60,
         None, 'A blind race in the tournament room.',
         'The tournament room is closed overnight. Could you pick another room?'),
        ('Cosplay photo walk', 'player_four', CommunityEventStatus.CANCELLED, timedelta(days=2), 60,
         'Main entrance', 'Meet at the main entrance with your costume.', None),
        ('Speedrun trivia', 'sm_user', CommunityEventStatus.APPROVED, timedelta(hours=5), 60,
         'Panel room B', 'Teams of two. Questions on routes, glitches and world records.', None),
    ]
    events: dict[str, CommunityEvent] = {}
    for title, host, status, offset, minutes, location, description, note in specs:
        reviewed = status in (CommunityEventStatus.APPROVED, CommunityEventStatus.REJECTED)
        event, _ = await CommunityEvent.update_or_create(
            tenant=tenant, title=title,
            defaults={
                'host': users[host],
                'status': status,
                'starts_at': now_utc + offset,
                'duration_minutes': minutes,
                'location': location,
                'description': description,
                'review_note': note,
                'reviewed_by': staff if reviewed else None,
                'reviewed_at': now_utc if reviewed else None,
            },
        )
        events[title] = event

    for title, usernames in (
        ('Board game night', ('player_one', 'player_two', 'player_three', 'staff_user')),
        ('Speedrun route Q&A', ('player_one',)),
    ):
        for username in usernames:
            await CommunityEventSubscription.get_or_create(
                tenant=tenant, event=events[title], user=users[username],
            )
    await CommunityEvent.filter(id=events['Board game night'].id).update(rsvp_enabled=True, rsvp_capacity=6)
    await CommunityEventSubscription.filter(
        event_id=events['Board game night'].id,
        user_id__in=[users['player_one'].id, users['player_two'].id, users['player_three'].id],
    ).update(going=True)
    for username in ('player_two', 'player_three'):
        await CommunityEventSubscription.get_or_create(
            tenant=tenant, event=events['Speedrun trivia'], user=users[username],
        )
    await CommunityEvent.filter(id=events['Speedrun trivia'].id).update(rsvp_enabled=True, rsvp_capacity=2)
    await CommunityEventSubscription.filter(event_id=events['Speedrun trivia'].id).update(going=True)
    await CommunityEvent.filter(id=events['Morning coffee run'].id).update(
        reviewed_at=now_utc - timedelta(days=2),
    )
    await CommunityEventSeen.update_or_create(
        tenant=tenant, user=users['player_one'], defaults={'seen_at': now_utc - timedelta(days=1)},
    )
    print(f"    [{tenant.slug}] community events ok (one per status, with subscriptions and RSVPs)")
