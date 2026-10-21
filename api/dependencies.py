"""Shared FastAPI dependencies and error handling for the REST API.

Authentication is via a personal bearer token (``Authorization: Bearer
wizzrobe_pat_...``) generated on the user's profile page. A token resolves to
its owning :class:`User` and the request then runs the *same* service-layer
permission checks as the web UI. A token flagged ``read_only`` may only call
read (GET) endpoints. A third-party app's token is further limited to the
scopes its user approved (:mod:`api.scopes`).

``ServiceErrorRoute`` translates the service layer's ``PermissionError`` /
``ValueError`` into ``403`` / ``400`` responses. It is scoped to the API
routers (via ``route_class``) so it never affects the NiceGUI frontend mounted
on the same app.
"""

from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.scopes import scope_for
from application.api_scopes import ApiScope, parse_scope
from application.errors import NotFoundError
from application.services.api_token_service import ApiTokenService
from application.services.auth_service import AuthService
from application.services.feature_flag_service import FeatureFlagService
from application.services.tenant_service import TenantService
from application.services.timezone_service import TimezoneService
from application.tenant_context import reset_tenant_id, set_tenant_id
from application.timezone_context import set_timezone_name
from models import ApiToken, ApiTokenOrigin, FeatureFlag, User


async def tenant_context_scope():
    """Router-level dependency that baselines and resets the tenant contextvar.

    The API is excluded from ``TenantMiddleware`` (it derives its tenant from the
    bearer token, not the URL), so there is no middleware to reset the context.
    This sets a clean ``None`` baseline before token resolution and restores it in
    a ``finally`` after the response, so a token-derived tenant never leaks to the
    next request handled on a reused task.
    """
    token = set_tenant_id(None)
    try:
        yield
    finally:
        reset_tenant_id(token)

# auto_error=False so a missing/malformed header yields our own 401 (FastAPI's
# default for HTTPBearer is a 403), keeping all auth failures consistent.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Personal API token generated on your profile page.",
)


def _require_scope(request: Request, token: ApiToken) -> None:
    """Refuse a scoped token whose grant doesn't cover this route.

    A PAT is unscoped: it acts with everything its owner may do, exactly as
    before scopes existed. Every other origin that gets this far (an app token)
    is limited to the scopes on its grant, and a route missing from the table
    is refused rather than assumed open.
    """
    if token.origin == ApiTokenOrigin.PAT.value:
        return
    route = request.scope.get('route')
    path = getattr(route, 'path', '').removeprefix('/api')
    required = scope_for(request.method, path)
    if isinstance(required, ApiScope):
        if required in parse_scope(token.scope):
            return
        detail = f"This app wasn't given the {required.value} scope"
        challenge = f'Bearer error="insufficient_scope", scope="{required.value}"'
    else:
        detail = "Apps can't use this endpoint; it needs a personal API token"
        challenge = 'Bearer error="insufficient_scope"'
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
        headers={"WWW-Authenticate": challenge},
    )


def _reject_read_only(token: ApiToken) -> None:
    """Raise ``403`` if the token may not perform write actions."""
    if token.read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This token is read-only and cannot perform write actions",
        )


async def resolve_token(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Tuple[User, ApiToken]:
    """Resolve and authenticate the bearer token, returning ``(user, token)``.

    Declared as a FastAPI dependency so its result is *cached per request*: an
    endpoint that gates on both a router-level actor dep and a per-endpoint
    write dep authenticates the token (and its tenant lookup) exactly once.
    """
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        result = await ApiTokenService().resolve_actor(creds.credentials)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user, token = result
    # An OAuth token belongs to the MCP server: it is platform-wide (no tenant),
    # and its holder consented to that surface, not to this one. Refusing it
    # here — and refusing a PAT at /mcp — means a credential minted for one
    # surface can never be replayed against the other.
    #
    # More load-bearing than it looks now that an MCP grant can carry write
    # access: an OAuth token has tenant=NULL, and everything below this line
    # derives the request's community from token.tenant_id. A write-capable
    # tenant-less credential reaching that code has no community to act in.
    if token.origin == ApiTokenOrigin.OAUTH.value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This token is for the MCP server and cannot be used with the REST API",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _require_scope(request, token)
    # A PAT acts within exactly one tenant: set the request tenant context from
    # it (the token was resolved globally, before any context existed), so the
    # same tenant-aware service checks the web UI uses apply here.
    tenant = await TenantService.get_by_id(token.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This tenant is inactive",
        )
    # Removal revokes a member's tokens, but a token can outlive membership by
    # other routes (removed before that existed, a super-admin who lost the
    # role), so the check that decides it runs on every request.
    if not await TenantService.is_member(user.id, tenant.id) and not await AuthService.is_super_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this community",
        )
    set_tenant_id(tenant.id)
    # REST responses stay UTC — timezone is a presentation concern and a
    # programmatic client should get one canonical instant. But a few endpoints
    # accept or derive a calendar *date*, and a date has no meaning without a
    # zone; those resolve on the community's clock, matching what its members
    # see, rather than silently on UTC's.
    set_timezone_name(await TimezoneService.tenant_timezone_name(tenant.id))
    return user, token


