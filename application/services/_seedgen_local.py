"""The randomizers a player runs on their own machine: Zelda 1, Zelda 2, SMB3R
and Aria of Sorrow. A roll is a fresh seed beside the preset's flags (or inside
its link); nothing goes upstream, so ``MOCK_SEEDGEN`` leaves these alone.

Final Fantasy's flags-link check lives here too: that game is also built in the
player's browser, though its roll is mocked like the hosted ones.
"""

import random
import re
import urllib.parse
from typing import Optional

from application.services._seedgen_types import RolledSeed
from application.utils.seed_provider import SeedProviderInvalidRequest
from models import Preset

FF1R_HOST = 'finalfantasyrandomizer.com'


def ff1r_reference_url(settings: Optional[dict]) -> str:
    """The FFR flags link a preset holds, checked, or ``ValueError`` naming what's wrong."""
    url = settings.get('url') if isinstance(settings, dict) else None
    if not isinstance(url, str) or not url.strip():
        raise ValueError('A Final Fantasy preset needs a "url": the flags link copied from finalfantasyrandomizer.com.')
    unknown = set(settings or {}) - {'url'}
    if unknown:
        raise ValueError(f"A Final Fantasy preset holds only a flags link, not {', '.join(sorted(unknown))}.")
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not (host == FF1R_HOST or host.endswith('.' + FF1R_HOST)):
        raise ValueError('That isn\'t a finalfantasyrandomizer.com link. Copy the link from the site\'s address bar.')
    if not urllib.parse.parse_qs(parsed.query).get('f', [''])[0]:
        raise ValueError('That link has no flags in it (the "f=" part). Copy it again after setting the flags.')
    return url


FLAG_GAMES = {'z1r': 'Zelda 1', 'z2r': 'Zelda 2', 'smb3r': 'Super Mario Bros. 3'}


def flag_string(randomizer: str, settings: Optional[dict]) -> str:
    """The flag string a preset holds, checked, or ``ValueError`` naming what's wrong."""
    game = FLAG_GAMES[randomizer]
    flags = settings.get('flags') if isinstance(settings, dict) else None
    if not isinstance(flags, str) or not flags.strip():
        raise ValueError(f'A {game} preset needs "flags": the flag string from the randomizer.')
    unknown = set(settings or {}) - {'flags'}
    if unknown:
        raise ValueError(f"A {game} preset holds only flags, not {', '.join(sorted(unknown))}.")
    flags = flags.strip()
    if re.search(r'\s', flags):
        raise ValueError(f'{game} flags are one string with no spaces.')
    return flags


FLAG_SEED_RANGES = {
    'z1r': (0, 8999999999999999999),
    'z2r': (0, 1000000000),
    'smb3r': (0, 999999999999),
}

_AOSR_HOST = 'aosrando.surge.sh'


def aosr_reference_url(settings: Optional[dict]) -> str:
    """The Aria of Sorrow link a preset holds, checked, or ``ValueError``."""
    url = settings.get('url') if isinstance(settings, dict) else None
    if not isinstance(url, str) or not url.strip():
        raise ValueError('An Aria of Sorrow preset needs a "url": the randomizer link with its options.')
    unknown = set(settings or {}) - {'url'}
    if unknown:
        raise ValueError(f"An Aria of Sorrow preset holds only a link, not {', '.join(sorted(unknown))}.")
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or (parsed.hostname or '').lower() != _AOSR_HOST:
        raise ValueError(f"That isn't an {_AOSR_HOST} link.")
    return url


def roll_flag_game(randomizer: str, preset: Optional[Preset]) -> RolledSeed:
    """A fresh seed number beside the preset's flags.

    The player types both into their own copy of the randomizer. With no
    bundled fallback, a tournament without a preset fails loudly.
    """
    try:
        if preset is None:
            raise ValueError(
                f'{FLAG_GAMES[randomizer]} needs a preset with flags. Add one on the Presets tab.'
            )
        flags = flag_string(randomizer, preset.settings)
    except ValueError as e:
        raise SeedProviderInvalidRequest(str(e), provider=randomizer, operation='generate_seed') from None
    seed = random.randint(*FLAG_SEED_RANGES[randomizer])
    return RolledSeed(
        url=f"{seed} - {flags}", settings={'seed': str(seed), 'flags': flags},
    )


def roll_aosr(preset: Optional[Preset]) -> RolledSeed:
    """An Aria of Sorrow Randomizer link, purely local.

    aosrando.surge.sh reads its options and seed from the link and patches the
    player's own ROM in the browser, so the preset's link gets a fresh seed.
    """
    try:
        if preset is None:
            raise ValueError('Aria of Sorrow needs a preset with a randomizer link. Add one on the Presets tab.')
        reference = aosr_reference_url(preset.settings)
    except ValueError as e:
        raise SeedProviderInvalidRequest(str(e), provider='aosr', operation='generate_seed') from None
    seed = random.randint(-2147483648, 2147483647)
    up = urllib.parse.urlparse(reference)
    qs = urllib.parse.parse_qs(up.query, keep_blank_values=True)
    qs['seed'] = [str(seed)]
    newurl = urllib.parse.urlunparse((up.scheme, up.netloc, up.path or '/', up.params,
                                      urllib.parse.urlencode(qs, doseq=True), up.fragment))
    return RolledSeed(url=newurl, settings={'seed': str(seed), 'url': reference})
