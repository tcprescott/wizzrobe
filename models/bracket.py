"""Native tournament bracket models (see docs/features/brackets.md).

Wizzrobe manages brackets natively — generating, progressing, and standing
tournaments — instead of mirroring them from the Challonge API. The five models
here are all tenant-scoped:

* :class:`Bracket` — one *stage* of a tournament (a single-stage tournament has
  one row; a group→playoff tournament has several, ordered by ``stage_order``).
* :class:`BracketEntrant` — the tournament-level roster row that carries an
  entrant's identity across every stage (placeholder-friendly: a ``display_name``
  now, a linked ``user`` later).
* :class:`BracketEntry` — an entrant's participation within one stage (its seed,
  group, and — once the stage completes — ``final_rank``).
* :class:`BracketMatch` — one slot in a stage's persisted match graph, carrying
  the ``winner_to`` / ``loser_to`` progression pointers so elimination
  advancement is plain pointer-following once the graph is generated.
* :class:`BracketMatchGame` — one game of that slot's best-of-N series, and the
  scheduling seam: each game links to its own scheduled ``Match``. A best-of-1 is
  a series with one game, so there is no special case.

The pairing/progression engine is invoked only at generation (``start``) and at
per-round pairing (Swiss / round robin); at all other times these rows are the
source of truth.
"""

from tortoise import fields
from tortoise.models import Model

from .enums import (
    BracketEntrantStatus,
    BracketEntryStatus,
    BracketFormat,
    BracketMatchGameState,
    BracketMatchState,
    BracketState,
)


class Bracket(Model):
    """One stage of a tournament's bracket."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='brackets', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='brackets', on_delete=fields.CASCADE)
    name = fields.CharField(max_length=255)
    format = fields.CharEnumField(BracketFormat, max_length=32)
    state = fields.CharEnumField(BracketState, default=BracketState.DRAFT, max_length=16)
    stage_order = fields.IntField(default=0)
    config = fields.JSONField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    entries = fields.ReverseRelation["BracketEntry"]
    bracket_matches = fields.ReverseRelation["BracketMatch"]

    class Meta:
        table = 'bracket'
        unique_together = (('tournament', 'stage_order'),)
        indexes = (('tournament',),)


class BracketEntrant(Model):
    """Tournament-level roster row carrying identity across stages.

    Placeholder-friendly: seed with a ``display_name`` now and link a ``user``
    later — one link fixes the entrant in every stage. Team support later = the
    entrant pointing at a team instead of a user; this indirection is the
    future-proofing.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='bracket_entrants', on_delete=fields.CASCADE)
    tournament = fields.ForeignKeyField('models.Tournament', related_name='bracket_entrants', on_delete=fields.CASCADE)
    display_name = fields.CharField(max_length=255)
    user = fields.ForeignKeyField(
        'models.User', related_name='bracket_entrants', null=True, on_delete=fields.SET_NULL
    )
    status = fields.CharEnumField(BracketEntrantStatus, default=BracketEntrantStatus.ACTIVE, max_length=16)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    entries = fields.ReverseRelation["BracketEntry"]

    class Meta:
        table = 'bracketentrant'
        indexes = (('tournament',), ('user',))


class BracketEntry(Model):
    """An entrant's participation in one stage."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='bracket_entries', on_delete=fields.CASCADE)
    bracket = fields.ForeignKeyField('models.Bracket', related_name='entries', on_delete=fields.CASCADE)
    entrant = fields.ForeignKeyField('models.BracketEntrant', related_name='entries', on_delete=fields.CASCADE)
    seed = fields.IntField(null=True)
    group_number = fields.IntField(null=True)
    final_rank = fields.IntField(null=True)
    status = fields.CharEnumField(BracketEntryStatus, default=BracketEntryStatus.ACTIVE, max_length=16)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'bracketentry'
        unique_together = (('bracket', 'entrant'),)
        indexes = (('bracket',),)


class BracketMatch(Model):
    """One slot in a bracket stage's persisted match graph."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='bracket_matches', on_delete=fields.CASCADE)
    bracket = fields.ForeignKeyField('models.Bracket', related_name='bracket_matches', on_delete=fields.CASCADE)
    # Negative round numbers denote the losers bracket (start.gg's convention).
    round = fields.IntField()
    position = fields.IntField()
    group_number = fields.IntField(null=True)
    entry1 = fields.ForeignKeyField(
        'models.BracketEntry', related_name='matches_as_entry1', null=True, on_delete=fields.SET_NULL
    )
    entry2 = fields.ForeignKeyField(
        'models.BracketEntry', related_name='matches_as_entry2', null=True, on_delete=fields.SET_NULL
    )
    winner = fields.ForeignKeyField(
        'models.BracketEntry', related_name='matches_won', null=True, on_delete=fields.SET_NULL
    )
    entry1_score = fields.IntField(null=True)
    entry2_score = fields.IntField(null=True)
    forfeit = fields.BooleanField(default=False)
    state = fields.CharEnumField(BracketMatchState, default=BracketMatchState.PENDING, max_length=16)
    winner_to = fields.ForeignKeyField(
        'models.BracketMatch', related_name='feeder_winners', null=True, on_delete=fields.SET_NULL
    )
    winner_to_slot = fields.IntField(null=True)
    loser_to = fields.ForeignKeyField(
        'models.BracketMatch', related_name='feeder_losers', null=True, on_delete=fields.SET_NULL
    )
    loser_to_slot = fields.IntField(null=True)
    best_of = fields.IntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    games = fields.ReverseRelation["BracketMatchGame"]

    class Meta:
        table = 'bracketmatch'
        unique_together = (('bracket', 'round', 'position'),)
        indexes = (('bracket',),)


class BracketMatchGame(Model):
    """One game of a best-of-N series — the scheduling seam.

    A bracket match spans ``best_of`` games and **every game is its own scheduled
    ``Match``**; a ``Match`` never represents more than one game. This model is
    that link, replacing the old one-shot ``BracketMatch.match`` FK: a best-of-1
    is simply a series with a single game, so there is one code path rather than a
    special case.

    Rows are created **lazily, at schedule time** — a row exists because a game
    was scheduled. "Game 3 of 3, not yet scheduled" is display arithmetic from
    ``best_of`` minus the existing rows, so changing ``best_of`` mid-series never
    orphans anything. ``game_number`` is assigned by the service, never by a
    caller.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField(
        'models.Tenant', related_name='bracket_match_games', on_delete=fields.CASCADE
    )
    bracket_match = fields.ForeignKeyField(
        'models.BracketMatch', related_name='games', on_delete=fields.CASCADE
    )
    game_number = fields.IntField()
    match = fields.OneToOneField(
        'models.Match', related_name='bracket_match_game', null=True, on_delete=fields.SET_NULL
    )
    winner_entry = fields.ForeignKeyField(
        'models.BracketEntry', related_name='games_won', null=True, on_delete=fields.SET_NULL
    )
    forfeit = fields.BooleanField(default=False)
    state = fields.CharEnumField(
        BracketMatchGameState, default=BracketMatchGameState.SCHEDULED, max_length=16
    )
    cancelled_reason = fields.CharField(max_length=255, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'bracketmatchgame'
        unique_together = (('bracket_match', 'game_number'),)
        indexes = (('bracket_match',), ('match',))
