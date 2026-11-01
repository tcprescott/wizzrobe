"""Create a Google Cloud Tasks HTTP task, over the REST API.

The task POSTs a JSON body to a URL at a given time, signed with a Google OIDC
token for a service account, the same kind of token Cloud Scheduler sends to
``/internal/workers/<key>/tick``. On Cloud Run the access token comes from the
metadata server, so no key file or client library is needed.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

API = 'https://cloudtasks.googleapis.com/v2'
METADATA_TOKEN_URL = (
    'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token'
)
_TIMEOUT = httpx.Timeout(10.0, connect=3.0)

_token: Optional[Tuple[str, float]] = None


class CloudTasksError(Exception):
    """The task could not be created."""


async def _access_token(client: httpx.AsyncClient) -> str:
    global _token
    if _token is not None and time.monotonic() < _token[1]:
        return _token[0]
    response = await client.get(METADATA_TOKEN_URL, headers={'Metadata-Flavor': 'Google'})
    response.raise_for_status()
    body = response.json()
    _token = (body['access_token'], time.monotonic() + max(0, int(body.get('expires_in', 0)) - 60))
    return _token[0]


def task_body(
    *, url: str, payload: Dict[str, Any], delay_seconds: float,
    service_account: str, audience: str, dispatch_deadline_seconds: int = 900,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    when = (now or datetime.now(timezone.utc)) + timedelta(seconds=max(0.0, delay_seconds))
    return {'task': {
        'scheduleTime': when.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
        'dispatchDeadline': f'{dispatch_deadline_seconds}s',
        'httpRequest': {
            'httpMethod': 'POST',
            'url': url,
            'headers': {'Content-Type': 'application/json'},
            'body': base64.b64encode(json.dumps(payload).encode()).decode(),
            'oidcToken': {'serviceAccountEmail': service_account, 'audience': audience},
        },
    }}


async def create_task(queue_path: str, body: Dict[str, Any]) -> str:
    """Create the task on ``projects/<p>/locations/<l>/queues/<q>``; return its name."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            token = await _access_token(client)
            response = await client.post(
                f'{API}/{queue_path}/tasks', json=body,
                headers={'Authorization': f'Bearer {token}'},
            )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise CloudTasksError(f'{type(exc).__name__}: {exc}') from exc
    if response.status_code >= 400:
        raise CloudTasksError(f'HTTP {response.status_code}: {response.text[:300]}')
    return str(response.json().get('name', ''))
