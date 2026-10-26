"""Room Token Repository - Data Access Layer

Data access for the venue-kiosk tokens that open the read-only seeds view.
"""

from datetime import datetime
from typing import List, Optional

from application.repositories._base import EventScopedRepository
from application.repositories._event import event_scoped
from application.repositories._tenant import scoped
from models import RoomToken


class RoomTokenRepository(EventScopedRepository[RoomToken]):
    """Repository for room token data access.

    ``get_by_hash`` is **scoped**, unlike its ``ApiToken`` counterpart. That one
    resolves a bearer before any tenant exists; this one is only ever reached
    from a page under ``/t/<slug>``, where the middleware has already bound the
    community. Scoping it there means a token issued by another community is
    simply not found — the wrong-tenant case falls out of the query rather than
    needing a check someone could forget. A token belongs to the event it was
    issued in; ``get_by_hash`` finds it whichever event is in the URL, and the
    token decides.
    """

    model = RoomToken

    @staticmethod
    async def get_by_hash(token_hash: str) -> Optional[RoomToken]:
        """The token with this hash (event-scope: exempt — one row per token)."""
        return await scoped(RoomToken.filter(token_hash=token_hash)).first()

    @staticmethod
    async def list_all() -> List[RoomToken]:
        """Every token the current event has issued, live ones first, newest first.

        Revoked rows stay in the list: staff need to see that the token they
        pulled off a machine is the one that stopped working.

        The live-first half is sorted in Python, not by ``ORDER BY revoked_at``,
        because the two databases disagree about where NULLs land — Postgres
        sorts them last ascending and SQLite sorts them first, so the DB ordering
        would put revoked tokens on top in production and pass the test anyway.
        ``sorted`` is stable, so newest-first survives inside each group.
        """
        rows = await event_scoped(RoomToken.all()).order_by('-created_at') \
            .prefetch_related('created_by')
        return sorted(rows, key=lambda token: token.revoked_at is not None)

    @staticmethod
    async def touch_last_used(token: RoomToken, when: datetime) -> None:
        token.last_used_at = when
        await token.save(update_fields=['last_used_at'])

    @staticmethod
    async def revoke(token: RoomToken, when: datetime) -> None:
        token.revoked_at = when
        await token.save(update_fields=['revoked_at'])
