"""Webhooks other services call into: Discord interactions and Telegram updates.

Neither carries a Wizzrobe token. Each proves itself instead: Discord signs
every POST with the application's ed25519 key (``DISCORD_PUBLIC_KEY``), and
Telegram echoes the secret we registered (``TELEGRAM_WEBHOOK_SECRET``). An
endpoint whose key or secret isn't configured answers 404, as if absent.

Mounted on the outer app (like ``/oauth``) rather than under ``/api``: the API
router's tenant binding and per-IP limiter are for people, and these calls
come from a handful of Discord and Telegram addresses.
"""

import hmac
import json
import logging

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from application.services import telegram_bot_worker
from application.utils.clients import telegram_client
from discordbot import http_interactions

logger = logging.getLogger(__name__)

DISCORD_PATH = '/discord/interactions'

router = APIRouter(include_in_schema=False)


@router.post(DISCORD_PATH)
async def discord_interactions(request: Request) -> Response:
    key = http_interactions.public_key()
    if key is None:
        return Response(status_code=404)
    body = await request.body()
    signature = request.headers.get('X-Signature-Ed25519', '')
    timestamp = request.headers.get('X-Signature-Timestamp', '')
    if not http_interactions.verify_signature(key, signature, timestamp, body):
        return Response('invalid request signature', status_code=401)
    try:
        payload = json.loads(body)
    except ValueError:
        return Response(status_code=400)
    if not isinstance(payload, dict):
        return Response(status_code=400)
    try:
        answer = await http_interactions.dispatch(payload)
    except http_interactions.InteractionsUnavailable:
        logger.warning('Discord interaction arrived before the client signed in')
        return Response(status_code=503)
    return JSONResponse(answer)


@router.post(telegram_bot_worker.WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> Response:
    secret = telegram_client.webhook_secret()
    if secret is None or not telegram_client.is_configured():
        return Response(status_code=404)
    sent = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not hmac.compare_digest(sent.encode(), secret.encode()):
        return Response(status_code=401)
    try:
        update = telegram_client.parse_update(await request.json())
    except (ValueError, KeyError, TypeError):
        # Telegram retries anything but a 2xx, so a malformed update is dropped, not refused.
        return Response(status_code=200)
    await telegram_bot_worker.handle_update(update)
    return Response(status_code=200)


def mount(app: FastAPI) -> None:
    app.include_router(router)
