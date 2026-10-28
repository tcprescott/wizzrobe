from enum import Enum
from typing import Any, Optional

from tortoise import fields
from tortoise.models import Model


class SurveyStatus(str, Enum):
    """Where a post-event survey stands.

    ``DRAFT`` while staff build it (only staff, through the preview, see it);
    ``OPEN`` once sent, taking answers until ``closes_at`` or staff close it;
    ``CLOSED`` after that, read-only. Sending is the one way out of ``DRAFT``,
    and a closed survey doesn't reopen: send a new one.
    """

    DRAFT = 'draft'
    OPEN = 'open'
    CLOSED = 'closed'


class SurveyAudience(str, Enum):
    """Who a survey section is for, decided from what each person did.

    ``EACH_TOURNAMENT`` and ``EACH_POSITION`` repeat: a player in three
    tournaments answers the section three times, once per tournament, and a
    volunteer once per position they worked.
    """

    EVERYONE = 'everyone'
    EACH_TOURNAMENT = 'each_tournament'
    EACH_POSITION = 'each_position'
    CREW = 'crew'
    # One crew role each, so commentators, trackers and broadcasters get their own questions.
    COMMENTATORS = 'commentators'
    TRACKERS = 'trackers'
    BROADCASTERS = 'broadcasters'
    CHECKED_IN = 'checked_in'


class SurveyQuestionKind(str, Enum):
    """How a survey question is answered."""

    RATING = 'rating'          # 1 to 5
    NPS = 'nps'                # 0 to 10, "would you recommend"
    YES_NO = 'yes_no'
    CHOICE_ONE = 'choice_one'
    CHOICE_MANY = 'choice_many'
    COMMENT = 'comment'        # short free text, sanitized player input
    AGREE_SCALE = 'agree_scale'  # 2 to 10 statements, each Strongly disagree (1) to Strongly agree (5)


class SurveyResponseStatus(str, Enum):
    """``DRAFT`` while a respondent works through the parts (answers save as
    they go); ``SUBMITTED`` once they send it. Results only read submitted ones."""

    DRAFT = 'draft'
    SUBMITTED = 'submitted'


class Survey(Model):
    """A post-event survey: staff build it, send it by DM, and read the answers.

    Belongs to one event. Who gets it, and which sections each person sees, is
    worked out once at send time from what they did at the event (see
    :class:`SurveyRecipient`). ``allow_anonymous`` offers respondents a switch
    that strips their name from what they send.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='surveys', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='surveys', on_delete=fields.CASCADE,
    )
    event_id: int
    title = fields.CharField(max_length=120)
    intro = fields.TextField(default='')
    status = fields.CharEnumField(SurveyStatus, max_length=16, default=SurveyStatus.DRAFT)
    allow_anonymous = fields.BooleanField(default=True)
    closes_at = fields.DatetimeField(null=True)
    sent_at = fields.DatetimeField(null=True)
    reminded_at = fields.DatetimeField(null=True)
    reminders_sent = fields.SmallIntField(default=0)
    closed_at = fields.DatetimeField(null=True)
    # SET_NULL: the survey outlives the accounts that built or sent it.
    created_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='surveys_created', null=True, on_delete=fields.SET_NULL,
    )
    created_by_id: Optional[int]
    sent_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='surveys_sent', null=True, on_delete=fields.SET_NULL,
    )
    sent_by_id: Optional[int]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    sections: fields.ReverseRelation['SurveySection']

    class Meta:
        table = 'survey'
        indexes = (('tenant', 'event', 'status'),)


class SurveySection(Model):
    """A titled group of questions shown to one audience.

    A repeating audience (each tournament played, each position worked) shows
    the section once per tournament or position, titled with its name.
    ``conditions`` narrows who sees it further, in the same shape as a
    question's (``application/services/survey/conditions.py``).
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='survey_sections', on_delete=fields.CASCADE,
    )
    tenant_id: int
    survey: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Survey', related_name='sections', on_delete=fields.CASCADE,
    )
    survey_id: int
    title = fields.CharField(max_length=120)
    # One or two plain-text lines under the title, setting the context.
    description = fields.CharField(max_length=240, null=True)
    audience = fields.CharEnumField(SurveyAudience, max_length=24, default=SurveyAudience.EVERYONE)
    position = fields.IntField(default=0)
    conditions = fields.JSONField(null=True)  # type: ignore[var-annotated]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    questions: fields.ReverseRelation['SurveyQuestion']

    class Meta:
        table = 'survey_section'
        ordering = ('position', 'id')


