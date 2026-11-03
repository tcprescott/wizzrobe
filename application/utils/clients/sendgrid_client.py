"""Minimal SendGrid v3 Mail Send client.

One call: :func:`send_email`. Configured by ``SENDGRID_API_KEY`` and
``EMAIL_FROM_ADDRESS`` (plus an optional ``EMAIL_FROM_NAME``); without both,
:func:`is_configured` is false and nothing is sent.
"""

import logging
import os
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

_API_URL = 'https://api.sendgrid.com/v3/mail/send'
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_client: Optional[httpx.AsyncClient] = None


def is_configured() -> bool:
    return bool(os.getenv('SENDGRID_API_KEY') and os.getenv('EMAIL_FROM_ADDRESS'))


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def send_email(
    to: str,
    subject: str,
    text: str,
    html: str,
    headers: Optional[Dict[str, str]] = None,
) -> bool:
    """Send one message. ``True`` once SendGrid accepted it; never raises."""
    payload: dict = {
        'personalizations': [{'to': [{'email': to}]}],
        'from': {
            'email': os.getenv('EMAIL_FROM_ADDRESS', ''),
            'name': os.getenv('EMAIL_FROM_NAME') or 'Wizzrobe',
        },
        'subject': subject,
        'content': [
            {'type': 'text/plain', 'value': text},
            {'type': 'text/html', 'value': html},
        ],
        # Keep click tracking off: it would rewrite every call-to-action link through SendGrid's domain.
        'tracking_settings': {'click_tracking': {'enable': False, 'enable_text': False}},
    }
    if headers:
        payload['headers'] = headers
    try:
        response = await _get_client().post(
            _API_URL,
            json=payload,
            headers={'Authorization': f'Bearer {os.getenv("SENDGRID_API_KEY", "")}'},
        )
    except httpx.HTTPError as exc:
        logger.warning('SendGrid send failed: %s', str(exc) or type(exc).__name__)
        return False
    if response.status_code >= 400:
        logger.warning('SendGrid refused a send with HTTP %s: %s', response.status_code, response.text[:300])
        return False
    return True
