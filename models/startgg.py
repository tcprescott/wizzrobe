from tortoise import fields
from tortoise.models import Model

from .enums import StartggSetState


class StartggEntrant(Model):
    """An entrant in a linked start.gg event, mirrored read-only.

    ``user`` resolves, in order, from a hand assignment, the entrant's start.gg
    account linked on a Wizzrobe profile, or the Discord account the entrant
    connected on start.gg. ``suggested_user`` is a same-name match staff can
    confirm; it never books anything until they do.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='startgg_entrants', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='startgg_entrants', on_delete=fields.CASCADE)
    entrant_id = fields.CharField(max_length=64)
    name = fields.CharField(max_length=255)
    gamer_tag = fields.CharField(max_length=255, null=True)
    startgg_user_id = fields.CharField(max_length=64, null=True)
    discord_id = fields.CharField(max_length=64, null=True)
    user = fields.ForeignKeyField('models.User', related_name='startgg_entries', null=True, on_delete=fields.SET_NULL)
    # Set when staff chose ``user`` by hand (or confirmed a suggestion). Sync
    # never overwrites it.
    user_assigned_manually = fields.BooleanField(default=False)
    suggested_user = fields.ForeignKeyField(
        'models.User', related_name='startgg_suggestions', null=True, on_delete=fields.SET_NULL,
    )
    # Staff said the suggestion is someone else; sync stops offering it.
    suggestion_dismissed = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'startggentrant'
        unique_together = (('tournament', 'entrant_id'),)
        indexes = (('user',),)


class StartggPhase(Model):
    """One phase of a linked event (e.g. "Group Prelim", "Final Bracket")."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='startgg_phases', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='startgg_phases', on_delete=fields.CASCADE)
    phase_id = fields.CharField(max_length=64)
    name = fields.CharField(max_length=255)
    phase_order = fields.IntField(default=0)
    bracket_type = fields.CharField(max_length=32, null=True)
    state = fields.CharField(max_length=32, null=True)

    class Meta:
        table = 'startggphase'
        unique_together = (('tournament', 'phase_id'),)


class StartggPool(Model):
    """One pool (start.gg "phase group") of a phase.

    ``standings`` is start.gg's placement order as ``[{"entrant_id", "placement"}]``;
    wins and losses are counted from the mirrored sets.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='startgg_pools', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='startgg_pools', on_delete=fields.CASCADE)
    phase = fields.ForeignKeyField('models.StartggPhase', related_name='pools', on_delete=fields.CASCADE)
    phase_group_id = fields.CharField(max_length=64)
    identifier = fields.CharField(max_length=32)
    bracket_type = fields.CharField(max_length=32, null=True)
    complete = fields.BooleanField(default=False)
    standings = fields.JSONField(default=list)

    class Meta:
        table = 'startggpool'
        unique_together = (('tournament', 'phase_group_id'),)


class StartggSet(Model):
    """A start.gg set (one matchup) mirrored for display and booking.

    ``match`` links the Wizzrobe match a player booked for it. Sets of a phase
    that hasn't started carry start.gg's ``preview_…`` ids; they display but
    can't be booked, since start.gg replaces them when the phase starts.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='startgg_sets', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='startgg_sets', on_delete=fields.CASCADE)
    pool = fields.ForeignKeyField('models.StartggPool', related_name='sets', on_delete=fields.CASCADE)
    set_id = fields.CharField(max_length=64)
    round = fields.IntField(default=0)
    round_text = fields.CharField(max_length=64, null=True)
    identifier = fields.CharField(max_length=16, null=True)
    state = fields.CharEnumField(StartggSetState, default=StartggSetState.PENDING, max_length=16)
    entrant1 = fields.ForeignKeyField('models.StartggEntrant', related_name='sets_as_1', null=True, on_delete=fields.SET_NULL)
    entrant2 = fields.ForeignKeyField('models.StartggEntrant', related_name='sets_as_2', null=True, on_delete=fields.SET_NULL)
    # What fills an empty slot, e.g. "Winner of B", shown until it's decided.
    slot1_label = fields.CharField(max_length=64, null=True)
    slot2_label = fields.CharField(max_length=64, null=True)
    # The set whose result fills each empty slot.
    slot1_prereq = fields.CharField(max_length=64, null=True)
    slot2_prereq = fields.CharField(max_length=64, null=True)
    winner = fields.ForeignKeyField('models.StartggEntrant', related_name='sets_won', null=True, on_delete=fields.SET_NULL)
    score1 = fields.IntField(null=True)
    score2 = fields.IntField(null=True)
    completed_at = fields.DatetimeField(null=True)
    match = fields.ForeignKeyField('models.Match', related_name='startgg_sets', null=True, on_delete=fields.SET_NULL)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'startggset'
        unique_together = (('tournament', 'set_id'),)
        indexes = (('match',), ('entrant1',), ('entrant2',))


class StartggConnection(Model):
    """A community's start.gg sign-in: the staff member's OAuth grant that syncs
    and reports results run as.

    One row per tenant. Tokens are secrets: shown to no one and never logged.
    ``STARTGG_API_TOKEN`` is the read-only fallback when a community has none.
    """

    id = fields.IntField(pk=True)
    tenant: fields.OneToOneRelation = fields.OneToOneField(
        'models.Tenant', related_name='startgg_connection', on_delete=fields.CASCADE,
    )
    tenant_id: int
    access_token = fields.TextField()
    refresh_token = fields.TextField(null=True)
    token_expires_at = fields.DatetimeField(null=True)
    scopes = fields.CharField(max_length=255, null=True)
    startgg_user_id = fields.CharField(max_length=64, null=True)
    startgg_username = fields.CharField(max_length=255, null=True)
    connected_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='startgg_connections', null=True, on_delete=fields.SET_NULL,
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'startggconnection'
