from tortoise import fields
from tortoise.models import Model

from .enums import (
    MatchCommentKind,
    PresetOverride,
    RescheduleRequestKind,
    RescheduleRequestStatus,
    StationSide,
)


class Match(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='matches', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='matches')
    # SET_NULL: deleting a stage (or seed) detaches its matches instead of
    # cascade-deleting the entire match and its players/crew/acknowledgments.
    stage = fields.ForeignKeyField(
        'models.Stage', related_name='matches', null=True, on_delete=fields.SET_NULL
    )
    scheduled_at = fields.DatetimeField(null=True, index=True)
    seated_at = fields.DatetimeField(null=True) # now known as "Checked In"
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True, index=True)
    confirmed_at = fields.DatetimeField(null=True)
    comment = fields.TextField(null=True)
    is_stream_candidate = fields.BooleanField(default=False)
    title = fields.CharField(max_length=255, null=True)
    generated_seed = fields.ForeignKeyField(
        'models.GeneratedSeeds', related_name='matches', null=True, on_delete=fields.SET_NULL
    )
    # Staff overruling the players' hard-preset choice for this one match, in
    # either direction (see ``PresetOverride``). Null is the normal case: the
    # players decide. Set only while the seed is unrolled — afterwards
    # ``generated_seed.preset`` is the record of what actually happened and this
    # column would be a claim about a decision already made.
    preset_override = fields.CharEnumField(
        PresetOverride, max_length=16, null=True,
    )
    # Source marker for the SpeedGaming ETL (PR 7). Non-null = this Match was
    # materialized from an SG episode, which makes the ETL-owned fields
    # (``scheduled_at``, players, ``tournament``) read-only in Wizzrobe — the guard
    # lives in ``MatchService.update_match``. SET_NULL so purging a synced episode
    # soft-detaches the Match (everything Wizzrobe added on top survives) rather
    # than cascade-deleting it. OneToOne: an episode maps to exactly one Match.
    speedgaming_episode = fields.OneToOneField(
        'models.SpeedGamingEpisode', related_name='match', null=True, on_delete=fields.SET_NULL
    )
    # Proctor-set "an admin should look at this before confirming" flag. Cleared
    # when the admin confirms — confirming *is* the resolution. Deliberately not
    # a state: the match is still Finished, and deliberately just a flag: the
    # proctor raising it is standing in the room, and typing up what happened is
    # the admin's conversation to have, not a textarea to fill in mid-event.
    needs_review = fields.BooleanField(default=False)
    # Set when the stage reminder for this match has been sent, so the worker
    # fires once. Cleared whenever the stage changes, which re-arms it.
    stage_reminder_sent_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    # related fields
    acknowledgments = fields.ReverseRelation["MatchAcknowledgment"]
    challonge_match = fields.ReverseRelation["ChallongeMatch"]

    @property
    def is_seated(self) -> bool:
        return self.seated_at is not None

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    @property
    def is_started(self) -> bool:
        return self.started_at is not None

    @property
    def current_state(self) -> str:
        if self.is_finished:
            return 'Finished'
        elif self.is_started:
            return 'In Progress'
        elif self.is_seated:
            return 'Checked In'
        else:
            return 'Scheduled'

    class Meta:
        # scheduled_at / finished_at are indexed at the field level; these FK
        # columns drive the tournament- and stage-scoped schedule/report filters.
        indexes = (('tournament',), ('stage',))


