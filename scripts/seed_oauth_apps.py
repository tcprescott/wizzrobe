"""Dev-seed fixtures for third-party apps (the ``/oauth`` authorization server).

Two global apps owned by ``staff_user`` (one confidential, one public, so both
client types exist), and per tenant one live grant for staff plus the spent
authorization code it came from. Deterministic like ``seed_tokens``: the dev
app bearer is predictable for ``/api-validation`` curl runs, and only hashes
are stored. Non-secret fixtures.
"""

import hashlib
from datetime import datetime, timedelta

from models import ApiToken, ApiTokenOrigin, OAuthApp, OAuthAppBlock, OAuthAppCode, OAuthClientType, Tenant, User

DEV_CONFIDENTIAL_CLIENT_ID = 'wza_devseed_schedule_bot'
DEV_CONFIDENTIAL_CLIENT_CRED = 'wzs_devseed_local_only_do_not_use'
DEV_PUBLIC_CLIENT_ID = 'wza_devseed_desktop_client'
DEV_BLOCKED_CLIENT_ID = 'wza_devseed_blocked_app'


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def _seed_apps(owner: User) -> OAuthApp:
    confidential, _ = await OAuthApp.get_or_create(
        client_id=DEV_CONFIDENTIAL_CLIENT_ID,
        defaults={
            'owner': owner,
            'name': 'Dev Schedule Bot',
            'description': 'Posts the schedule to a stream overlay. Seeded fixture.',
            'homepage_url': 'https://example.com/schedule-bot',
            'client_type': OAuthClientType.CONFIDENTIAL,
            'client_secret_hash': _sha(DEV_CONFIDENTIAL_CLIENT_CRED),
            'secret_prefix': DEV_CONFIDENTIAL_CLIENT_CRED[:10],
            'redirect_uris': ['https://example.com/schedule-bot/callback', 'http://127.0.0.1:8765/callback'],
            'allowed_scopes': ['profile', 'schedule:read', 'play'],
        },
    )
    await OAuthApp.get_or_create(
        client_id=DEV_PUBLIC_CLIENT_ID,
        defaults={
            'owner': owner,
            'name': 'Dev Desktop Client',
            'description': 'A desktop app with no secret, signing in with PKCE. Seeded fixture.',
            'client_type': OAuthClientType.PUBLIC,
            'redirect_uris': ['http://127.0.0.1:8766/callback', 'org.example.wizzrobe:/callback'],
            'allowed_scopes': ['profile', 'schedule:read', 'crew', 'volunteer'],
        },
    )
    return confidential


async def _seed_blocked_app(owner: User) -> OAuthApp:
    """An app every seeded community blocks, so Connected apps shows the state."""
    blocked, _ = await OAuthApp.get_or_create(
        client_id=DEV_BLOCKED_CLIENT_ID,
        defaults={
            'owner': owner,
            'name': 'Dev Blocked App',
            'description': 'An app staff blocked here. Seeded fixture.',
            'client_type': OAuthClientType.PUBLIC,
            'redirect_uris': ['http://127.0.0.1:8767/callback'],
            'allowed_scopes': ['profile', 'staff:read'],
        },
    )
    return blocked


async def seed_oauth_apps_for_tenant(tenant: Tenant, staff: User, now: datetime) -> None:
    """Seed the global apps (once) and this tenant's dev grant and spent code."""
    app = await _seed_apps(staff)
    bearer = f'wizzrobe_app_devseed_{tenant.slug}_local_only_do_not_use'
    grant = await ApiToken.filter(token_hash=_sha(bearer)).first()
    if grant is None:
        grant = await ApiToken.create(
            tenant=tenant, user=staff, oauth_app=app, name=app.name,
            origin=ApiTokenOrigin.APP.value,
            token_hash=_sha(bearer), token_prefix=bearer[:17],
            scope='profile schedule:read', read_only=True,
            expires_at=now + timedelta(days=3650),
            refresh_token_hash=_sha(f'{bearer}_refresh'),
            refresh_expires_at=now + timedelta(days=3650),
        )
    await OAuthAppCode.get_or_create(
        code_hash=_sha(f'devseed_code_{tenant.slug}'),
        defaults={
            'app': app, 'user': staff, 'tenant': tenant,
            'redirect_uri': app.redirect_uris[0],
            'code_challenge': 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM',
            'scope': 'profile schedule:read',
            'expires_at': now - timedelta(minutes=1),
            'consumed_at': now - timedelta(minutes=2),
            'grant': grant,
        },
    )
    blocked = await _seed_blocked_app(staff)
    await OAuthAppBlock.get_or_create(tenant=tenant, app=blocked, defaults={'blocked_by': staff})
    print(f'    [{tenant.slug}] oauth apps ok (dev app bearer: {bearer})')
