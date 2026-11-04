"""A tournament's pre-match form: questions players answer before a match.

Answers feed the tournament's seed rules (``when.answer`` and the
``from_answer`` outcome). Only choices staff listed can be answered; nothing
is free-typed. Answers lock once the match's seed rolls.
"""

from enum import Enum

from tortoise import fields
from tortoise.models import Model


class MatchFormKind(str, Enum):
    YES_NO = 'yes_no'
    CHOICE_ONE = 'choice_one'
    CHOICE_MANY = 'choice_many'
    NUMBER = 'number'


class MatchFormWho(str, Enum):
    """Who in a match answers: every player, or one seat."""
    EACH_PLAYER = 'each_player'
    HIGHER_SEED = 'higher_seed'
    PREVIOUS_LOSER = 'previous_loser'


class MatchFormAskOn(str, Enum):
    """Which matches show the question: all of them, or only where a rule that reads it could fire."""
    EVERY_MATCH = 'every_match'
    WHEN_USED = 'when_used'


class MatchFormVisibility(str, Enum):
    """When the other players see an answer."""
    RIGHT_AWAY = 'right_away'
    AT_ROLL = 'at_roll'
    NEVER = 'never'


class MatchFormQuestion(Model):
    """One question on a tournament's match form.

    ``key`` is what seed rules name, so it survives reordering and rewording.
    ``choices`` is ``[{"value": "a1", "label": "Open", "preset": 12}]`` for the
    choice kinds (``preset`` optional, for a rule that rolls the answer) and
    ``{"min": 0, "max": 10}`` bounds live in ``config`` for a number.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='match_form_questions', on_delete=fields.CASCADE,
    )
    tournament: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tournament', related_name='form_questions', on_delete=fields.CASCADE,
    )
    key = fields.CharField(max_length=16)
    position = fields.IntField(default=0)
    kind = fields.CharEnumField(MatchFormKind, max_length=16)
    prompt = fields.CharField(max_length=200)
    help = fields.TextField(null=True)
    choices = fields.JSONField(default=list)  # type: ignore[var-annotated]
    config = fields.JSONField(default=dict)  # type: ignore[var-annotated]
    who = fields.CharEnumField(MatchFormWho, max_length=16, default=MatchFormWho.EACH_PLAYER)
    ask_on = fields.CharEnumField(MatchFormAskOn, max_length=16, default=MatchFormAskOn.EVERY_MATCH)
    visibility = fields.CharEnumField(MatchFormVisibility, max_length=16, default=MatchFormVisibility.RIGHT_AWAY)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'matchformquestion'
        unique_together = (('tournament', 'key'),)
        ordering = ['position', 'id']


class MatchFormAnswer(Model):
    """One person's answer to one question on one match."""

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='match_form_answers', on_delete=fields.CASCADE,
    )
    match: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Match', related_name='form_answers', on_delete=fields.CASCADE,
    )
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='match_form_answers', on_delete=fields.CASCADE,
    )
    question: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.MatchFormQuestion', related_name='answers', on_delete=fields.CASCADE,
    )
    value = fields.JSONField()  # type: ignore[var-annotated]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'matchformanswer'
        unique_together = (('match', 'user', 'question'),)
