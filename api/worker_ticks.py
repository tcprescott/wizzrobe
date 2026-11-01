"""``POST /internal/workers/<key>/tick``: Cloud Scheduler runs a background worker.

Live only with ``WORKER_MODE=scheduled``; in the default ``loop`` mode every
path here is a 404 and the workers loop in-process as before. Each call must
carry the Google-signed OIDC token Cloud Scheduler attaches for
``WORKER_TICK_SERVICE_ACCOUNT``, minted for ``WORKER_TICK_AUDIENCE``.

Responses: 200 when every tick succeeded or there was nothing to do (worker
switched off, or a tick from an earlier call is still running here), 500 when a
tick failed so the Scheduler job's log shows it. Failures are already reported
to Sentry by :meth:`BackgroundLoop.run_once`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from application import worker_registry
from application.utils import google_oidc

logger = logging.getLogger(__name__)

PATH_PREFIX = '/internal/workers'

DEFAULT_WINDOW_SECONDS = 55.0

_running: Dict[str, asyncio.Lock] = {}


def _window() -> float:
    try:
        return float(os.environ.get('WORKER_TICK_WINDOW_SECONDS') or DEFAULT_WINDOW_SECONDS)
    except ValueError:
        return DEFAULT_WINDOW_SECONDS


async def require_scheduler(request: Request) -> None:
    if worker_registry.worker_mode() is not worker_registry.WorkerMode.SCHEDULED:
        raise HTTPException(status_code=404)
    audience = (os.environ.get('WORKER_TICK_AUDIENCE') or '').strip()
    email = (os.environ.get('WORKER_TICK_SERVICE_ACCOUNT') or '').strip()
    if not audience or not email:
        logger.error('WORKER_MODE=scheduled needs WORKER_TICK_AUDIENCE and WORKER_TICK_SERVICE_ACCOUNT')
        raise HTTPException(status_code=503, detail='worker ticks are not configured')
    scheme, _, token = (request.headers.get('authorization') or '').partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise HTTPException(status_code=401, headers={'WWW-Authenticate': 'Bearer'})
    try:
        await google_oidc.verify_id_token(token.strip(), audience=audience, email=email)
    except google_oidc.InvalidToken as exc:
        logger.warning('refused a worker tick: %s', exc)
        raise HTTPException(status_code=401, headers={'WWW-Authenticate': 'Bearer'}) from exc


router = APIRouter(prefix=PATH_PREFIX, dependencies=[Depends(require_scheduler)], include_in_schema=False)


@router.post('/{key}/tick')
async def tick(key: str) -> JSONResponse:
    spec = worker_registry.scheduled(key)
    if spec is None:
        raise HTTPException(status_code=404)
    if not spec.enabled():
        return JSONResponse({'worker': key, 'skipped': 'disabled'})
    lock = _running.setdefault(key, asyncio.Lock())
    if lock.locked():
        return JSONResponse({'worker': key, 'skipped': 'running'})
    async with lock:
        result = await worker_registry.run_scheduled(spec, _window())
    body = {'worker': key, 'ticks': result.ticks, 'failures': result.failures}
    return JSONResponse(body, status_code=500 if result.failures else 200)


def mount(app: FastAPI) -> None:
    app.include_router(router)
