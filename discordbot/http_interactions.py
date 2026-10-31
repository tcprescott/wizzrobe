"""Discord interactions received as signed HTTP POSTs instead of over the gateway.

With an Interactions Endpoint URL set in the Discord developer portal, Discord
stops sending button presses, modal submits and slash commands down the
gateway and POSTs each one to us instead, signed with the application's
ed25519 key. That lets DM buttons work in a process that never opens a gateway
connection (the stateless API service), while the handlers registered in
``discordbot/__init__.py`` stay exactly as they are.

The trick is the first response. Over the gateway, a handler answers by
POSTing to Discord's callback endpoint. Over HTTP, Discord expects that first
answer as the body of its own request. So each payload is fed through the
library's normal dispatch (``parse_interaction_create``, the same path a
gateway event takes) with a webhook adapter that, for that one interaction id,
captures the first callback instead of sending it. The endpoint returns what
was captured. Everything after the first response (followups, message edits)
already goes over Discord's REST API and is left alone.

A handler that hasn't answered within :data:`RESPONSE_DEADLINE_SECONDS` gets a
deferred acknowledgement on its behalf, and a late first answer is then
delivered as a followup or an edit of the original message.
"""

import asyncio
import contextvars
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from discord.webhook.async_ import AsyncWebhookAdapter, async_context

logger = logging.getLogger(__name__)

PUBLIC_KEY_ENV = 'DISCORD_PUBLIC_KEY'

# Discord gives three seconds for the first response; leave room for the trip back.
RESPONSE_DEADLINE_SECONDS = 2.5
# An interaction token stays usable for followups for 15 minutes.
TOKEN_LIFETIME_SECONDS = 15 * 60
# Older signed requests are refused, so a captured one can't be replayed later.
MAX_CLOCK_SKEW_SECONDS = 5 * 60

PING = 1
APPLICATION_COMMAND = 2
MESSAGE_COMPONENT = 3
AUTOCOMPLETE = 4
MODAL_SUBMIT = 5

PONG = 1
CHANNEL_MESSAGE = 4
DEFERRED_CHANNEL_MESSAGE = 5
DEFERRED_UPDATE_MESSAGE = 6
UPDATE_MESSAGE = 7
AUTOCOMPLETE_RESULT = 8

EPHEMERAL_FLAG = 1 << 6


class InteractionsUnavailable(Exception):
    """The Discord client isn't signed in yet, so the interaction can't be run."""


def public_key() -> Optional[str]:
    value = (os.environ.get(PUBLIC_KEY_ENV) or '').strip()
    return value or None


def verify_signature(key_hex: str, signature_hex: str, timestamp: str, body: bytes) -> bool:
    """True when ``body`` was signed by the application key and is recent."""
    try:
        signed_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - signed_at) > MAX_CLOCK_SKEW_SECONDS:
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
        key.verify(bytes.fromhex(signature_hex), timestamp.encode() + body)
    except (InvalidSignature, ValueError):
        return False
    return True


@dataclass
class _Pending:
    application_id: int
    first_response: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


_pending: Dict[int, _Pending] = {}


def _callback_echo(interaction_id: int, response_type: int) -> Dict[str, Any]:
    """The ``with_response`` body the library expects back from the callback endpoint."""
    return {'interaction': {'id': str(interaction_id), 'type': response_type}}


class _CapturingAdapter(AsyncWebhookAdapter):
    def create_interaction_response(self, interaction_id, token, *, session, proxy=None, proxy_auth=None, params):  # type: ignore[override]
        pending = _pending.get(int(interaction_id))
        if pending is None:
            return super().create_interaction_response(
                interaction_id, token, session=session, proxy=proxy, proxy_auth=proxy_auth, params=params,
            )
        return self._capture(pending, int(interaction_id), token, session, proxy, proxy_auth, params)

    async def _capture(self, pending: _Pending, interaction_id: int, token: str, session, proxy, proxy_auth, params) -> Dict[str, Any]:
        payload = dict(params.payload or {})
        response_type = int(payload.get('type', DEFERRED_UPDATE_MESSAGE))
        if params.files:
            logger.warning('Discord HTTP interaction %s: files in a first response are dropped', interaction_id)
        if not pending.first_response.done():
            pending.first_response.set_result(payload)
            return _callback_echo(interaction_id, response_type)
        await self._deliver_late(pending, interaction_id, token, session, proxy, proxy_auth, payload)
        return _callback_echo(interaction_id, response_type)

    async def _deliver_late(self, pending: _Pending, interaction_id: int, token: str, session, proxy, proxy_auth, payload: Dict[str, Any]) -> None:
        response_type = payload.get('type')
        data = payload.get('data') or {}
        if response_type == CHANNEL_MESSAGE:
            await self.execute_webhook(
                pending.application_id, token, session=session, proxy=proxy, proxy_auth=proxy_auth,
                payload=data, wait=True,
            )
        elif response_type == UPDATE_MESSAGE:
            await self.edit_original_interaction_response(
                pending.application_id, token, session=session, proxy=proxy, proxy_auth=proxy_auth,
                payload=data,
            )
        elif response_type not in (DEFERRED_CHANNEL_MESSAGE, DEFERRED_UPDATE_MESSAGE):
            logger.warning(
                'Discord HTTP interaction %s answered with type %s after the deadline; dropped',
                interaction_id, response_type,
            )


_ADAPTER = _CapturingAdapter()


def _stand_in_response(interaction_type: int) -> Dict[str, Any]:
    """What to answer when the handler is still working at the deadline."""
    if interaction_type == APPLICATION_COMMAND:
        return {'type': DEFERRED_CHANNEL_MESSAGE, 'data': {'flags': EPHEMERAL_FLAG}}
    if interaction_type == AUTOCOMPLETE:
        return {'type': AUTOCOMPLETE_RESULT, 'data': {'choices': []}}
    return {'type': DEFERRED_UPDATE_MESSAGE}


async def _signed_in_state():
    """The Discord client's connection state, once it holds a token."""
    from application.services.discord.discord_service import ensure_rest_client

    bot = await ensure_rest_client()
    if bot is None:
        raise InteractionsUnavailable
    return bot._connection


async def dispatch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run one verified interaction and return Discord's first response body."""
    interaction_type = int(payload.get('type', 0))
    if interaction_type == PING:
        return {'type': PONG}

    state = await _signed_in_state()
    interaction_id = int(payload['id'])
    pending = _Pending(application_id=int(payload['application_id']))
    _pending[interaction_id] = pending
    asyncio.get_running_loop().call_later(TOKEN_LIFETIME_SECONDS, _pending.pop, interaction_id, None)

    context = contextvars.copy_context()
    context.run(async_context.set, _ADAPTER)
    try:
        context.run(state.parse_interaction_create, payload)
    except Exception:
        logger.exception('Discord HTTP interaction %s could not be dispatched', interaction_id)
        _pending.pop(interaction_id, None)
        raise

    try:
        return await asyncio.wait_for(asyncio.shield(pending.first_response), RESPONSE_DEADLINE_SECONDS)
    except asyncio.TimeoutError:
        stand_in = _stand_in_response(interaction_type)
        if not pending.first_response.done():
            pending.first_response.set_result(stand_in)
            logger.info('Discord HTTP interaction %s: no answer in time, deferred', interaction_id)
            return stand_in
        return pending.first_response.result()
