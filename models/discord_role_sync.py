from typing import Optional

from tortoise import fields
from tortoise.models import Model

from .enums import CheckInTierLanyard, DiscordRoleRuleKind


class DiscordRoleRule(Model):
    """One Discord role Wizzrobe hands out in its community's server.

    The reverse of :class:`DiscordRoleMapping`: there a Discord role grants an
    app role, here something Wizzrobe knows (enrolled in a tournament, in a
    match right now, holding a badge) earns a Discord role. ``key`` is the
    rule's identity within the tenant (``any_tournament``, ``live_match``,
    ``tournament:<id>``, ``badge:<lanyard>``), so the unique constraint holds
    where a nullable ``(kind, tournament, lanyard)`` triple would not.

    ``problem`` is why the bot can't hand the role out (missing Manage Roles,
    role above the bot's, a managed role), written by every sync and cleared
    once it can; a rule with a problem is skipped rather than retried per
    person. ``holding`` is how many qualifying people held the role at the last
    sync.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField('models.Tenant', related_name='discord_role_rules', on_delete=fields.CASCADE)
    tenant_id: int
    key = fields.CharField(max_length=64)
    kind = fields.CharEnumField(DiscordRoleRuleKind, max_length=32)
    tournament: fields.ForeignKeyNullableRelation = fields.ForeignKeyField(
        'models.Tournament', related_name='discord_role_rules', null=True, on_delete=fields.CASCADE,
    )
    tournament_id: Optional[int]
    lanyard = fields.CharEnumField(CheckInTierLanyard, max_length=16, null=True)
    discord_role_id = fields.BigIntField()
    discord_role_name = fields.CharField(max_length=100)
    problem = fields.TextField(null=True)
    holding = fields.IntField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'discordrolerule'
        unique_together = (('tenant', 'key'),)


class DiscordRoleGrant(Model):
    """A Discord role Wizzrobe itself put on someone.

    The ledger behind "only take away roles we gave": a role a moderator handed
    out by hand never gets a row, so the sync never removes it. A row is written
    only after Discord confirms the add and deleted only after it confirms the
    removal (or the person or role is gone), so an outage mid-sync leaves the
    ledger true.
    """

    id = fields.IntField(pk=True)
    tenant: fields.ForeignKeyRelation = fields.ForeignKeyField('models.Tenant', related_name='discord_role_grants', on_delete=fields.CASCADE)
    tenant_id: int
    user: fields.ForeignKeyRelation = fields.ForeignKeyField('models.User', related_name='discord_role_grants', on_delete=fields.CASCADE)
    user_id: int
    discord_role_id = fields.BigIntField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = 'discordrolegrant'
        unique_together = (('tenant', 'user', 'discord_role_id'),)


class DiscordRoleSyncState(Model):
    """How the community's last Discord role sync went, for the settings card.

    ``unreachable`` lists the qualifying people the sync couldn't reach, as
    ``{"user_id", "name", "reason"}`` with reason ``no_discord`` or
    ``not_in_server`` (capped, see the service). ``last_error`` is set when the
    whole run stopped (Discord down, no server linked) and cleared by the next
    clean one; ``last_synced_at`` only moves on a run that finished.
    """

    id = fields.IntField(pk=True)
    tenant: fields.OneToOneRelation = fields.OneToOneField('models.Tenant', related_name='discord_role_sync_state', on_delete=fields.CASCADE)
    tenant_id: int
    last_synced_at = fields.DatetimeField(null=True)
    last_error = fields.TextField(null=True)
    in_place = fields.IntField(default=0)
    added = fields.IntField(default=0)
    removed = fields.IntField(default=0)
    # mypy cannot infer a JSONField's type parameter, as with every sibling.
    unreachable = fields.JSONField(default=list)  # type: ignore[var-annotated]
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = 'discordrolesyncstate'
