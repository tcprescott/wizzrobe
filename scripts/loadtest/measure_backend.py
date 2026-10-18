"""Time every REST list endpoint, every read-only MCP tool and every worker tick.

The non-browser half of the load sim (``page_timings.js`` is the browser half).
Runs in-process against whatever the dev database holds — run ``seed_scale.py``
first for numbers that mean something — and, for each call, records wall time,
the number of SQL statements issued and the response size. No server and no
background workers run, so every statement counted belongs to the call.

* **REST**: every ``GET`` under ``/api`` whose path parameters can be filled from
  the database (a real match, tournament, user … of the target community), with
  the community's seeded dev token.
* **MCP**: every tool the catalogue marks read-only, with ``tenant`` and any id
  argument filled the same way, over the real ``/mcp`` transport with an OAuth
  token minted for ``staff_user``. Tools whose required arguments cannot be
  guessed are listed as skipped rather than silently dropped.
* **Workers**: each ``run_worker_loop`` tick, awaited once (as
  ``scripts/run_worker_tick.py`` does), across every community.

    PYTHONPATH=. poetry run python scripts/loadtest/measure_backend.py [--only rest|mcp|workers] [--json out.json]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import re
import secrets
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from tortoise import Tortoise, connections

METHODS = ('execute_query', 'execute_query_dict', 'execute_insert', 'execute_many')
WORKERS = {
    'discord_event': 'application.services.discord.discord_event_worker',
    'discord_role_sync': 'application.services.discord.discord_role_sync_worker',
    'seed_roll': 'application.services.seed_roll_worker',
    'volunteer_reminder': 'application.services.volunteer.volunteer_reminder',
    'async_qualifier': 'application.services.async_qualifier.async_qualifier_worker',
    'stage_reminder': 'application.services.match.stage_reminder',
    'service_health': 'application.services.service_health_worker',
    'race_room': 'application.services.race_room_worker',
    'check_in_sync': 'application.services.check_in_sync_worker',
    'startgg_sync': 'application.services.startgg_sync_worker',
    'community_event_reminder': 'application.services.community_event_reminder',
    'active_users_metrics': 'application.services.active_users_metrics',
    'speedgaming_sync': 'application.services.speedgaming_sync_worker',
}


@contextmanager
def count_queries():
    client = connections.get('default')
    tally = {'n': 0}
    for name in METHODS:
        def wrap(original):
            async def counted(*args, **kwargs):
                tally['n'] += 1
                return await original(*args, **kwargs)
            return counted
        client.__dict__[name] = wrap(getattr(client, name))
    try:
        yield tally
    finally:
        for name in METHODS:
            client.__dict__.pop(name, None)


async def timed(coro_fn) -> dict:
    with count_queries() as tally:
        t0 = time.perf_counter()
        try:
            result = await coro_fn()
            err = None
        except Exception as e:
            result, err = None, f'{type(e).__name__}: {str(e)[:120]}'
        ms = (time.perf_counter() - t0) * 1000
    return {'ms': round(ms, 1), 'queries': tally['n'], 'error': err, 'result': result}


async def ids_for(tenant_id: int) -> dict[str, Any]:
    from models import (
        Bracket,
        CheckInEvent,
        Equipment,
        Match,
        Preset,
        Stage,
        VolunteerPosition,
        VolunteerShift,
    )

    conn = connections.get('default')
    _, busiest = await conn.execute_query(
        'SELECT user_id FROM matchplayers WHERE tenant_id = $1 GROUP BY user_id ORDER BY count(*) DESC LIMIT 1',
        [tenant_id])
    _, tour = await conn.execute_query(
        'SELECT t.id FROM tournament t JOIN match m ON m.tournament_id = t.id WHERE t.tenant_id = $1 '
        'AND t.is_active GROUP BY t.id ORDER BY count(*) DESC LIMIT 1', [tenant_id])
    match = await Match.filter(tenant_id=tenant_id, finished_at__isnull=True).order_by('scheduled_at').first()
    vals = {
        'match_id': match.id if match else None,
        'tournament_id': tour[0]['id'] if tour else None,
        'user_id': busiest[0]['user_id'] if busiest else None,
        'stage_id': (await Stage.filter(tenant_id=tenant_id).first()).id,
        'bracket_id': getattr(await Bracket.filter(tenant_id=tenant_id).first(), 'id', None),
        'event_id': getattr(await CheckInEvent.filter(tenant_id=tenant_id).order_by('-id').first(), 'id', None),
        'equipment_id': getattr(await Equipment.filter(tenant_id=tenant_id).first(), 'id', None),
        'preset_id': getattr(await Preset.filter(tenant_id=tenant_id).first(), 'id', None),
        'position_id': getattr(await VolunteerPosition.filter(tenant_id=tenant_id).first(), 'id', None),
        'shift_id': getattr(await VolunteerShift.filter(tenant_id=tenant_id).first(), 'id', None),
    }
    _, hook = await conn.execute_query('SELECT id FROM webhook WHERE tenant_id = $1 LIMIT 1', [tenant_id])
    _, aq = await conn.execute_query('SELECT id FROM asyncqualifier WHERE tenant_id = $1 LIMIT 1', [tenant_id])
    _, link = await conn.execute_query('SELECT id FROM speedgamingeventlink WHERE tenant_id = $1 LIMIT 1', [tenant_id])
    vals['webhook_id'] = hook[0]['id'] if hook else None
    vals['qualifier_id'] = aq[0]['id'] if aq else None
    vals['link_id'] = link[0]['id'] if link else None
    vals['asset_id'] = vals['equipment_id']
    vals['id'] = vals['match_id']
    return vals


def query_value(name: str, ids: dict) -> Any:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if name in ids:
        return ids[name]
    if any(k in name for k in ('start', 'from', 'since')):
        return (now - timedelta(days=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
    if any(k in name for k in ('end', 'until', 'to')):
        return (now + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
    if name in ('at', 'when'):
        return now.strftime('%Y-%m-%dT%H:%M:%SZ')
    if name in ('days', 'weeks'):
        return 30
    return None


async def measure_rest(tenant: Any, ids: dict) -> list[dict]:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import api

    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    token = f'wizzrobe_pat_devseed_{tenant.slug}_local_only_do_not_use'
    out = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', timeout=120) as c:
        for path, ops in app.openapi()['paths'].items():
            if 'get' not in ops:
                continue
            params = re.findall(r'{(\w+)}', path)
            if any(ids.get(p) is None for p in params):
                out.append({'surface': 'rest', 'name': f'GET {path}', 'skipped': f'no id for {params}'})
                continue
            url = path.format(**{p: ids[p] for p in params})
            query = {}
            for prm in ops['get'].get('parameters', []):
                if prm.get('in') == 'query' and prm.get('required'):
                    query[prm['name']] = query_value(prm['name'], ids)
            if query:
                url += '?' + '&'.join(f'{k}={v}' for k, v in query.items() if v is not None)
            r = await timed(lambda url=url: c.get(url, headers={'Authorization': f'Bearer {token}'}))
            resp = r.pop('result')
            r.update({'surface': 'rest', 'name': f'GET {path}',
                      'status': resp.status_code if resp is not None else None,
                      'bytes': len(resp.content) if resp is not None else 0})
            out.append(r)
    return out


async def measure_mcp(tenant: Any, ids: dict) -> list[dict]:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import mcpserver
    from application.services.api_token_service import MCP_TOKEN_PREFIX
    from application.services.mcp_auth_service import granted_scope
    from models import ApiToken, ApiTokenOrigin, McpOAuthClient, User

    staff = await User.get(username='staff_user')
    client_row, _ = await McpOAuthClient.get_or_create(
        client_id='loadtest-client', defaults={
            'client_name': 'Load test', 'redirect_uris': ['http://localhost/cb'],
            'grant_types': ['authorization_code'], 'response_types': ['code']})
    raw = MCP_TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = await ApiToken.create(
        user=staff, name='loadtest-mcp', tenant_id=None, oauth_client=client_row,
        origin=ApiTokenOrigin.OAUTH.value, read_only=True, scope=granted_scope(allow_write=False),
        token_hash=hashlib.sha256(raw.encode()).hexdigest(), token_prefix=raw[:17],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
               'Authorization': f'Bearer {raw}'}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    from models import Match as _M
    pair = await _M.filter(id=ids['match_id']).prefetch_related('players').first()
    guesses = dict(ids, tenant=tenant.slug, start=(now - timedelta(days=4)).isoformat(),
                   end=(now + timedelta(days=3)).isoformat(), date=now.date().isoformat(),
                   instant=now.isoformat(), dimension='path',
                   player_ids=[p.user_id for p in pair.players] if pair else None)
    out = []
    try:
        app = FastAPI()
        mcpserver.mount(app)
        async with mcpserver.session_lifespan():
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', timeout=120) as c:
                listing = await c.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
                for tool in listing.json()['result']['tools']:
                    if not (tool.get('annotations') or {}).get('readOnlyHint'):
                        continue
                    schema = tool.get('inputSchema') or {}
                    required = schema.get('required', [])
                    missing = [p for p in required if guesses.get(p) is None]
                    if missing:
                        out.append({'surface': 'mcp', 'name': tool['name'], 'skipped': f'needs {missing}'})
                        continue
                    args = {p: guesses[p] for p in required}
                    if 'tenant' in (schema.get('properties') or {}):
                        args['tenant'] = tenant.slug
                    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                            'params': {'name': tool['name'], 'arguments': args}}
                    r = await timed(lambda body=body: c.post('/mcp', headers=headers, json=body))
                    resp = r.pop('result')
                    is_err = None
                    if resp is not None and resp.status_code == 200:
                        res = resp.json().get('result', {})
                        is_err = (res.get('content') or [{}])[0].get('text', '')[:100] if res.get('isError') else None
                    r.update({'surface': 'mcp', 'name': tool['name'], 'bytes': len(resp.content) if resp else 0,
                              'error': r['error'] or is_err})
                    out.append(r)
    finally:
        await token.delete()
    return out


async def measure_workers() -> list[dict]:
    out = []
    for name, module in WORKERS.items():
        tick = getattr(importlib.import_module(module), '_tick', None)
        if tick is None:
            out.append({'surface': 'worker', 'name': name, 'skipped': 'no _tick'})
            continue
        # Twice: a first tick often does one-off catch-up work (grants to hand
        # out, reminders never sent) that the steady state never repeats.
        for label in ('first', 'steady'):
            r = await timed(tick)
            r.pop('result')
            r.update({'surface': 'worker', 'name': f'{name} ({label})'})
            out.append(r)
    return out


async def run(only: str | None, slug: str, out_path: str | None) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from migrations.tortoise_config import TORTOISE_ORM
    from models import Tenant

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        tenant = await Tenant.get(slug=slug)
        from application.tenant_context import tenant_scope
        with tenant_scope(tenant.id):
            ids = await ids_for(tenant.id)
        rows: list[dict] = []
        if only in (None, 'rest'):
            rows += await measure_rest(tenant, ids)
        if only in (None, 'mcp'):
            rows += await measure_mcp(tenant, ids)
        if only in (None, 'workers'):
            rows += await measure_workers()
        for r in rows:
            if 'skipped' in r:
                continue
            flag = ' !' if (r.get('error') or (r.get('status') or 200) >= 400) else ''
            print(f"{r['surface']:7s} {r['name'][:60]:60s} {r['ms']:9.1f}ms {r['queries']:6d}q "
                  f"{r.get('bytes', 0):>9}B {r.get('status') or ''}{flag} {r.get('error') or ''}")
        skipped = [r for r in rows if 'skipped' in r]
        print(f'\n{len(skipped)} skipped:', ', '.join(f"{r['name']} ({r['skipped']})" for r in skipped))
        if out_path:
            with open(out_path, 'w') as fh:  # noqa: ASYNC230 - a CLI script, after the run
                json.dump(rows, fh, indent=1, default=str)
    finally:
        await Tortoise.close_connections()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', choices=('rest', 'mcp', 'workers'))
    ap.add_argument('--tenant', default='default')
    ap.add_argument('--json')
    a = ap.parse_args()
    asyncio.run(run(a.only, a.tenant, a.json))
    sys.exit(0)
