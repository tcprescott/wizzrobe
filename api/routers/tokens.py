"""Personal API token self-management endpoints.

These let a user manage their own tokens programmatically. Creating or revoking
a token requires a *non* read-only token, so a read-only token can never be
used to mint a more privileged one.
"""

from typing import List, Tuple

from fastapi import APIRouter, Depends, status

from api.dependencies import (
    ServiceErrorRoute,
    require_api_actor,
    require_write_actor,
    resolve_token,
)
from api.schemas.tokens import (
    ApiTokenCreatedResponse,
    ApiTokenCreateRequest,
    ApiTokenResponse,
)
from application.services.api_token_service import ApiTokenService
from models import ApiToken, User

router = APIRouter(prefix="/tokens", tags=["API Tokens"], route_class=ServiceErrorRoute)


@router.get(
    "",
    response_model=List[ApiTokenResponse],
    summary="List your API tokens",
    description=(
        "Returns your tokens that haven't been revoked: the ones made in this community plus any "
        "app connections that work across communities. The secret is never included.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def list_tokens(actor: User = Depends(require_api_actor)):
    return await ApiTokenService().list_tokens(actor)


@router.post(
    "",
    response_model=ApiTokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API token",
    description=(
        "Creates a token that acts as you in this community. The response carries the secret "
        "once; copy it then, because it can't be shown again. Fails with 400 when the name is "
        "blank or the expiry isn't in the future.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def create_token(
    body: ApiTokenCreateRequest,
    actor: User = Depends(require_write_actor),
    resolved: Tuple[User, ApiToken] = Depends(resolve_token),
):
    # A child token must not outlive its parent, or a leaked short-lived one could become permanent.
    _, parent = resolved
    token, raw = await ApiTokenService().create_token(
        actor,
        name=body.name,
        read_only=body.read_only,
        expires_at=body.expires_at,
        not_after=parent.expires_at,
    )
    return ApiTokenCreatedResponse(
        id=token.id,
        name=token.name,
        token_prefix=token.token_prefix,
        read_only=token.read_only,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        created_at=token.created_at,
        token=raw,
    )


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API token",
    description=(
        "Stops the token working straight away. You can revoke only your own tokens, including "
        "the one making this call.\n\n"
        "**Who can call this:** Any member."
    ),
)
async def revoke_token(
    token_id: int,
    actor: User = Depends(require_write_actor),
):
    await ApiTokenService().revoke_token(actor, token_id)
