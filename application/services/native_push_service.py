"""
Native Push Service - Business Logic Layer

Push notifications for the iOS and Android store apps. The website keeps web
push (:mod:`web_push_service`); the store apps can't use it, so each install
registers the token its platform hands out and every DM mirror reaches it the
same way.

Two transports, each switched on by its own settings and a silent no-op
without them:

- **APNs** (iOS), token auth: ``APNS_KEY`` (the .p8 key's PEM text),
  ``APNS_KEY_ID``, ``APNS_TEAM_ID`` and ``APNS_TOPIC`` (the app's bundle id).
  ``APNS_SANDBOX=1`` sends to Apple's development gateway first, for builds
  run from Xcode. A token the first gateway calls bad is retried once on the
  other, since a TestFlight build and an Xcode build hand out tokens for
  different gateways.
- **FCM** (Android), HTTP v1: ``FCM_SERVICE_ACCOUNT``, the Firebase service
  account's JSON. An iOS token goes through FCM too when APNs isn't set up,
  for an app built with the Firebase plugin.

A token the push service reports as gone is deleted, as web push prunes a
404/410 subscription.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import httpx
import jwt

from application.errors import NotFoundError
from application.repositories import DevicePushTokenRepository
from application.services.audit_service import AuditActions, AuditService
from application.utils.environment import env_flag
from models import DevicePushToken, User

logger = logging.getLogger(__name__)

PLATFORMS = ('ios', 'android')

APNS_PRODUCTION = 'https://api.push.apple.com'
APNS_SANDBOX = 'https://api.sandbox.push.apple.com'
FCM_TOKEN_URL = 'https://oauth2.googleapis.com/token'
FCM_SCOPE = 'https://www.googleapis.com/auth/firebase.messaging'

# Android notification channels the app creates on first launch (web/src/native/push.ts).
ANDROID_CHANNEL = 'messages'
ANDROID_QUIET_CHANNEL = 'quiet'

# Apple allows a provider token to be reused for up to an hour and refuses one older than that.
_APNS_TOKEN_SECONDS = 50 * 60


def _env(name: str) -> str:
    return (os.environ.get(name) or '').strip()


@dataclass(frozen=True)
class _ApnsConfig:
    key: str
    key_id: str
    team_id: str
    topic: str
    sandbox_first: bool


@dataclass(frozen=True)
class _FcmConfig:
    project_id: str
    client_email: str
    private_key: str
    token_uri: str


@lru_cache(maxsize=4)
def _parse_apns(key: str, key_id: str, team_id: str, topic: str, sandbox: bool) -> Optional[_ApnsConfig]:
    if not (key and key_id and team_id and topic):
        return None
    pem = key.replace('\\n', '\n')
    try:
        jwt.encode({'iss': team_id, 'iat': 0}, pem, algorithm='ES256', headers={'kid': key_id})
    except Exception:
        logger.error('APNS_KEY is not a usable .p8 key; iOS push is off')
        return None
    return _ApnsConfig(pem, key_id, team_id, topic, sandbox)


def _apns_config() -> Optional[_ApnsConfig]:
    return _parse_apns(
        _env('APNS_KEY'), _env('APNS_KEY_ID'), _env('APNS_TEAM_ID'), _env('APNS_TOPIC'), env_flag('APNS_SANDBOX'),
    )


@lru_cache(maxsize=4)
def _parse_fcm(raw: str) -> Optional[_FcmConfig]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return _FcmConfig(
            project_id=data['project_id'],
            client_email=data['client_email'],
            private_key=data['private_key'],
            token_uri=data.get('token_uri') or FCM_TOKEN_URL,
        )
    except (ValueError, KeyError, TypeError):
        logger.error('FCM_SERVICE_ACCOUNT is not a Firebase service account JSON; Android push is off')
        return None


def _fcm_config() -> Optional[_FcmConfig]:
    return _parse_fcm(_env('FCM_SERVICE_ACCOUNT'))


_http_client: Optional[httpx.AsyncClient] = None
_apns_token: Tuple[str, float] = ('', 0.0)
_fcm_token: Tuple[str, float] = ('', 0.0)
_fcm_lock = asyncio.Lock()


def _client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        # APNs only speaks HTTP/2.
        _http_client = httpx.AsyncClient(http2=True, timeout=NativePushService.DELIVERY_TIMEOUT)
    return _http_client


async def aclose_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _apns_bearer(config: _ApnsConfig) -> str:
    global _apns_token
    token, made_at = _apns_token
    now = time.time()
    if not token or now - made_at > _APNS_TOKEN_SECONDS:
        token = jwt.encode(
            {'iss': config.team_id, 'iat': int(now)}, config.key, algorithm='ES256', headers={'kid': config.key_id},
        )
        _apns_token = (token, now)
    return token


async def _fcm_bearer(client: httpx.AsyncClient, config: _FcmConfig) -> Optional[str]:
    global _fcm_token
    async with _fcm_lock:
        token, expires_at = _fcm_token
        if token and expires_at - time.time() > 60:
            return token
        now = int(time.time())
        assertion = jwt.encode(
            {'iss': config.client_email, 'scope': FCM_SCOPE, 'aud': config.token_uri, 'iat': now, 'exp': now + 3600},
            config.private_key,
            algorithm='RS256',
        )
        try:
            response = await client.post(
                config.token_uri,
                data={'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion},
            )
        except httpx.HTTPError as exc:
            logger.warning('FCM access token request failed: %s', exc)
            return None
        if response.status_code != 200:
            logger.warning('FCM access token request got HTTP %s: %s', response.status_code, response.text[:200])
            return None
        body = response.json()
        _fcm_token = (body['access_token'], time.time() + int(body.get('expires_in', 3600)))
        return _fcm_token[0]


@dataclass(frozen=True)
class _Message:
    title: str
    body: str
    navigate: Optional[str]
    silent: bool


class NativePushService:
    """Token registration plus delivery over APNs and FCM."""

    DELIVERY_TIMEOUT = 10.0
    TTL_SECONDS = 24 * 60 * 60
    MAX_DEVICE_NAME = 100

    def __init__(self) -> None:
        self.repository = DevicePushTokenRepository()
        self.audit_service = AuditService()

    @staticmethod
    def is_configured() -> bool:
        return _apns_config() is not None or _fcm_config() is not None

    @staticmethod
    def configured_platforms() -> List[str]:
        """The platforms this server can reach right now."""
        fcm = _fcm_config() is not None
        return [p for p in PLATFORMS if fcm or (p == 'ios' and _apns_config() is not None)]

    async def list_tokens(self, user: User) -> List[DevicePushToken]:
        return await self.repository.list_for_user(user)

    async def register(
        self, user: User, *, platform: str, token: str, device_name: Optional[str] = None,
    ) -> DevicePushToken:
        """Store (or move to ``user``) one install's push token."""
        platform = (platform or '').strip().lower()
        token = (token or '').strip()
        if platform not in PLATFORMS:
            raise ValueError('Platform must be ios or android.')
        if platform not in self.configured_platforms():
            raise NotFoundError("Notifications for this kind of phone aren't set up on this server.")
        if not token or len(token) > 512 or not token.isprintable() or ' ' in token:
            raise ValueError("That push token doesn't look right.")
        name = (device_name or '').strip()[: self.MAX_DEVICE_NAME] or None
        row = await self.repository.upsert(user, platform=platform, token=token, device_name=name)
        await self.audit_service.write_log(
            user, AuditActions.DEVICE_PUSH_REGISTERED, {'device_id': row.id, 'platform': platform},
        )
        return row

    async def remove(self, user: User, device_id: int) -> None:
        row = await self.repository.get_by_id(device_id)
        if row is None or row.user_id != user.id:
            raise NotFoundError('Device not found')
        await self.repository.delete(row)
        await self.audit_service.write_log(
            user, AuditActions.DEVICE_PUSH_REMOVED, {'device_id': row.id, 'platform': row.platform},
        )

    async def send_to_discord_id(
        self, discord_id: int, *, title: str, body: str, navigate: Optional[str] = None, silent: bool = False,
    ) -> int:
        """Deliver to every install the person with ``discord_id`` has. Never raises."""
        try:
            if not self.is_configured():
                return 0
            rows = await self.repository.list_for_discord_id(discord_id)
            return await self._send(rows, _Message(title, body, navigate, silent))
        except Exception:
            logger.exception('native push failed for discord id %s', discord_id)
            return 0

    async def send_to_user(
        self, user: User, *, title: str, body: str, navigate: Optional[str] = None,
    ) -> int:
        if not self.is_configured():
            return 0
        rows = await self.repository.list_for_user(user)
        return await self._send(rows, _Message(title, body, navigate, False))

    async def _send(self, rows: List[DevicePushToken], message: _Message) -> int:
        if not rows:
            return 0
        results = await asyncio.gather(*(self._deliver_one(row, message) for row in rows))
        return sum(1 for delivered in results if delivered)

    async def _deliver_one(self, row: DevicePushToken, message: _Message) -> bool:
        apns = _apns_config()
        if row.platform == 'ios' and apns is not None:
            outcome = await self._send_apns(row.token, message, apns)
        elif _fcm_config() is not None:
            outcome = await self._send_fcm(row.token, row.platform, message, _fcm_config())
        else:
            return False
        if outcome == 'gone':
            logger.info('pruning native push token %s (%s): the push service says it is gone', row.id, row.platform)
            await self.repository.delete(row)
            return False
        if outcome == 'sent':
            await self.repository.touch_last_used(row)
            return True
        return False

    @staticmethod
    def _apns_payload(message: _Message) -> Dict:
        aps: Dict = {'alert': {'title': message.title, 'body': message.body}}
        if not message.silent:
            aps['sound'] = 'default'
        payload: Dict = {'aps': aps}
        if message.navigate:
            payload['navigate'] = message.navigate
        return payload

    async def _send_apns(self, token: str, message: _Message, config: _ApnsConfig) -> str:
        if not all(c in '0123456789abcdefABCDEF' for c in token):
            return 'gone'
        hosts = [APNS_SANDBOX, APNS_PRODUCTION] if config.sandbox_first else [APNS_PRODUCTION, APNS_SANDBOX]
        headers = {
            'authorization': f'bearer {_apns_bearer(config)}',
            'apns-topic': config.topic,
            'apns-push-type': 'alert',
            'apns-priority': '5' if message.silent else '10',
            'apns-expiration': str(int(time.time()) + self.TTL_SECONDS),
        }
        content = json.dumps(self._apns_payload(message), ensure_ascii=False).encode()
        for attempt, host in enumerate(hosts):
            try:
                response = await _client().post(f'{host}/3/device/{token}', content=content, headers=headers)
            except httpx.HTTPError as exc:
                logger.warning('APNs delivery failed: %s', exc)
                return 'failed'
            if response.status_code == 200:
                return 'sent'
            reason = _reason(response)
            if response.status_code == 410 or reason == 'Unregistered':
                return 'gone'
            # A token from the other gateway reads as bad here; try that one before giving up on it.
            if reason in ('BadDeviceToken', 'DeviceTokenNotForTopic') and attempt == 0:
                continue
            if reason == 'BadDeviceToken':
                return 'gone'
            logger.warning('APNs delivery got HTTP %s: %s', response.status_code, reason)
            return 'failed'
        return 'failed'

    @staticmethod
    def _fcm_body(token: str, platform: str, message: _Message) -> Dict:
        data = {'navigate': message.navigate} if message.navigate else {}
        body: Dict = {
            'token': token,
            'notification': {'title': message.title, 'body': message.body},
            'data': data,
            'android': {
                'priority': 'normal' if message.silent else 'high',
                'ttl': f'{NativePushService.TTL_SECONDS}s',
                'notification': {'channel_id': ANDROID_QUIET_CHANNEL if message.silent else ANDROID_CHANNEL},
            },
        }
        if platform == 'ios':
            aps: Dict = {} if message.silent else {'sound': 'default'}
            body['apns'] = {'payload': {'aps': aps}}
        return {'message': body}

    async def _send_fcm(self, token: str, platform: str, message: _Message, config: _FcmConfig) -> str:
        client = _client()
        bearer = await _fcm_bearer(client, config)
        if bearer is None:
            return 'failed'
        url = f'https://fcm.googleapis.com/v1/projects/{config.project_id}/messages:send'
        try:
            response = await client.post(
                url,
                json=self._fcm_body(token, platform, message),
                headers={'Authorization': f'Bearer {bearer}'},
            )
        except httpx.HTTPError as exc:
            logger.warning('FCM delivery failed: %s', exc)
            return 'failed'
        if response.status_code == 200:
            return 'sent'
        if response.status_code == 404 or _fcm_error(response) == 'UNREGISTERED':
            return 'gone'
        logger.warning('FCM delivery got HTTP %s: %s', response.status_code, response.text[:200])
        return 'failed'


def _reason(response: httpx.Response) -> str:
    try:
        return str(response.json().get('reason') or '')
    except ValueError:
        return ''


def _fcm_error(response: httpx.Response) -> str:
    try:
        details = response.json().get('error', {}).get('details', [])
    except ValueError:
        return ''
    for detail in details:
        if isinstance(detail, dict) and detail.get('errorCode'):
            return str(detail['errorCode'])
    return ''
