"""HTTP front for Aerinon's ALttP Door Randomizer.

Wizzrobe calls this over the private compose network. It never sees a ROM:
the randomizer runs with ``--jsonout``, which emits the seed as byte writes on
top of the randomizer's own base patch (``data/base2current.bps``). The
player's browser applies both to their own Japanese 1.0 ROM.

Endpoints:
    GET  /health      version and commit
    GET  /base-patch  the base BPS the seed patches sit on (per version)
    POST /generate    {"settings": {...}} -> {"version", "seed", "patch", "spoiler", "attempts"}
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

from aiohttp import web

DR_DIR = Path(os.environ.get('DOOR_RANDO_DIR', '/opt/door-rando'))
DR_COMMIT = os.environ.get('DOOR_RANDO_COMMIT', 'unknown')
TOKEN = os.environ.get('DOOR_RANDO_TOKEN', '')
MAX_CONCURRENT = int(os.environ.get('DOOR_RANDO_CONCURRENCY', '2'))
MAX_QUEUED = int(os.environ.get('DOOR_RANDO_MAX_QUEUED', '8'))
ATTEMPTS = int(os.environ.get('DOOR_RANDO_ATTEMPTS', '6'))
ATTEMPT_TIMEOUT = float(os.environ.get('DOOR_RANDO_ATTEMPT_TIMEOUT', '60'))
MAX_BODY = 64 * 1024

# Settings that name files, change how many worlds or ROMs come out, or pick
# cosmetics the browser applies afterwards. The service sets or ignores these.
_FORCED = {
    'multi': 1,
    'count': 1,
    'names': '',
    'create_rom': True,
    'calc_playthrough': True,
    'bps': False,
}
_BLOCKED = {
    'rom', 'patch', 'outputpath', 'outputname', 'settingsfile', 'customizer',
    'enemizercli', 'sprite', 'print_custom_yaml', 'jsonout', 'suppress_rom',
    'suppress_meta', 'lang', 'saveonexit', 'mystery',
}

log = logging.getLogger('door_rando')


def _load_defaults() -> tuple[str, dict, dict]:
    sys.path.insert(0, str(DR_DIR))
    cwd = os.getcwd()
    os.chdir(DR_DIR)
    try:
        import CLI  # type: ignore[import-not-found]
        import Main  # type: ignore[import-not-found]
        args = json.loads((DR_DIR / 'resources' / 'app' / 'cli' / 'args.json').read_text(encoding='utf-8'))
        return Main.__version__, CLI.parse_settings(), args
    finally:
        os.chdir(cwd)


VERSION, DEFAULTS, ARG_SPECS = _load_defaults()
ALLOWED = set(DEFAULTS) - _BLOCKED - set(_FORCED)


class SettingsError(ValueError):
    pass


def clean_settings(raw: object) -> dict:
    """The caller's settings, checked against the randomizer's own keys."""
    if not isinstance(raw, dict):
        raise SettingsError('"settings" must be an object.')
    unknown = sorted(k for k in raw if k not in ALLOWED and k not in _FORCED)
    if unknown:
        raise SettingsError(f"Unknown or not allowed settings: {', '.join(unknown)}")
    settings = {}
    for key, value in raw.items():
        if key in _FORCED:
            continue
        default = DEFAULTS[key]
        if isinstance(default, dict):
            if not isinstance(value, dict) or not all(isinstance(v, (int, str, bool)) for v in value.values()):
                raise SettingsError(f'"{key}" must be an object of plain values.')
        elif not isinstance(value, (str, int, float, bool)) and value is not None:
            raise SettingsError(f'"{key}" must be a plain value.')
        spec = ARG_SPECS.get(key) or {}
        choices = spec.get('choices')
        if choices and str(value).lower() not in [str(c).lower() for c in choices]:
            raise SettingsError(f'"{key}" is one of {", ".join(map(str, choices))}, not {value!r}.')
        if spec.get('type') == 'bool' and not isinstance(value, bool):
            raise SettingsError(f'"{key}" is true or false, not {value!r}.')
        settings[key] = value
    settings.update(_FORCED)
    return settings


class Busy(Exception):
    pass


class GenerationFailed(Exception):
    pass


class Generator:
    def __init__(self) -> None:
        self._slots = asyncio.Semaphore(MAX_CONCURRENT)
        self._waiting = 0

    async def generate(self, settings: dict) -> dict:
        if self._waiting >= MAX_QUEUED:
            raise Busy()
        self._waiting += 1
        acquired = False
        try:
            await self._slots.acquire()
            acquired = True
        finally:
            self._waiting -= 1
        try:
            return await self._generate(settings)
        finally:
            if acquired:
                self._slots.release()

    async def _generate(self, settings: dict) -> dict:
        last_error = ''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'settings.json'
            path.write_text(json.dumps(settings), encoding='utf-8')
            for attempt in range(1, ATTEMPTS + 1):
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, 'DungeonRandomizer.py', '--settingsfile', str(path), '--jsonout',
                    cwd=DR_DIR,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=ATTEMPT_TIMEOUT)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    last_error = f'timed out after {ATTEMPT_TIMEOUT:.0f}s'
                    log.warning('attempt %d %s', attempt, last_error)
                    continue
                if proc.returncode != 0:
                    lines = stderr.decode(errors='replace').strip().splitlines()
                    last_error = lines[-1] if lines else f'exit code {proc.returncode}'
                    log.info('attempt %d failed: %s', attempt, last_error)
                    continue
                return self._parse(stdout, attempt)
        raise GenerationFailed(last_error)

    @staticmethod
    def _parse(stdout: bytes, attempts: int) -> dict:
        # Logging and the JSON share stdout; the JSON is the last line.
        line = stdout.decode(errors='replace').strip().splitlines()[-1]
        data = json.loads(line)
        spoiler = data.get('spoiler')
        if isinstance(spoiler, str):
            spoiler = json.loads(spoiler)
        meta = spoiler.get('meta', {}) if isinstance(spoiler, dict) else {}
        return {
            'version': VERSION,
            'commit': DR_COMMIT,
            'seed': meta.get('seed'),
            'patch': data['patch_t0_p1'],
            'spoiler': spoiler,
            'attempts': attempts,
        }


def _authorized(request: web.Request) -> bool:
    if not TOKEN:
        return True
    supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
    return hmac.compare_digest(supplied, TOKEN)


@web.middleware
async def auth(request: web.Request, handler):
    if request.path != '/health' and not _authorized(request):
        return web.json_response({'error': 'unauthorized'}, status=401)
    return await handler(request)


async def health(_request: web.Request) -> web.Response:
    return web.json_response({'status': 'ok', 'version': VERSION, 'commit': DR_COMMIT})


async def base_patch(_request: web.Request) -> web.Response:
    body = (DR_DIR / 'data' / 'base2current.bps').read_bytes()
    return web.Response(body=body, content_type='application/octet-stream', headers={'X-Door-Rando-Version': VERSION})


async def generate(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        settings = clean_settings(body.get('settings') if isinstance(body, dict) else None)
    except (json.JSONDecodeError, SettingsError) as e:
        return web.json_response({'error': str(e)}, status=400)
    try:
        result = await request.app['generator'].generate(settings)
    except Busy:
        return web.json_response({'error': 'busy'}, status=503, headers={'Retry-After': '5'})
    except GenerationFailed as e:
        return web.json_response({'error': f'Generation failed after {ATTEMPTS} attempts: {e}'}, status=422)
    return web.json_response(result)


def make_app() -> web.Application:
    app = web.Application(middlewares=[auth], client_max_size=MAX_BODY)
    app['generator'] = Generator()
    app.router.add_get('/health', health)
    app.router.add_get('/base-patch', base_patch)
    app.router.add_post('/generate', generate)
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    web.run_app(make_app(), host='0.0.0.0', port=int(os.environ.get('PORT', '8080')))
