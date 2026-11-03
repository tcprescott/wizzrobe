"""The Super Metroid VARIA Randomizer backend (randommetroidsolver.pythonanywhere.com).

VARIA builds seeds server-side from two named presets: a *settings* preset (item
split, progression, area rando...) and a *skills* preset (which techniques the
logic may expect). A ``Preset`` here names the settings preset, and may name a
skills preset and override single settings:

    {"settings_preset": "Season_Races", "skills_preset": "Season_Races",
     "overrides": {"areaRandomization": "on"}}

Without ``skills_preset`` the roll uses the one the settings preset names itself.
A roll fetches both presets, lays them over VARIA's ``default`` settings (the
service fills any parameter still missing with its own default, so a new
upstream parameter never breaks a roll), and posts the lot as a race seed. The
answer carries the IPS patch, which is where the seed code comes from.
Each generate call stores a seed upstream, so a roll gets one attempt.
"""

import json
import re
from typing import Optional

import aiohttp

import application.services._seedgen_codes as seed_codes
from application.services._seedgen_types import RolledSeed
from application.utils.seed_provider import (
    SeedProviderBadResponse,
    SeedProviderInvalidRequest,
    raise_for_status,
)

VARIA_BASE_URL = 'https://randommetroidsolver.pythonanywhere.com'
# The service caps the randomizer at 20 seconds of work; the rest is upload and patch.
VARIA_GENERATION_TIMEOUT = 90.0

# web2py's IS_ALPHANUMERIC, which the preset endpoints apply to the name.
_PRESET_NAME = re.compile(r'[A-Za-z0-9_]{1,32}')
_SETTING_NAME = re.compile(r'[A-Za-z0-9_]{1,64}')
_SEED_KEY = re.compile(r'[0-9a-fA-F-]{36}')
_PRESET_KEYS = {'settings_preset', 'skills_preset', 'overrides'}
# Parameters a roll owns: a fixed seed would repeat a seed, and race mode off
# would hand out a seed whose spoiler the solver can read back.
_ROLL_OWNED = {'seed', 'raceMode', 'paramsFileTarget', 'preset'}


def _name(settings: dict, key: str, required: bool) -> Optional[str]:
    value = settings.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _PRESET_NAME.fullmatch(value.strip()):
        raise ValueError(
            f'A VARIA preset\'s "{key}" is one of VARIA\'s preset names, letters, '
            'numbers and _ only, e.g. "Season_Races".'
        )
    return value.strip()


def varia_settings(settings: Optional[dict]) -> dict:
    """A preset's VARIA settings, checked, or ``ValueError``."""
    if not isinstance(settings, dict) or 'settings_preset' not in settings:
        raise ValueError(
            'A VARIA preset needs a "settings_preset": the name of a VARIA settings preset, e.g. "Season_Races".'
        )
    unknown = set(settings) - _PRESET_KEYS
    if unknown:
        raise ValueError(
            f"A VARIA preset holds settings_preset, skills_preset and overrides, not {', '.join(sorted(unknown))}."
        )
    checked: dict = {'settings_preset': _name(settings, 'settings_preset', True)}
    skills = _name(settings, 'skills_preset', False)
    if skills:
        checked['skills_preset'] = skills

    overrides = settings.get('overrides')
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise ValueError('A VARIA preset\'s "overrides" is an object of setting names and values.')
        for key, value in overrides.items():
            if not isinstance(key, str) or not _SETTING_NAME.fullmatch(key):
                raise ValueError(f'"{key}" is not a VARIA setting name.')
            if key in _ROLL_OWNED:
                raise ValueError(f'"{key}" is set by each roll, so a preset can\'t override it.')
            if isinstance(value, list):
                if not all(isinstance(v, str) for v in value):
                    raise ValueError(f'"{key}" lists text values only.')
            elif isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise ValueError(f'"{key}" is text, a number, or a list of text values.')
        if overrides:
            checked['overrides'] = dict(overrides)
    return checked


def _form_value(value) -> str:
    if isinstance(value, list):
        return ','.join(str(v) for v in value)
    return str(value)


async def _post_json(session: aiohttp.ClientSession, path: str, data: dict, operation: str):
    async with session.post(f'{VARIA_BASE_URL}/{path}', data=data) as resp:
        await raise_for_status(resp, provider='smvaria', operation=operation)
        try:
            return await resp.json(content_type=None)
        except (json.JSONDecodeError, aiohttp.ContentTypeError):
            raise SeedProviderBadResponse(
                'VARIA sent back something that is not JSON.', provider='smvaria', operation=operation,
            ) from None


async def _settings_preset(session: aiohttp.ClientSession, name: str) -> dict:
    body = await _post_json(
        session, 'randoPresetWebService', {'randoPreset': name, 'origin': 'extStats'}, 'generate_seed',
    )
    if not isinstance(body, dict):
        raise SeedProviderBadResponse(
            f'VARIA sent back no settings for "{name}".', provider='smvaria', operation='generate_seed',
        )
    return body


async def generate_varia(settings: Optional[dict]) -> RolledSeed:
    """Roll one VARIA race seed from the preset's settings."""
    try:
        checked = varia_settings(settings)
    except ValueError as e:
        raise SeedProviderInvalidRequest(str(e), provider='smvaria', operation='generate_seed') from None

    timeout = aiohttp.ClientTimeout(total=VARIA_GENERATION_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        form = await _settings_preset(session, 'default')
        if checked['settings_preset'] != 'default':
            form.update(await _settings_preset(session, checked['settings_preset']))
        form.update(checked.get('overrides', {}))

        skills_name = checked.get('skills_preset') or form.get('preset') or 'regular'
        if not isinstance(skills_name, str) or not _PRESET_NAME.fullmatch(skills_name):
            skills_name = 'regular'
        skills = await _post_json(session, 'presetWebService', {'preset': skills_name}, 'generate_seed')

        form.setdefault('complexity', 'advanced')
        form.setdefault('logic', 'vanilla')
        form.update(preset=skills_name, raceMode='on', seed='0')
        payload = {k: _form_value(v) for k, v in form.items()}
        payload['paramsFileTarget'] = json.dumps(skills)

        result = await _post_json(session, 'randomizerWebService', payload, 'generate_seed')

    if not isinstance(result, dict) or result.get('status') != 'OK':
        message = result.get('errorMsg') if isinstance(result, dict) else None
        raise SeedProviderInvalidRequest(
            'VARIA could not build a seed with these settings.',
            provider='smvaria', operation='generate_seed',
            provider_message=str(message).replace('<br/>', '\n') if message else None,
        )
    seed_key = result.get('seedKey')
    if not isinstance(seed_key, str) or not _SEED_KEY.fullmatch(seed_key):
        raise SeedProviderBadResponse(
            'VARIA built a seed but sent back no link to it.', provider='smvaria', operation='generate_seed',
        )

    sent = {k: v for k, v in payload.items() if k != 'paramsFileTarget'}
    sent['skills_preset'] = skills_name
    return RolledSeed(
        url=f'{VARIA_BASE_URL}/customizer/{seed_key}',
        settings=sent,
        code=seed_codes.varia_code(result.get('ips')),
    )
