"""The alttpr.com sprite list, for the door seed patcher's sprite picker.

alttpr.com sends no CORS header, so the browser can't read the list itself;
Wizzrobe fetches it and serves a trimmed copy. The sprite files and previews
live on the alttpr-assets S3 bucket, which does allow cross-origin reads, so
the browser fetches those directly. Entries pointing anywhere else are dropped,
which keeps the page from being steered to fetch from an arbitrary host.
"""

from __future__ import annotations

from typing import Any, List

import aiohttp

SPRITES_URL = 'https://alttpr.com/sprites'
ASSET_PREFIX = 'https://alttpr-assets.s3.us-east-2.amazonaws.com/'
# Link's own sprite is listed but not served (403); picking it means "leave
# the ROM's sprite alone", so the patcher never fetches it.
DEFAULT_SPRITE_FILE = ASSET_PREFIX + '001.link.1.zspr'
TIMEOUT = 15.0


class SpriteListError(Exception):
    pass


def trim_sprite_list(payload: Any) -> List[dict]:
    if not isinstance(payload, list):
        raise SpriteListError('The sprite list was not a list.')
    sprites = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name, file, preview = entry.get('name'), entry.get('file'), entry.get('preview')
        if not isinstance(name, str) or not isinstance(file, str) or not file.startswith(ASSET_PREFIX):
            continue
        if not isinstance(preview, str) or not preview.startswith(ASSET_PREFIX):
            preview = None
        tags = entry.get('tags')
        sprites.append({
            'default': file == DEFAULT_SPRITE_FILE,
            'name': name[:80],
            'author': str(entry.get('author') or '')[:80],
            'file': file,
            'preview': preview,
            'tags': [t for t in tags if isinstance(t, str)][:10] if isinstance(tags, list) else [],
        })
    return sprites


async def fetch_sprite_list() -> List[dict]:
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(SPRITES_URL) as resp:
                if resp.status != 200:
                    raise SpriteListError(f'alttpr.com answered {resp.status}.')
                payload = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise SpriteListError(str(e)) from e
    return trim_sprite_list(payload)