class MatchPlayers(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='match_players', on_delete=fields.CASCADE)
    match = fields.ForeignKeyField('models.Match', related_name='players')
    user = fields.ForeignKeyField('models.User', related_name='match_players')
    finish_rank = fields.IntField(null=True)
    # Elapsed finish time in whole seconds, captured from a racetime room result
    # (PR 6). Null for non-finishers (forfeit / no-show / DQ) and for matches not
    # run through a race room. ``finish_rank`` remains the place (1 = winner).
    finish_time = fields.IntField(null=True)
    assigned_station = fields.CharField(max_length=50, null=True)
    # The player's own "running late" heads-up: how far out they said they were,
    # when they said it, and an optional note for staff. Cleared by the player's
    # Undo; check-in makes it moot rather than clearing it, so the record stays.
    late_minutes = fields.IntField(null=True)
    late_at = fields.DatetimeField(null=True)
    late_note = fields.CharField(max_length=280, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (('match', 'user'),)
        table = 'matchplayers'
        # Postgres returns rows in heap order, and an UPDATE moves the row, so
        # without this a seat assignment or a running-late note swaps who is
        # listed first on every board, dialog and DM that reads match.players.
        ordering = ('id',)
        indexes = (('user',),)  # composite is match-first; user-only reverse lookup uncovered


class MatchAcknowledgment(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='match_acknowledgments', on_delete=fields.CASCADE)
    match = fields.ForeignKeyField('models.Match', related_name='acknowledgments', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='match_acknowledgments', on_delete=fields.CASCADE)
    acknowledged_at = fields.DatetimeField(null=True)
    auto_acknowledged = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (('match', 'user'),)
        table = 'matchacknowledgment'


class Stage(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='stages', on_delete=fields.CASCADE)
    name = fields.CharField(max_length=255)
    stream_url = fields.CharField(max_length=255, null=True)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'stage'
        unique_together = (('tenant', 'name'),)


class Station(Model):
    """A physical seat/setup in the venue a match player can be assigned to.

    The pool a proctor picks from. ``MatchPlayers.assigned_station`` stores the
    *label*, not an FK: the pool is a picker and a validation source, and a
    community that has defined no stations keeps the historical free-text
    behaviour.

    ``side`` and ``position`` are the layout half, and both are optional: they
    feed ``MatchService.suggest_stations`` and nothing else, so a pool that
    leaves them null behaves exactly as it did before they existed.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='stations', on_delete=fields.CASCADE)
    name = fields.CharField(max_length=50)
    # Free-text grouping ("North wall", "Row A") shown beside the name in the
    # picker. Purely a label — it carries no pairing semantics.
    section = fields.CharField(max_length=50, null=True)
    # Which half of the room. The seating suggestion puts a match's two players
    # on different sides; a null side can still be assigned by hand but can
    # never satisfy that rule, so the suggestion draws from it last.
    side = fields.CharEnumField(StationSide, max_length=10, null=True)
    # Seat index along a row, used only to tell whether two stations are
    # neighbours: same side and same section, positions one apart.
    position = fields.IntField(null=True)
    sort_order = fields.IntField(default=0)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'station'
        unique_together = (('tenant', 'name'),)


class Commentator(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='commentators', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='commentaries')
    match = fields.ForeignKeyField('models.Match', related_name='commentators')
    approved = fields.BooleanField(default=False)
    # SET_NULL: deleting the approver must not delete another user's crew signup.
    approved_by = fields.ForeignKeyField(
        'models.User', related_name='approved_commentaries', null=True, on_delete=fields.SET_NULL
    )
    acknowledged_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (('match', 'user'),)
        table = 'commentator'


class Tracker(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='trackers', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='trackers')
    match = fields.ForeignKeyField('models.Match', related_name='trackers')
    approved = fields.BooleanField(default=False)
    # SET_NULL: deleting the approver must not delete another user's crew signup.
    approved_by = fields.ForeignKeyField(
        'models.User', related_name='approved_trackers', null=True, on_delete=fields.SET_NULL
    )
    acknowledged_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (('match', 'user'),)
        table = 'tracker'


class MatchWatcher(Model):
    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='match_watchers', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='watched_matches', on_delete=fields.CASCADE)
    match = fields.ForeignKeyField('models.Match', related_name='watchers', on_delete=fields.CASCADE)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'match')
        table = 'matchwatcher'
        indexes = (('match',),)  # composite is user-first; match-only fan-out lookup uncovered


class MatchStreamVolunteer(Model):
    """A player putting their own match forward to be streamed.

    Advisory only. It is a row saying "we're happy to be on stream", nothing
    more: it does not set ``Match.is_stream_candidate``, does not assign a
    stage, and does not oblige anyone. Staff read it while they build the stream
    schedule, and ``is_stream_candidate`` remains the answer they write.

    One row per player, not a flag on the match, so the board can say whether
    one player asked or both did.
    """

    id = fields.IntField(pk=True)
    # Untyped FKs like every other model here; the three annotations keep the
    # mypy ratchet flat for a new file (the ORM builds these descriptors at
    # runtime, so mypy cannot infer them).
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='match_stream_volunteers', on_delete=fields.CASCADE
    )
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='stream_volunteered_matches', on_delete=fields.CASCADE
    )
    match: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Match', related_name='stream_volunteers', on_delete=fields.CASCADE
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'match')
        table = 'matchstreamvolunteer'
        indexes = (('match',),)  # composite is user-first; match-only fan-out lookup uncovered


class MatchComment(Model):
    """A comment on a match, signed with its author's name.

    ``kind`` decides the audience (see ``MatchCommentKind``). The author is
    SET_NULL rather than CASCADE so removing an account keeps the notes staff
    left on a match; the board then shows them unsigned.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='match_comments', on_delete=fields.CASCADE
    )
    match: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Match', related_name='comments', on_delete=fields.CASCADE
    )
    author: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='match_comments', null=True, on_delete=fields.SET_NULL
    )
    kind = fields.CharEnumField(MatchCommentKind, max_length=24)
    body = fields.TextField()
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'matchcomment'
        indexes = (('match',),)


