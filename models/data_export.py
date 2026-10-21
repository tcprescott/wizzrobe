from tortoise import fields
from tortoise.models import Model

from .enums import DataExportKind, DataExportStatus


class DataExport(Model):
    """One requested data export, built in the background.

    The export worker builds queued rows one at a time so a large export never
    runs inside a page request or competes with live traffic. The finished file
    lives on the row until the worker prunes it: ``content`` is the main
    download (named ``filename``, of type ``media_type``) and ``json_content``
    the machine-readable copy, for the kinds that have one. ``params`` is what
    the requester asked for; each kind's builder reads it.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='data_exports', on_delete=fields.CASCADE)
    requested_by = fields.ForeignKeyField(
        'models.User', related_name='data_exports', null=True, on_delete=fields.SET_NULL,
    )
    kind = fields.CharEnumField(DataExportKind, max_length=32)
    title = fields.CharField(max_length=255)
    params = fields.JSONField(default=dict)
    status = fields.CharEnumField(DataExportStatus, default=DataExportStatus.QUEUED, max_length=16)
    progress_done = fields.IntField(default=0)
    progress_total = fields.IntField(default=0)
    error = fields.CharField(max_length=500, null=True)
    filename = fields.CharField(max_length=255, null=True)
    media_type = fields.CharField(max_length=100, null=True)
    content = fields.BinaryField(null=True)
    json_content = fields.BinaryField(null=True)
    size_bytes = fields.IntField(default=0)
    created_at = fields.DatetimeField(auto_now_add=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)

    class Meta:
        table = 'dataexport'
        indexes = (('status', 'created_at'), ('requested_by', 'created_at'))
