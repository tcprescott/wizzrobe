"""Reading a ROM patch file well enough to host it: which format, and which ROM.

The patching itself happens in the player's browser (``static/js/rom-patcher``).
This side only refuses what isn't a patch and reads the CRC32 of the ROM a
BPS or UPS patch was made for, which both formats store in their footer. IPS
names no ROM, so staff may type the CRC instead.
"""

import re
import zlib
from dataclasses import dataclass
from typing import Optional

MAX_PATCH_BYTES = 16 * 1024 * 1024
FORMATS = ('bps', 'ips', 'ups')

_CRC = re.compile(r'^[0-9A-Fa-f]{8}$')


@dataclass(frozen=True)
class PatchInfo:
    format: str
    source_crc: Optional[str]


def _crc_hex(value: int) -> str:
    return f'{value:08X}'


def normalize_crc(value: Optional[str]) -> Optional[str]:
    """``'d63ed5f8'`` → ``'D63ED5F8'``; blank → ``None``; anything else is refused."""
    value = (value or '').strip().removeprefix('0x').removeprefix('0X')
    if not value:
        return None
    if not _CRC.match(value):
        raise ValueError('A ROM CRC32 is 8 hex digits, like D63ED5F8.')
    return value.upper()


def _checked_footer(data: bytes, name: str) -> PatchInfo:
    if len(data) < 16:
        raise ValueError(f'That {name.upper()} patch is cut short.')
    stored = int.from_bytes(data[-4:], 'little')
    if zlib.crc32(data[:-4]) != stored:
        raise ValueError(f"That {name.upper()} patch is damaged: its checksum doesn't match.")
    return PatchInfo(name, _crc_hex(int.from_bytes(data[-12:-8], 'little')))


def _checked_ips(data: bytes) -> PatchInfo:
    at = 5
    end = len(data)
    while True:
        if at + 3 > end:
            raise ValueError('That IPS patch is cut short.')
        if data[at:at + 3] == b'EOF' and end - at in (3, 6):
            return PatchInfo('ips', None)
        if at + 5 > end:
            raise ValueError('That IPS patch is cut short.')
        size = int.from_bytes(data[at + 3:at + 5], 'big')
        at += 5
        at += 3 if size == 0 else size
        if at > end:
            raise ValueError('That IPS patch is cut short.')


def read_patch(data: bytes) -> PatchInfo:
    """The patch's format and source CRC, or ``ValueError`` saying what's wrong."""
    if not data:
        raise ValueError('That file is empty.')
    if len(data) > MAX_PATCH_BYTES:
        raise ValueError(f'Patches can be up to {MAX_PATCH_BYTES // (1024 * 1024)} MB.')
    if data.startswith(b'BPS1'):
        return _checked_footer(data, 'bps')
    if data.startswith(b'UPS1'):
        return _checked_footer(data, 'ups')
    if data.startswith(b'PATCH'):
        return _checked_ips(data)
    raise ValueError("That isn't a BPS, IPS or UPS patch.")