class MatchRescheduleRequest(Model):
    """A player asking staff to move or call off a match they are playing in.

    Players never reschedule or cancel their own matches. They ask, and someone
    who could already have done it decides — ``approve`` runs the *existing*
    ``MatchService.update_match`` / ``cancel_match``, so there is no second way
    to change a match and no authority here that ``can_crud_match`` did not
    already grant.

    Unlike :class:`MatchStreamVolunteer`, which is advisory and needs no answer,
    this exists to be decided, hence ``status`` and the decision columns.

    The free text is load-bearing, which is worth saying because the proctor's
    review note was just removed for being a textarea to fill in mid-event. That
    reasoning does not carry over: a proctor raising a dispute is standing in the
    room with the admin, while this request exists precisely so a player does not
    have to find anyone. The ask and the answer travel as words or not at all.
    """

    id = fields.IntField(pk=True)
    # Untyped FKs elsewhere in this module predate the mypy ratchet; annotating
    # the ones on a new model keeps its baseline at zero.
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='reschedule_requests', on_delete=fields.CASCADE
    )
    match: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Match', related_name='reschedule_requests', on_delete=fields.CASCADE
    )
    requested_by: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='reschedule_requests', on_delete=fields.CASCADE
    )
    kind = fields.CharEnumField(
        RescheduleRequestKind, default=RescheduleRequestKind.RESCHEDULE, max_length=20
    )
    # Null means "I can't make this slot, you pick" — a player who knows only
    # that they are unavailable still has something worth saying. Always null on
    # a CANCEL, which proposes no time by definition.
    proposed_at = fields.DatetimeField(null=True)
    reason = fields.TextField()
    # Snapshotted at submission because approving overwrites ``Match.scheduled_at``:
    # without this, a decided request no longer records what it moved away from.
    original_scheduled_at = fields.DatetimeField(null=True)
    # Set when the match's *other* player agrees, and read as a signal, never a
    # gate — staff decide either way. Only meaningful on a two-player match; see
    # ``MatchRescheduleService`` for why a third player skips this entirely.
    opponent_agreed_at = fields.DatetimeField(null=True)
    status = fields.CharEnumField(
        RescheduleRequestStatus, default=RescheduleRequestStatus.PENDING, max_length=20
    )
    # SET_NULL: deleting a staff account must not delete a player's request.
    decided_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='decided_reschedule_requests', null=True,
        on_delete=fields.SET_NULL,
    )
    decided_at = fields.DatetimeField(null=True)
    decision_note = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'matchreschedulerequest'
        # No unique constraint: "one open request per player per match" is a
        # rule about PENDING rows only, which a composite unique cannot express
        # (and encoding ``status`` into one would forbid a second request after
        # a decline — exactly the retry the decline DM invites). The service
        # enforces it; the index below is what makes that check cheap.
        indexes = (('match', 'status'), ('status',))


class MatchHardPresetOptIn(Model):
    """One player privately agreeing to play their match on the harder preset.

    The match rolls ``Tournament.hard_preset`` only when *every* one of its
    current players has a row here. One player opting in alone changes nothing
    and, crucially, tells nobody: the opt-in is secret until it is unanimous, so
    that choosing the harder settings can never be read as a challenge, and
    declining can never be read as backing down. Nothing outside
    ``MatchHardPresetService`` may report who holds a row.

    One row per player rather than a flag on the match, for the same reason
    ``MatchStreamVolunteer`` is: the question is per person. Agreement is
    *derived* (the opt-in set equals the player set) rather than stored, so a
    roster change re-answers it instead of leaving a stale yes behind.

    Rows stop mattering the moment the seed is rolled — ``GeneratedSeeds.preset``
    is then the record of what was actually played — so the service refuses both
    opting in and withdrawing from that point on.
    """

    id = fields.IntField(pk=True)
    # Untyped FKs like every other model here; the three annotations keep the
    # mypy ratchet flat for a new file (the ORM builds these descriptors at
    # runtime, so mypy cannot infer them).
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='match_hard_preset_opt_ins', on_delete=fields.CASCADE
    )
    match: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Match', related_name='hard_preset_opt_ins', on_delete=fields.CASCADE
    )
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='hard_preset_opt_ins', on_delete=fields.CASCADE
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (('match', 'user'),)
        table = 'matchhardpresetoptin'