class SurveyQuestion(Model):
    """One question in a section.

    ``options`` holds the choices of a pick-one or pick-many question, or an
    agree scale's statements. A choice question can add an ``Other`` choice
    with a write-in (``other_option``, the text stored in the answer's
    ``text``), and a pick-many an exclusive "none" choice (``none_option``,
    its label) and pick limits. ``long_answer`` makes a comment a tall box.
    ``conditions`` lists the registered conditions (``application/services/
    survey/conditions.py``) the respondent must all meet to see it, each
    ``{'key': …}`` plus, for an answer-based one, the earlier question and
    the answers that show it.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='survey_questions', on_delete=fields.CASCADE,
    )
    tenant_id: int
    section: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.SurveySection', related_name='questions', on_delete=fields.CASCADE,
    )
    section_id: int
    prompt = fields.CharField(max_length=280)
    kind = fields.CharEnumField(SurveyQuestionKind, max_length=16)
    options = fields.JSONField(null=True)  # type: ignore[var-annotated]
    required = fields.BooleanField(default=False)
    other_option = fields.BooleanField(default=False)
    none_option = fields.CharField(max_length=60, null=True)
    min_picks = fields.SmallIntField(null=True)
    max_picks = fields.SmallIntField(null=True)
    long_answer = fields.BooleanField(default=False)
    position = fields.IntField(default=0)
    conditions = fields.JSONField(null=True)  # type: ignore[var-annotated]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'survey_question'
        ordering = ('position', 'id')


class SurveyRecipient(Model):
    """Someone the survey was sent to, and what they did at the event.

    ``facts`` is worked out once at send time — the tournaments they played (and
    whether each had a match on a stage), the positions they worked, whether they
    crewed or checked in, and each registered condition's answer — so the survey
    shows the same sections however the event's data changes afterwards.

    It records *that* someone answered (``submitted_at``), never *what*: an
    anonymous :class:`SurveyResponse` carries no link back to this row.
    ``opened_at`` and ``submitted_at`` hold the day only (midnight UTC), so
    they can't be lined up with when an anonymous response was written.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='survey_recipients', on_delete=fields.CASCADE,
    )
    tenant_id: int
    survey: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Survey', related_name='recipients', on_delete=fields.CASCADE,
    )
    survey_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.User', related_name='survey_invitations', on_delete=fields.CASCADE,
    )
    user_id: int
    facts = fields.JSONField(default=dict)  # type: ignore[var-annotated]
    dm_sent_at = fields.DatetimeField(null=True)
    opened_at = fields.DatetimeField(null=True)
    submitted_at = fields.DatetimeField(null=True)
    # How far they got while they haven't sent it, for the drop-off card: part
    # keys opened and saved, and the last one opened; never what they answered.
    # Submitting folds this into :class:`SurveyPartTally` and clears it, so
    # which parts someone skipped can't be matched to an anonymous response.
    progress = fields.JSONField(default=dict)  # type: ignore[var-annotated]
    last_part = fields.CharField(max_length=64, null=True)
    # When they first saved a part, kept only until they submit: it becomes
    # ``finish_seconds`` and is cleared, so no exact time outlives the draft.
    started_at = fields.DatetimeField(null=True)
    finish_seconds = fields.IntField(null=True)
    # The day they pressed "Not interested" on a survey DM: no more reminders.
    declined_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'survey_recipient'
        unique_together = (('survey', 'user'),)


