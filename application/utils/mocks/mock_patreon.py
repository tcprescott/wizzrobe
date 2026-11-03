"""The mock Patreon layer for local development (``MOCK_PATREON``).

Connecting a campaign signs in as a canned creator whose campaign has two
tiers and three members; linking signs in as Patreon user ``p-1001``, who is
an active Hero patron of it. The dev seed links player_one to ``p-1001`` and
player_two to ``p-1002`` (a declined patron). Never active in production.
"""

from typing import List, Optional, Tuple

from application.utils.clients.patreon_client import (
    ACTIVE_PATRON,
    Campaign,
    Identity,
    MemberStatus,
    Tier,
    Tokens,
)
from application.utils.environment import env_flag, is_production

MOCK_CAMPAIGN_ID = 'mock-campaign'
MOCK_SIGNING_KEY = 'mock-webhook-secret'
MOCK_LINKED_ACCOUNTS = {'player_one': 'p-1001', 'player_two': 'p-1002'}

_TIERS = [Tier('t-hero', 'Hero'), Tier('t-supporter', 'Supporter')]
_MEMBERS = [
    MemberStatus('m-1', 'p-1001', MOCK_CAMPAIGN_ID, ACTIVE_PATRON, ('t-hero',)),
    MemberStatus('m-2', 'p-1002', MOCK_CAMPAIGN_ID, 'declined_patron', ('t-supporter',)),
    MemberStatus('m-3', 'p-1003', MOCK_CAMPAIGN_ID, 'former_patron', ()),
]


def is_mock_patreon() -> bool:
    """True when MOCK_PATREON is on (and refuses to be on in production)."""
    enabled = env_flag('MOCK_PATREON')
    if enabled and is_production():
        raise RuntimeError(
            'MOCK_PATREON must not be enabled in production: it hands out canned '
            'patron badges. Unset MOCK_PATREON or change ENVIRONMENT.'
        )
    return enabled


class MockPatreonOAuthClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def exchange_code(self, code: str, redirect_uri: str) -> Tokens:
        return Tokens(access_token=f'mock-access-{code}', refresh_token='mock-refresh', expires_in=2_678_400)

    async def refresh(self, refresh_token: str) -> Tokens:
        return Tokens(access_token='mock-access-refreshed', refresh_token='mock-refresh-2', expires_in=2_678_400)


class MockPatreonClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def identity(self, *, with_memberships: bool = False) -> Identity:
        return Identity(
            patreon_user_id='p-1001', name='Mock Patron',
            memberships=(_MEMBERS[0],) if with_memberships else (),
        )

    async def campaigns(self) -> List[Campaign]:
        return [Campaign(MOCK_CAMPAIGN_ID, 'Mock Campaign')]

    async def tiers(self, campaign_id: str) -> List[Tier]:
        return list(_TIERS)

    async def members_page(
        self, campaign_id: str, cursor: Optional[str] = None,
    ) -> Tuple[List[MemberStatus], Optional[str]]:
        return list(_MEMBERS), None

    async def create_webhook(self, campaign_id: str, uri: str) -> Tuple[str, str]:
        return 'mock-webhook', MOCK_SIGNING_KEY

    async def delete_webhook(self, webhook_id: str) -> None:
        return None
