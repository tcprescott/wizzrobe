from tortoise import fields
from tortoise.models import Model

from .enums import ChallongeMatchState


class ChallongeConnection(Model):
    """Single shared Wizzrobe service-account OAuth connection to Challonge.

    Only one connection is meaningful at a time; the most recently saved row is
    authoritative. Tokens are privileged secrets — surfaced only to STAFF and
    never logged.
    """

    id = fields.IntField(pk=True)
    # One connection per tenant: the most recent row for a tenant is
    # authoritative (each tenant links its own Challonge service account).
    tenant = fields.ForeignKeyField('models.Tenant', related_name='challonge_connections', on_delete=fields.CASCADE)
    access_token = fields.CharField(max_length=512)
    refresh_token = fields.CharField(max_length=512, null=True)
    token_expires_at = fields.DatetimeField(null=True)
    scopes = fields.CharField(max_length=255, null=True)
    challonge_username = fields.CharField(max_length=255, null=True)
    connected_by = fields.ForeignKeyField('models.User', related_name='challonge_connections', null=True, on_delete=fields.SET_NULL)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'challongeconnection'


class ChallongeParticipant(Model):
    """A Challonge participant in a linked tournament, mirrored into wizzrobe.

    ``user`` is resolved by matching ``challonge_user_id`` to a player who has
    linked their Challonge identity; it stays null for participants we can't map
    to an wizzrobe user.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='challonge_participants', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='challonge_participants', on_delete=fields.CASCADE)
    challonge_participant_id = fields.CharField(max_length=64)
    name = fields.CharField(max_length=255, null=True)
    challonge_user_id = fields.CharField(max_length=64, null=True)
    user = fields.ForeignKeyField('models.User', related_name='challonge_participations', null=True, on_delete=fields.SET_NULL)
    # Set when a tournament editor assigned ``user`` by hand — an entrant added
    # by name has no Challonge account to resolve. Sync never overwrites it.
    user_assigned_manually = fields.BooleanField(default=False)
    # False once Challonge marks the entrant inactive: dropped after the bracket
    # started, so their remaining matches are forfeits. They leave the roster.
    active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'challongeparticipant'
        unique_together = (('tournament', 'challonge_participant_id'),)
        indexes = (('user',),)  # resolve participants for a linked wizzrobe user


class ChallongeMatch(Model):
    """A Challonge bracket match mirrored into wizzrobe.

    ``match`` links to the scheduled wizzrobe :class:`Match` once a player has
    scheduled it; it is null while the matchup is still unscheduled.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='challonge_matches', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='challonge_matches', on_delete=fields.CASCADE)
    challonge_match_id = fields.CharField(max_length=64)
    round = fields.IntField(null=True)
    state = fields.CharEnumField(ChallongeMatchState, default=ChallongeMatchState.PENDING, max_length=20)
    # Challonge numbers group and elimination rounds from 1 alike and doesn't
    # label which is which, so a match seen while the group stage runs is marked
    # here once and stays marked (see ChallongeService._mirror_bracket).
    group_stage = fields.BooleanField(default=False)
    participant1 = fields.ForeignKeyField('models.ChallongeParticipant', related_name='matches_as_p1', null=True, on_delete=fields.SET_NULL)
    participant2 = fields.ForeignKeyField('models.ChallongeParticipant', related_name='matches_as_p2', null=True, on_delete=fields.SET_NULL)
    winner_participant = fields.ForeignKeyField('models.ChallongeParticipant', related_name='matches_as_winner', null=True, on_delete=fields.SET_NULL)
    # Challonge's letter for the match ("C"), and the remote match whose winner
    # (or loser) fills each empty slot: what a staff booking made before the
    # players are known names its sides by.
    identifier = fields.CharField(max_length=16, null=True)
    slot1_prereq = fields.CharField(max_length=64, null=True)
    slot2_prereq = fields.CharField(max_length=64, null=True)
    slot1_label = fields.CharField(max_length=64, null=True)
    slot2_label = fields.CharField(max_length=64, null=True)
    match = fields.ForeignKeyField('models.Match', related_name='challonge_match', null=True, on_delete=fields.SET_NULL)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'challongematch'
        unique_together = (('tournament', 'challonge_match_id'),)
        indexes = (('match',), ('participant1',), ('participant2',))


class ChallongeApiUsage(Model):
    """Per-calendar-month tally of real outbound Challonge API requests.

    One row per ``YYYY-MM`` period; incremented at the client's single HTTP
    choke point so we can show consumption against the monthly quota and decide
    whether more capacity is needed.
    """

    id = fields.IntField(pk=True)
    # Per-tenant connections mean per-tenant Challonge quotas: usage is tallied
    # per (tenant, period), not globally.
    tenant = fields.ForeignKeyField('models.Tenant', related_name='challonge_api_usage', on_delete=fields.CASCADE)
    period = fields.CharField(max_length=7)  # 'YYYY-MM' (UTC)
    request_count = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'challongeapiusage'
        unique_together = (('tenant', 'period'),)