class SurveyPartTally(Model):
    """How many people who sent the survey opened and answered one part.

    Bumped once per submit from that person's progress, which is then cleared:
    a running count, with no row per person to line up with a response.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='survey_part_tallies', on_delete=fields.CASCADE,
    )
    tenant_id: int
    survey: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Survey', related_name='part_tallies', on_delete=fields.CASCADE,
    )
    survey_id: int
    section: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.SurveySection', related_name='tallies', on_delete=fields.CASCADE,
    )
    section_id: int
    scope = fields.CharField(max_length=24, default='')
    scope_label = fields.CharField(max_length=255, default='')
    opened = fields.IntField(default=0)
    answered = fields.IntField(default=0)

    class Meta:
        table = 'survey_part_tally'
        unique_together = (('survey', 'section', 'scope'),)


class SurveyResponse(Model):
    """One person's answers.

    While a respondent works through the parts the draft is theirs (``user``
    set) so they can come back to it. Submitting anonymously clears ``user``
    for good and keeps only ``groups`` (the tournaments and positions it was
    about), and ``submitted_on`` keeps the day, not the time, so the row can't be
    matched to a recipient by when it arrived.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='survey_responses', on_delete=fields.CASCADE,
    )
    tenant_id: int
    survey: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Survey', related_name='responses', on_delete=fields.CASCADE,
    )
    survey_id: int
    user: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='survey_responses', null=True, on_delete=fields.SET_NULL,
    )
    user_id: Optional[int]
    status = fields.CharEnumField(SurveyResponseStatus, max_length=16, default=SurveyResponseStatus.DRAFT)
    anonymous = fields.BooleanField(default=False)
    groups = fields.JSONField(default=list)  # type: ignore[var-annotated]
    submitted_on = fields.DateField(null=True)

    answers: fields.ReverseRelation['SurveyAnswer']

    class Meta:
        table = 'survey_response'
        indexes = (('survey', 'status'),)


class SurveyAnswer(Model):
    """One answer to one question, for one repeat of its section.

    ``scope`` names the repeat (``t:12`` for tournament 12, ``p:5`` for position
    5, ``''`` for a section that doesn't repeat) and ``scope_label`` its name as
    the respondent saw it. :attr:`value` is the number, choice or list of
    choices; ``text`` the comment, stored as plain text and only ever rendered
    as such.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='survey_answers', on_delete=fields.CASCADE,
    )
    tenant_id: int
    response: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.SurveyResponse', related_name='answers', on_delete=fields.CASCADE,
    )
    response_id: int
    question: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.SurveyQuestion', related_name='answers', on_delete=fields.CASCADE,
    )
    question_id: int
    scope = fields.CharField(max_length=24, default='')
    scope_label = fields.CharField(max_length=255, default='')
    # ``{"v": answer}``: Tortoise reads a bare string in a JSONField as JSON
    # text, so a chosen option ("Yes") can't be stored on its own.
    data = fields.JSONField(null=True)  # type: ignore[var-annotated]
    text = fields.TextField(null=True)

    @property
    def value(self) -> Any:
        return (self.data or {}).get('v')

    class Meta:
        table = 'survey_answer'
        unique_together = (('response', 'question', 'scope'),)


class SurveySuggestionBatch(Model):
    """One press of "Suggest questions" on a section: what was sent, and the drafts that came back.

    ``context`` is the exact text the AI provider was given, kept so staff can
    read it back ("What was sent"). ``drafts`` is a list of
    ``{"prompt", "kind", "options", "state"}``; a draft only becomes a
    :class:`SurveyQuestion` when staff keep it (``state`` ``kept``), and
    dismissing the batch sets ``dismissed_at``. Rows stay after that: they are
    also the community's usage log for the daily cap.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='survey_suggestion_batches', on_delete=fields.CASCADE,
    )
    tenant_id: int
    survey: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Survey', related_name='suggestion_batches', on_delete=fields.CASCADE,
    )
    survey_id: int
    section: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.SurveySection', related_name='suggestion_batches', on_delete=fields.CASCADE,
    )
    section_id: int
    requested_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='survey_suggestion_batches', null=True, on_delete=fields.SET_NULL,
    )
    requested_by_id: Optional[int]
    provider = fields.CharField(max_length=32)
    model = fields.CharField(max_length=120)
    context = fields.TextField()
    drafts = fields.JSONField(default=list)  # type: ignore[var-annotated]
    input_tokens = fields.IntField(null=True)
    output_tokens = fields.IntField(null=True)
    dismissed_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'survey_suggestion_batch'
        indexes = (('tenant', 'created_at'), ('survey', 'section'))
