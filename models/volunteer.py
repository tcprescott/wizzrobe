
from tortoise import fields
from tortoise.models import Model

from .enums import Role, VolunteerAvailabilityStatus


class VolunteerPosition(Model):
    """A coordinator-defined volunteer job (e.g. Check-in Desk, Race Proctor)."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='volunteer_positions', on_delete=fields.CASCADE)
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='volunteer_positions', on_delete=fields.CASCADE,
    )
    event_id: int
    name = fields.CharField(max_length=255)
    description = fields.TextField(null=True)
    color = fields.CharField(max_length=32, null=True)
    display_order = fields.IntField(default=0)
    is_active = fields.BooleanField(default=True)
    shift_length_minutes = fields.IntField(null=True)
    stagger_minutes = fields.IntField(null=True)
    staff_alerts = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    shifts = fields.ReverseRelation["VolunteerShift"]
    qualifications = fields.ReverseRelation["VolunteerQualification"]

    @property
    def is_staggered(self) -> bool:
        return bool(self.shift_length_minutes and self.stagger_minutes)

    class Meta:
        table = 'volunteerposition'
        unique_together = (('tenant', 'event', 'name'),)


class VolunteerShift(Model):
    """A fillable slot-set for a position over a time window (UTC)."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='volunteer_shifts', on_delete=fields.CASCADE)
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='volunteer_shifts', on_delete=fields.CASCADE,
    )
    event_id: int
    position = fields.ForeignKeyField('models.VolunteerPosition', related_name='shifts', on_delete=fields.CASCADE)
    starts_at = fields.DatetimeField(index=True)
    ends_at = fields.DatetimeField()
    label = fields.CharField(max_length=100, null=True)
    slots_needed = fields.IntField(default=1)
    notes = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    assignments = fields.ReverseRelation["VolunteerAssignment"]

    class Meta:
        table = 'volunteershift'
        indexes = (('position',),)


class VolunteerAssignment(Model):
    """A volunteer placed into a shift."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='volunteer_assignments', on_delete=fields.CASCADE)
    shift = fields.ForeignKeyField('models.VolunteerShift', related_name='assignments', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='volunteer_assignments', on_delete=fields.CASCADE)
    assigned_by = fields.ForeignKeyField('models.User', related_name='volunteer_assignments_made', null=True, on_delete=fields.SET_NULL)
    auto_generated = fields.BooleanField(default=False)
    acknowledged_at = fields.DatetimeField(null=True)
    reminder_sent_at = fields.DatetimeField(null=True)
    checked_in_at = fields.DatetimeField(null=True)
    checked_in_by = fields.ForeignKeyField('models.User', related_name='volunteer_check_ins', null=True, on_delete=fields.SET_NULL)
    start_dm_sent_at = fields.DatetimeField(null=True)
    late_minutes = fields.IntField(null=True)
    called_off_at = fields.DatetimeField(null=True)
    called_off_by = fields.ForeignKeyField('models.User', related_name='volunteer_call_offs', null=True, on_delete=fields.SET_NULL)
    called_off_note = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    @property
    def counts_as_worked(self) -> bool:
        """Checked in, or called off by staff: either way the shift is credited."""
        return self.checked_in_at is not None or self.called_off_at is not None

    class Meta:
        table = 'volunteerassignment'
        unique_together = (('shift', 'user'),)
        indexes = (('user',),)


class VolunteerQualification(Model):
    """Capability matrix: which positions a user can fill."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='volunteer_qualifications', on_delete=fields.CASCADE)
    user = fields.ForeignKeyField('models.User', related_name='volunteer_qualifications', on_delete=fields.CASCADE)
    position = fields.ForeignKeyField('models.VolunteerPosition', related_name='qualifications', on_delete=fields.CASCADE)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'volunteerqualification'
        unique_together = (('user', 'position'),)
        indexes = (('position',),)


class VolunteerRoleMapping(Model):
    """One volunteer position → one app role, held by anyone on its shifts.

    The volunteer-side sibling of ``DiscordRoleMapping``: a published assignment
    to any shift of ``position`` grants ``app_role`` (a ``UserRole`` row with
    ``RoleSource.VOLUNTEER``), and the grant lasts as long as at least one such
    assignment exists, past shifts included. Only ``Role.volunteer_mappable()``
    may be stored, because a coordinator, not staff, decides who is assigned.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.Tenant', related_name='volunteer_role_mappings', on_delete=fields.CASCADE,
    )
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='volunteer_role_mappings', on_delete=fields.CASCADE,
    )
    event_id: int
    position = fields.ForeignKeyField(  # type: ignore[var-annotated]
        'models.VolunteerPosition', related_name='role_mappings', on_delete=fields.CASCADE,
    )
    app_role = fields.CharEnumField(Role, max_length=32)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'volunteerrolemapping'
        unique_together = (('tenant', 'position', 'app_role'),)


class VolunteerAvailability(Model):
    """A window a volunteer self-declares (UTC)."""

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='volunteer_availability', on_delete=fields.CASCADE)
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='volunteer_availability', on_delete=fields.CASCADE,
    )
    event_id: int
    user = fields.ForeignKeyField('models.User', related_name='volunteer_availability', on_delete=fields.CASCADE)
    starts_at = fields.DatetimeField(index=True)
    ends_at = fields.DatetimeField()
    status = fields.CharEnumField(VolunteerAvailabilityStatus, default=VolunteerAvailabilityStatus.AVAILABLE, max_length=20)
    note = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'volunteeravailability'
        indexes = (('user',),)


class PlayerAvailability(Model):
    """A window a player spoke for (UTC).

    Read opt-out, unlike its volunteer sibling: a player is available for the
    whole event, so a row is a time they cannot play (UNAVAILABLE) or would
    rather (PREFERRED). No rows means fully available.
    """

    id = fields.IntField(pk=True)
    tenant = fields.ForeignKeyField('models.Tenant', related_name='player_availability', on_delete=fields.CASCADE)
    # Keep nullable: not every write path stamps it yet.
    event: fields.ForeignKeyRelation = fields.ForeignKeyField(
        'models.Event', related_name='player_availability', on_delete=fields.CASCADE,
    )
    event_id: int
    user = fields.ForeignKeyField('models.User', related_name='player_availability', on_delete=fields.CASCADE)
    starts_at = fields.DatetimeField(index=True)
    ends_at = fields.DatetimeField()
    status = fields.CharEnumField(VolunteerAvailabilityStatus, default=VolunteerAvailabilityStatus.AVAILABLE, max_length=20)
    note = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'playeravailability'
        indexes = (('user',),)
