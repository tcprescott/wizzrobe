"""Shared helpers for serializing a Match into the API response model.

Used by both the read endpoints and the write endpoints (which reload and
return the updated match).
"""

from typing import Optional

from api.schemas.matches import MatchResponse
from application.tenant_context import require_tenant_id
from models import Match

MATCH_PREFETCH = (
    'tournament',
    'stage',
    'generated_seed',
    'players__user',
    'commentators__user',
    'trackers__user',
    'broadcasters__user',
)


def serialize_match(match: Match) -> MatchResponse:
    """Serialize a match, exposing only approved crew.

    Tortoise reverse relations are read-only and Pydantic v2 serialization
    bypasses any custom from_orm, so unapproved crew are dropped here.
    """
    resp = MatchResponse.model_validate(match, from_attributes=True)
    resp.commentators = [c for c in resp.commentators if c.approved]
    resp.trackers = [t for t in resp.trackers if t.approved]
    resp.broadcasters = [b for b in resp.broadcasters if b.approved]
    return resp


async def load_match_response(match_id: int) -> Optional[MatchResponse]:
    """Reload a match with all relations and serialize it, or None if absent.

    Scoped to the caller's tenant even though every current caller reloads an id
    a scoped service call just authorized: the id arrives as a raw path
    parameter, so a future caller that skips that step would otherwise read
    across tenants.
    """
    match = (
        await Match.filter(id=match_id, tenant_id=require_tenant_id())
        .prefetch_related(*MATCH_PREFETCH)
        .first()
    )
    return serialize_match(match) if match else None
