"""What the Patreon services share: the platform's OAuth app, client construction,
sealing, and minting a sign-in.

One Patreon OAuth app (``PATREON_CLIENT_ID`` / ``PATREON_CLIENT_SECRET``) serves
every community, for both the creator's sign-in and a member's. Both come back to
``/api/patreon/callback`` with no session, so the sign-in is tied to the person
who started it by a stored, single-use state.
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from application.repositories import PatreonRepository
from application.utils.clients.patreon_client import (
    PatreonClient,
    PatreonOAuthClient,
    build_authorize_url,
)
from application.utils.environment import get_base_url
from application.utils.mocks.mock_patreon import MockPatreonClient, MockPatreonOAuthClient, is_mock_patreon
from application.utils.secret_box import open_, seal
from models import PatreonCampaign, PatreonOAuthPurpose, User

CALLBACK_PATH = '/api/patreon/callback'
STATE_TTL = timedelta(minutes=10)

SEAL_PURPOSE = 'patreon_token'
WEBHOOK_PURPOSE = 'patreon_webhook'


def is_configured() -> bool:
    if is_mock_patreon():
        return True
    return bool(os.getenv('PATREON_CLIENT_ID') and os.getenv('PATREON_CLIENT_SECRET'))


def redirect_uri() -> str:
    return os.getenv('PATREON_REDIRECT_URI') or f'{get_base_url()}{CALLBACK_PATH}'


def oauth_client() -> Any:
    if is_mock_patreon():
        return MockPatreonOAuthClient()
    return PatreonOAuthClient(os.getenv('PATREON_CLIENT_ID', ''), os.getenv('PATREON_CLIENT_SECRET', ''))


def api_client(access_token: str) -> Any:
    if is_mock_patreon():
        return MockPatreonClient()
    return PatreonClient(access_token)


def seal_for(tenant_id: int, value: str, purpose: str = SEAL_PURPOSE) -> str:
    return seal(value, purpose=purpose, context=str(tenant_id))


def open_for(campaign: PatreonCampaign, value: Optional[str], purpose: str = SEAL_PURPOSE) -> Optional[str]:
    if not value:
        return None
    return open_(value, purpose=purpose, context=str(campaign.tenant_id))


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


async def start_sign_in(user: User, purpose: PatreonOAuthPurpose, scope: str) -> str:
    """Store a fresh single-use state for ``user`` in the community in scope; return Patreon's authorize URL.

    With ``MOCK_PATREON`` on, the URL is our own callback with a canned code, so
    the flow finishes without leaving the app.
    """
    if not is_configured():
        raise ValueError("Patreon sign-in isn't set up on this server yet.")
    now = datetime.now(timezone.utc)
    await PatreonRepository.prune_states(now)
    state = secrets.token_urlsafe(32)
    await PatreonRepository.create_state(hash_state(state), purpose, user.id, now + STATE_TTL)
    if is_mock_patreon():
        return f'{redirect_uri()}?code=mock&state={state}'
    return build_authorize_url(os.getenv('PATREON_CLIENT_ID', ''), redirect_uri(), scope, state)
