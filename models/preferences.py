"""Per-user UI preferences that outlive a session.

Deliberately **global**, with no ``tenant`` FK. This is the ``User.timezone``
case: which columns a person wants on the Users board is a property of the
table, not of the community whose rows fill it, and someone who is staff in two
communities wants one answer rather than two. The practical consequence is that
there is no tenant column to scope, stamp, or leak-test.
"""

from tortoise import fields
from tortoise.models import Model


class UserTablePreference(Model):
    """One person's saved layout for one table."""

    id = fields.IntField(pk=True)
    # The annotations below and the ignore on ``__str__`` are the Tortoise shape
    # mypy cannot see: a field descriptor's type and the ``<fk>_id`` shadow
    # column are both built at class-creation time. Every other model file
    # carries the same errors in the mypy baseline; a new file has to state them
    # at the line instead.
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='table_preferences', on_delete=fields.CASCADE)
    # Stable identifier for one table, e.g. ``admin.users``. Declared as a
    # constant in ``theme/tables/preferences.py`` so the guardrail can prove
    # uniqueness; stored here as an opaque string.
    table_key = fields.CharField(max_length=64)
    # Shape validated by TablePreferenceService.validate; column *names* are
    # reconciled in presentation, never here.
    config: fields.Field = fields.JSONField(default=dict)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (('user', 'table_key'),)

    def __str__(self) -> str:
        return f'{self.table_key} prefs for user {self.user_id}'  # type: ignore[attr-defined]


class UserFriend(Model):
    """One person on someone's friends list, for the Friends switch on Event > Schedule.

    Global like the table layout above: a friend is a person, and the same crowd
    turns up in several communities, so one list follows its owner everywhere.
    A community's schedule only ever shows that community's matches, so the list
    widens nothing a viewer can see.
    """

    id = fields.IntField(pk=True)
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='friends', on_delete=fields.CASCADE)
    friend: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='friend_of', on_delete=fields.CASCADE)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (('user', 'friend'),)

    def __str__(self) -> str:
        return f'user {self.user_id} friends user {self.friend_id}'  # type: ignore[attr-defined]
