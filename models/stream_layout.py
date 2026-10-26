from typing import Optional

from tortoise import fields
from tortoise.models import Model


class StreamLayout(Model):
    """A broadcast overlay design for one event: background art plus data boxes.

    OBS loads the rendered page as a browser source on top of its video, so
    the page is transparent wherever the art is (or wherever a ``hole`` box cuts
    it), and the feeds underneath show through. ``boxes`` is the list of placed
    boxes in the art's own pixel space; :mod:`application.services.stream_layout_rules`
    owns its shape and validates every write.

    The art itself lives in :class:`StreamLayoutArt` so listing layouts never
    loads megabytes of image; ``art_sha256`` names the bytes, and the overlay's
    image URL embeds it so a new upload is a new URL.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='stream_layouts', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='stream_layouts', on_delete=fields.CASCADE,
    )
    event_id: int
    name = fields.CharField(max_length=80)
    width = fields.IntField(default=1920)
    height = fields.IntField(default=1080)
    boxes = fields.JSONField(default=list)  # type: ignore[var-annotated]
    art_sha256 = fields.CharField(max_length=64, null=True)
    created_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='stream_layouts_created', null=True, on_delete=fields.SET_NULL,
    )
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'stream_layout'
        unique_together = (('event', 'name'),)

    def __str__(self) -> str:
        return self.name


class StreamLayoutArt(Model):
    """The background image of one :class:`StreamLayout`, as a PNG we encoded."""

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='stream_layout_art', on_delete=fields.CASCADE,
    )
    tenant_id: int
    layout: fields.OneToOneRelation = fields.OneToOneField(
        'models.StreamLayout', related_name='art', on_delete=fields.CASCADE,
    )
    layout_id: int
    sha256 = fields.CharField(max_length=64)
    content = fields.BinaryField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'stream_layout_art'


class StageOverlay(Model):
    """A stage's OBS overlay: which layout it shows, its secret URL, and its pin.

    The overlay follows the stage, showing whatever match is on it, unless a
    Broadcast Tech or staff member pins one (``pinned_match``). OBS can't sign
    in, so the URL carries ``token``; it opens nothing but this stage's public
    broadcast data, and a new one can be issued at any time to retire the old.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Tenant', related_name='stage_overlays', on_delete=fields.CASCADE,
    )
    tenant_id: int
    event: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Event', related_name='stage_overlays', on_delete=fields.CASCADE, null=True,
    )
    event_id: Optional[int]
    stage: fields.OneToOneRelation = fields.OneToOneField(
        'models.Stage', related_name='overlay', on_delete=fields.CASCADE,
    )
    stage_id: int
    layout: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.StreamLayout', related_name='stage_overlays', null=True, on_delete=fields.SET_NULL,
    )
    layout_id: Optional[int]
    token = fields.CharField(max_length=64, unique=True)
    pinned_match: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Match', related_name='overlay_pins', null=True, on_delete=fields.SET_NULL,
    )
    pinned_match_id: Optional[int]
    pinned_by: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.User', related_name='overlay_pins_made', null=True, on_delete=fields.SET_NULL,
    )
    pinned_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'stage_overlay'
