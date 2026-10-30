"""Actor resolution and per-call authorization for MCP tools.

Two halves, deliberately split:

* The **connection** identifies a user. ``mcpserver/asgi.py`` authenticates the
  bearer token once per HTTP request and stashes the result in ``_actor_var``.
* The **call** names a community. Every tenant-scoped tool takes a ``tenant``
  slug, and :func:`require_actor` resolves it, binds it, and runs the
  authorization gate inside that binding.

That split is forced by the credential: an OAuth token is platform-wide
(``tenant=NULL``), so there is no community to bind at connection time. It is
also what makes a single ``/mcp`` endpoint able to serve a user who staffs
several communities without reconnecting.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from application.api_scopes import CONSENT_WORDING, ApiScope
from application.errors import NotFoundError
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.mcp_auth_service import grant_scopes
from application.services.tenant_service import TenantService
from application.tenant_context import require_tenant_id
from models import ApiToken, FeatureFlag, Tenant, User


@dataclass(frozen=True)
class McpActor:
    """Who is behind the current MCP request."""

    user: User
    token: ApiToken


_actor_var: ContextVar[Optional[McpActor]] = ContextVar('mcp_actor', default=None)


def set_actor(actor: McpActor) -> Token:
    return _actor_var.set(actor)


def reset_actor(token: Token) -> None:
    _actor_var.reset(token)


def optional_actor() -> Optional[McpActor]:
    """The actor, or ``None`` when there is none.

    Only for the places where absence is a real state rather than a bug — the
    tool listing, which the SDK also builds outside a request while wiring the
    server up. Tools use :func:`current_actor`.
    """
    return _actor_var.get()


def current_actor() -> McpActor:
    """The authenticated actor for this request.

    Raises rather than returning ``None``: reaching a tool with no actor means
    the ASGI wrapper did not run, which is a wiring bug, not a permission
    outcome. Failing loudly here is the same instinct as ``require_tenant_id()``.
    """
    actor = _actor_var.get()
    if actor is None:
        raise RuntimeError(
            'No MCP actor in context — a tool ran outside McpEndpoint. '
            'Tools must be reached through the /mcp route, which authenticates first.'
        )
    return actor


class Gate(Enum):
    """The authorization each tool requires, mirroring its REST counterpart.

    ``GLOBAL`` is not a weaker ``ACTOR`` — it marks a tool that names no
    community at all (``whoami``, ``list_tenants``), so there is nothing to
    scope and no tenant-scoped role to check.

    ``PLATFORM`` also names no community, and requires the global super-admin
    role: the ``/platform`` page's surface, mirroring ``require_super_admin``.

    ``VOLUNTEER_ADMIN`` is ``ADMIN`` plus a volunteer coordinator, for the
    volunteer reads that coordinator manages in Admin (mirroring
    ``require_volunteer_admin``).
    """

    GLOBAL = 'global'
    PLATFORM = 'platform'
    ACTOR = 'actor'
    ADMIN = 'admin'
    VOLUNTEER_ADMIN = 'volunteer_admin'
    STAFF = 'staff'

    @property
    def names_no_community(self) -> bool:
        """Whether the tool takes no ``tenant``, so there is nothing to bind."""
        return self in (Gate.GLOBAL, Gate.PLATFORM)


async def belongs_to_tenant(user: User) -> bool:
    """Whether ``user`` belongs to the community in scope: a member, or a role holder.

    Membership is the bar, the same one the web app's join door uses, so a
    regular player reaches their community here exactly as they do in a browser.
    A role implies membership, so the role check only covers rows that predate
    that invariant.
    """
    if await TenantService.is_member(user.id, require_tenant_id()):
        return True
    return await AuthService.holds_any_role(user)


async def resolve_tenant(slug: Optional[str]) -> Optional[Tenant]:
    """Resolve a tool's ``tenant`` slug argument to a community.

    An unknown slug, an inactive one, and a missing argument all produce the
    same ``NotFoundError`` text on purpose: a caller must not be able to probe
    which communities exist by diffing error messages.
    """
    if not slug:
        raise NotFoundError(
            'This tool needs a community: pass tenant=<slug> from list_tenants.'
        )
    tenant = await TenantService.get_by_slug(slug)
    if tenant is None or not tenant.is_active:
        raise NotFoundError(f"No community '{slug}' is available.")
    return tenant


async def authorize(
    actor: McpActor,
    gate: Gate,
    feature: Optional[FeatureFlag],
    slug: str = '',
    *,
    write: bool = False,
    scope: Optional[ApiScope] = None,
) -> None:
    """Run the membership floor, the feature gate, the connection gate, then the role gate.

    Order matters: a subsystem the community has not enabled is hidden from
    everyone who can see the community at all, so the flag is checked before the
    role. Otherwise a 403 would tell an unauthorized caller that the feature
    exists here, and a staff member would get a different answer than a player
    for a feature that is simply off.
    """
    # NotFoundError worded like an unknown slug, never PermissionError: a distinct refusal would enumerate communities.
    if not gate.names_no_community and not await AuthService.is_super_admin(actor.user):
        if not await belongs_to_tenant(actor.user):
            raise NotFoundError(f"No community '{slug}' is available.")

    # Must run after the floor: "not enabled here" would confirm the community exists.
    if feature is not None and not await FeatureFlagService().is_enabled(feature):
        raise NotFoundError('This feature is not enabled for this community.')

    # After the floor, before the role gate: the two refusals need different fixes from the user.
    if write and actor.token.read_only:
        raise PermissionError(
            'This connection was approved for reading only. Reconnect from your '
            'MCP client and tick the change this tool makes.'
        )
    if scope is not None and scope not in grant_scopes(actor.token.scope):
        raise PermissionError(
            f'This connection was not allowed to "{CONSENT_WORDING[scope].lower()}". '
            'Reconnect from your MCP client and tick it to use this tool.'
        )

    if gate is Gate.ADMIN:
        await AuthService.ensure(
            await AuthService.can_view_admin(actor.user), 'Admin access required'
        )
    elif gate is Gate.VOLUNTEER_ADMIN:
        await AuthService.ensure(
            await AuthService.can_view_volunteer_admin(actor.user), 'Admin access required'
        )
    elif gate is Gate.STAFF:
        await AuthService.ensure(
            await AuthService.is_staff(actor.user), 'Staff access required'
        )
    elif gate is Gate.PLATFORM:
        await AuthService.ensure(
            await AuthService.is_super_admin(actor.user), 'Super admin access required'
        )

