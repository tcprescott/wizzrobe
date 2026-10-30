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
    # 0-based position in the stage chain; a single-stage tournament has one row
    # at 0. Unique per tournament (see Meta).
    stage_order = fields.IntField(default=0)
    # Schema-validated at the service boundary (never trusted raw): grand-final
    # reset toggle, Swiss round count, group count + points, tiebreaker order,
    # and — on non-first stages — the advancement rule from the prior stage.
    config = fields.JSONField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    entries = fields.ReverseRelation["BracketEntry"]
    bracket_matches = fields.ReverseRelation["BracketMatch"]

    class Meta:
        table = 'bracket'
        # tournament is itself tenant-scoped, so (tournament, stage_order) is a
        # tenant-safe unique; it also serves the stage-ordered list query.
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
    # SET_NULL: deleting a user detaches (and re-placeholders) their entrants
    # rather than erasing bracket history.
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
    # Per-stage seed (stage 2's seeds derive from stage 1's final ranks).
    seed = fields.IntField(null=True)
    # Group-stage formats only; "Group A" is derived display, not a stored name.
    group_number = fields.IntField(null=True)
    # Written when the stage completes, after tiebreakers — this is what the next
    # stage's advancement consumes.
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
    # Group-stage formats only.
    group_number = fields.IntField(null=True)
    # SET_NULL on the entry slots: pruning an entry (e.g. a dropped placeholder)
    # empties the slot rather than deleting the match.
    entry1 = fields.ForeignKeyField(
        'models.BracketEntry', related_name='matches_as_entry1', null=True, on_delete=fields.SET_NULL
    )
    entry2 = fields.ForeignKeyField(
        'models.BracketEntry', related_name='matches_as_entry2', null=True, on_delete=fields.SET_NULL
    )
    winner = fields.ForeignKeyField(
        'models.BracketEntry', related_name='matches_won', null=True, on_delete=fields.SET_NULL
    )
    # Optional set scores reported alongside the winner (nullable — win-only
    # reporting stays valid). Positional to the entry slots: ``entry1_score`` is
    # ``entry1``'s, ``entry2_score`` is ``entry2``'s. The winner must carry the
    # strictly-higher score UNLESS ``forfeit`` is set.
    entry1_score = fields.IntField(null=True)
    entry2_score = fields.IntField(null=True)
    # A DQ / walkover / no-show: when set, the recorded winner may carry the
    # lower or zero score (the score-vs-winner rule is waived) and the card
    # renders an "FF" marker.
    forfeit = fields.BooleanField(default=False)
    state = fields.CharEnumField(BracketMatchState, default=BracketMatchState.PENDING, max_length=16)
    # Progression pointers into the same graph: where this match's winner/loser
    # flow. The ``*_slot`` (1 or 2) says which entry slot they fill. Self-FK,
    # SET_NULL so pruning a downstream match just detaches the pointer.
    winner_to = fields.ForeignKeyField(
        'models.BracketMatch', related_name='feeder_winners', null=True, on_delete=fields.SET_NULL
    )
    winner_to_slot = fields.IntField(null=True)
    loser_to = fields.ForeignKeyField(
        'models.BracketMatch', related_name='feeder_losers', null=True, on_delete=fields.SET_NULL
    )
    loser_to_slot = fields.IntField(null=True)
    # Best-of length for THIS matchup, overriding the per-round default in
    # ``Bracket.config['rounds'][str(round)]['best_of']``. Null = use the round's
    # value, falling back to 1. Positive and odd (a best-of has no draws).
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
    # OneToOne: a scheduled Match backs at most one game. SET_NULL so deleting the
    # Match preserves the recorded game result — the same reasoning the other
    # bracket FKs use, and what lets a cancelled game keep its audit trail.
    match = fields.OneToOneField(
        'models.Match', related_name='bracket_match_game', null=True, on_delete=fields.SET_NULL
    )
    winner_entry = fields.ForeignKeyField(
        'models.BracketEntry', related_name='games_won', null=True, on_delete=fields.SET_NULL
    )
    # A DQ / walkover / no-show in THIS game (one entrant ranked, the other not).
    # A series-level forfeit is the AND of its games'.
    forfeit = fields.BooleanField(default=False)
    state = fields.CharEnumField(
        BracketMatchGameState, default=BracketMatchGameState.SCHEDULED, max_length=16
    )
    # Why a game was never played, e.g. 'series clinched 2-0'. Null unless CANCELLED.
    cancelled_reason = fields.CharField(max_length=255, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'bracketmatchgame'
        # bracket_match is itself tenant-scoped, so this is a tenant-safe unique
        # (the same argument BracketEntry's (bracket, entrant) unique uses). It
        # also serves the ordered per-series listing.
        unique_together = (('bracket_match', 'game_number'),)
        indexes = (('bracket_match',), ('match',))
