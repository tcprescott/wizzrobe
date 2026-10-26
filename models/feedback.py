from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import FeedbackCategory, FeedbackStatus


class Feedback(Model):
    """An in-app feedback submission from a logged-in attendee.

    Captures the submitting ``user``, a free-text ``message`` and ``category``,
    and the ``page_url`` (path + query, e.g. ``/admin/reports?report=capacity``)
    the user was on when they submitted, so staff have the context to act on it.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='feedback', on_delete=fields.CASCADE)
    # Which event this happened during, for filtering. The row belongs to the
    # community, so it outlives the event (SET NULL) and may carry none.
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='feedback', on_delete=fields.SET_NULL, null=True,
    )
    event_id: Optional[int]
    user = fields.ForeignKeyField('models.User', related_name='feedback_submissions', on_delete=fields.CASCADE)
    category = fields.CharEnumField(FeedbackCategory, default=FeedbackCategory.OTHER, max_length=20)
    message = fields.TextField()
    page_url = fields.CharField(max_length=512)
    status = fields.CharEnumField(FeedbackStatus, default=FeedbackStatus.NEW, max_length=20)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'feedback'
