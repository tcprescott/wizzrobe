"""The AVIANART backend (avianart.games): A Link to the Past, including door rando.

AVIANART rolls its own named presets server-side, so a ``Preset`` here holds one
name (``{"preset": "crosskeys"}``, or a user preset like ``"kaesden/casualboots"``).
A roll is a generate call that answers "generating" with a hash, then a few polls
of that hash until the game is built. Retrying the whole roll would build a second
game, so it gets one attempt and its polls run inside it.
"""

import asyncio
import re
from typing import List, Optional

import aiohttp

import application.services._seedgen_codes as seed_codes
from application.services._seedgen_types import RemotePreset, RolledSeed
from application.utils.seed_provider import (
    SeedProviderBadResponse,
    SeedProviderInvalidRequest,
    raise_for_status,
)

AVIANART_API = 'https://avianart.games/api.php'
AVIANART_PERMALINK = 'https://avianart.games/perm/{hash}'
AVIANART_POLL_INTERVAL = 3.0
AVIANART_MAX_POLLS = 40

_PRESET_NAME = re.compile(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?')
_HASH = re.compile(r'[A-Za-z0-9]+')


def avianart_preset_name(settings: Optional[dict]) -> str:
    """The AVIANART preset a ``Preset`` names, checked, or ``ValueError``."""
    name = settings.get('preset') if isinstance(settings, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ValueError('An AVIANART preset needs a "preset": the preset name from avianart.games, e.g. "crosskeys".')
    unknown = set(settings or {}) - {'preset'}
    if unknown:
        raise ValueError(f"An AVIANART preset holds only a preset name, not {', '.join(sorted(unknown))}.")
    name = name.strip()
    if not _PRESET_NAME.fullmatch(name):
        raise ValueError(
            'AVIANART preset names are letters, numbers, - and _, with an optional "owner/" in front.'
        )
    return name


def _response(body, operation: str) -> dict:
    inner = body.get('response') if isinstance(body, dict) else None
    if not isinstance(inner, dict):
        raise SeedProviderBadResponse(
            'AVIANART sent back something that is not a seed.',
            provider='avianart', operation=operation,
        )
    return inner


async def _poll_until_built(session: aiohttp.ClientSession, name: str, seed_hash: str) -> dict:
    for _ in range(AVIANART_MAX_POLLS):
        await asyncio.sleep(AVIANART_POLL_INTERVAL)
        async with session.get(
            AVIANART_API, params={'action': 'permlink', 'hash': seed_hash},
        ) as resp:
            await raise_for_status(resp, provider='avianart', operation='generate_seed')
            result = _response(await resp.json(content_type=None), 'generate_seed')

        status = result.get('status') or 'finished'
        if status == 'finished':
            return result
        if status == 'failure':
            raise SeedProviderInvalidRequest(
                f'AVIANART could not build a "{name}" seed. Check the preset name on avianart.games.',
                provider='avianart', operation='generate_seed',
                provider_message=result.get('message'),
            )
    raise SeedProviderBadResponse(
        f'AVIANART was still building the seed after {int(AVIANART_MAX_POLLS * AVIANART_POLL_INTERVAL)} seconds.',
        provider='avianart', operation='generate_seed',
    )


async def generate_avianart(settings: Optional[dict]) -> RolledSeed:
    """Roll one race seed of the named preset and wait for it to finish building."""
    try:
        name = avianart_preset_name(settings)
    except ValueError as e:
        raise SeedProviderInvalidRequest(str(e), provider='avianart', operation='generate_seed') from None

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
        async with session.post(
            AVIANART_API, params={'action': 'generate', 'preset': name},
            json=[{'args': {'race': True}}],
        ) as resp:
            await raise_for_status(resp, provider='avianart', operation='generate_seed')
            started = _response(await resp.json(content_type=None), 'generate_seed')

        seed_hash = started.get('hash')
        if not isinstance(seed_hash, str) or not _HASH.fullmatch(seed_hash):
            raise SeedProviderBadResponse(
                started.get('message') or 'AVIANART did not start a seed.',
                provider='avianart', operation='generate_seed',
            )
        result = await _poll_until_built(session, name, seed_hash)

    return RolledSeed(
        url=AVIANART_PERMALINK.format(hash=seed_hash),
        settings={'preset': name, 'race': True},
        code=seed_codes.avianart_code(result),
    )


async def avianart_presets() -> List[RemotePreset]:
    """AVIANART's published preset list, as ``Preset``-shaped entries."""
    async with aiohttp.request(method='get', url=AVIANART_API, params={'action': 'presets'}) as resp:
        await raise_for_status(resp, provider='avianart', operation='list_presets')
        modes = _response(await resp.json(content_type=None), 'list_presets').get('modes')
    if not isinstance(modes, list):
        raise SeedProviderBadResponse(
            'AVIANART sent back no preset list.', provider='avianart', operation='list_presets',
        )
    presets: List[RemotePreset] = []
    for mode in modes:
        prefix = mode.get('prefix') if isinstance(mode, dict) else None
        if not isinstance(prefix, str) or not _PRESET_NAME.fullmatch(prefix):
            continue
        title = mode.get('name') if isinstance(mode.get('name'), str) else prefix
        blurb = mode.get('description') if isinstance(mode.get('description'), str) else ''
        presets.append(RemotePreset(
            name=prefix,
            settings={'preset': prefix},
            description=f'{title}. {blurb}' if blurb else title,
        ))
    return presets
