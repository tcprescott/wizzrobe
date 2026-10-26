"""The ALTTP item tracker: one board per runner per match, marked by crew.

A crew tracker watches the runner's feed and marks what they collect, so the
stream can show it. The runner never touches it. ``state`` is the board as the
tracker sees it now; the broadcast copy lags by ``delay_seconds`` and is rebuilt
from :class:`ItemTrackerChange` rows, which double as the board's undo log.
"""

from tortoise import fields
from tortoise.models import Model


class ItemTrackerBoard(Model):
    """One runner's board on one match."""

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='item_tracker_boards', on_delete=fields.CASCADE
    )
    tenant_id: int
    match: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Match', related_name='item_tracker_boards', on_delete=fields.CASCADE
    )
    match_id: int
    runner: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='item_tracker_boards', on_delete=fields.CASCADE
    )
    runner_id: int
    # ``{key: level}``; a key that is absent is level 0.
    state = fields.JSONField(default=dict)  # type: ignore[var-annotated]
    # Null reads the dungeon-item mode from the match's seed; a value overrides
    # it, for a seed rolled outside Wizzrobe.
    dungeon_items = fields.CharField(max_length=16, null=True)
    delay_seconds = fields.IntField(default=0)
    # The OBS browser-source link carries this instead of a session.
    overlay_token = fields.CharField(max_length=64, unique=True)
    # Bumped on every write so open views repaint only when something changed.
    version = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'itemtrackerboard'
        unique_together = (('match', 'runner'),)
        table_description = "One runner's ALTTP item tracker board on one match."


class ItemTrackerChange(Model):
    """One mark on a board: which key went from which level to which."""

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='item_tracker_changes', on_delete=fields.CASCADE
    )
    tenant_id: int
    board: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.ItemTrackerBoard', related_name='changes', on_delete=fields.CASCADE
    )
    board_id: int
    # SET_NULL: a departed tracker's marks still explain the board.
    actor: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='item_tracker_changes', null=True, on_delete=fields.SET_NULL
    )
    actor_id: int
    key = fields.CharField(max_length=32)
    before = fields.IntField()
    after = fields.IntField()
    # Set on a change someone took back; the take-back is a change of its own.
    undone = fields.BooleanField(default=False)
    is_undo = fields.BooleanField(default=False)
    created_at = fields.DatetimeField()

    class Meta:
        table = 'itemtrackerchange'
        indexes = (('board', 'created_at'),)
        table_description = 'One mark on an item tracker board; also its undo log and stream delay.'