async def require_api_actor(
    resolved: Tuple[User, ApiToken] = Depends(resolve_token),
) -> User:
    """Resolve the authenticated user from the bearer token (any token)."""
    user, _ = resolved
    return user


async def require_write_actor(
    resolved: Tuple[User, ApiToken] = Depends(resolve_token),
) -> User:
    """Resolve the user and reject read-only tokens. Use on mutating routes."""
    user, token = resolved
    _reject_read_only(token)
    return user


async def require_admin(actor: User = Depends(require_api_actor)) -> User:
    """Authenticated user who can read the community's reports: staff, or a
    tournament admin or crew coordinator here (``can_view_admin``)."""
    await AuthService.ensure(
        await AuthService.can_view_admin(actor), "Admin access required"
    )
    return actor


async def require_volunteer_admin(actor: User = Depends(require_api_actor)) -> User:
    """``require_admin`` plus a volunteer coordinator, for the admin-side volunteer reads."""
    await AuthService.ensure(
        await AuthService.can_view_volunteer_admin(actor), "Admin access required"
    )
    return actor


async def require_staff(actor: User = Depends(require_api_actor)) -> User:
    """Authenticated user holding STAFF in this community (or a super-admin)."""
    await AuthService.ensure(
        await AuthService.is_staff(actor), "Staff access required"
    )
    return actor


async def require_staff_write(
    resolved: Tuple[User, ApiToken] = Depends(resolve_token),
) -> User:
    """Staff user with a non-read-only token. Use on mutating Staff-only routes
    so the HTTP-layer gate matches the documented contract while still rejecting
    read-only tokens (defense in depth alongside the service-layer check)."""
    user, token = resolved
    _reject_read_only(token)
    await AuthService.ensure(
        await AuthService.is_staff(user), "Staff access required"
    )
    return user


async def require_super_admin(actor: User = Depends(require_api_actor)) -> User:
    """Authenticated user holding the global ``SUPER_ADMIN`` role.

    Unlike :func:`require_staff` (which is tenant-scoped), this gates on the
    platform-global role (``UserRole`` with ``tenant=None``). Use it for global,
    non-tenant-scoped resources such as :class:`RacetimeBot` and the full
    service-health board, whose services do not (or cannot) apply a tenant gate.
    """
    await AuthService.ensure(
        await AuthService.is_super_admin(actor), "Super admin access required"
    )
    return actor


async def require_super_admin_write(
    resolved: Tuple[User, ApiToken] = Depends(resolve_token),
) -> User:
    """Super-admin user with a non-read-only token. Use on mutating global routes."""
    user, token = resolved
    _reject_read_only(token)
    await AuthService.ensure(
        await AuthService.is_super_admin(user), "Super admin access required"
    )
    return user


def require_feature(flag: FeatureFlag):
    """Router dependency factory that 404s when the tenant lacks a feature flag.

    Depends on :func:`require_api_actor` so the bearer token has resolved and set
    the tenant context before the flag is read. A subsystem the tenant hasn't
    enabled is hidden (404) — the REST mirror of the web ``@protected_page``
    feature gate — so the API can't be used to reach a feature the community
    turned off. Attach it via ``include_router(..., dependencies=[...])``.
    """

    async def _require_feature(actor: User = Depends(require_api_actor)) -> None:
        if not await FeatureFlagService().is_enabled(flag):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="This feature is not enabled for this community.",
            )

    _require_feature.feature_flag = flag  # type: ignore[attr-defined]  # read by api.openapi_docs
    return _require_feature


class ServiceErrorRoute(APIRoute):
    """Translate service-layer exceptions into HTTP responses.

    - ``PermissionError`` -> 403
    - ``NotFoundError``   -> 404 (checked before ``ValueError``, which it subclasses)
    - ``ValueError``      -> 400
    """

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except PermissionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=str(exc) or "Permission denied",
                ) from exc
            except NotFoundError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=str(exc) or "Not found",
                ) from exc
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc

        return custom_handler
