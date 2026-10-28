"""Seed per-user table layouts.

Global fixtures, like the model itself: ``UserTablePreference`` carries no tenant
column, so this runs once beside ``seed_users`` rather than per community.

One saved layout per shape the reconciler has to handle — a hidden column, a
reordered list, a stored width, and a non-default page size — so the
``/ui-validation`` loop exercises the *applied* path. Without these, every dev
run only ever sees the defaults, which is the path that already works.
"""

from models import User, UserFriend, UserTablePreference

TABLE_PREFERENCE_SPECS: dict[str, dict[str, dict]] = {
    'staff_user': {
        'admin.users': {
            'columns': [
                {'name': 'username', 'visible': True, 'width': 260},
                {'name': 'preferred_name', 'visible': True, 'width': None},
                {'name': 'roles', 'visible': True, 'width': None},
                {'name': 'pronouns', 'visible': False, 'width': None},
            ],
            'density': 'compact',
        },
        'admin.schedule': {
            'columns': [
                {'name': 'players', 'visible': True, 'width': 320},
            ],
            'page_size': 25,
        },
    },
}


async def seed_table_preferences(users: dict[str, User]) -> None:
    """Give the staff fixture a customized layout on two boards. Idempotent."""
    for username, tables in TABLE_PREFERENCE_SPECS.items():
        user = users.get(username)
        if user is None:
            continue
        for table_key, config in tables.items():
            await UserTablePreference.get_or_create(
                user=user, table_key=table_key, defaults={'config': config},
            )
    print('  table preferences ok (global)')


#: Friends lists for Event > Schedule's Friends switch: a volunteer following
#: two players, and a player following a racer in the big race.
FRIEND_SPECS: dict[str, list[str]] = {
    'proctor_user': ['player_two', 'player_three'],
    'player_one': ['racer_07'],
}


async def seed_friends(users: dict[str, User]) -> None:
    """Give two fixtures a friends list. Global like the model. Idempotent."""
    for username, friends in FRIEND_SPECS.items():
        user = users.get(username)
        for friend_name in friends:
            friend = users.get(friend_name)
            if user is not None and friend is not None:
                await UserFriend.get_or_create(user=user, friend=friend)
    print('  friends lists ok (global)')


async def seed_notification_channels(users: dict[str, User]) -> None:
    """player_one gets email and Telegram on; player_two has a confirmed email turned off.

    ``.invalid`` addresses and a negative (never real) chat id, so nothing can
    reach a person even with real SendGrid or Telegram keys in the env.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    specs = {
        'player_one': {
            'email': 'player_one@example.invalid', 'email_verified_at': now, 'email_notifications': True,
            'telegram_chat_id': -900001, 'telegram_username': 'player_one_tg',
            'telegram_linked_at': now, 'telegram_notifications': True,
        },
        'player_two': {
            'email': 'player_two@example.invalid', 'email_verified_at': now, 'email_notifications': False,
        },
    }
    for username, fields in specs.items():
        user = users.get(username)
        if user is None or user.email:
            continue
        await User.filter(id=user.id).update(**fields)
    print('  notification channels ok (global)')
