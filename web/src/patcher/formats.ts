/**
 * BPS, IPS and UPS patches, behind one interface.
 *
 * patchInfo(patch) says which format a file is and the CRC32 of the ROM it was
 * made for (BPS and UPS store it; IPS doesn't). matchRom(rom, crc) finds the
 * bytes the patch expects, dropping a 512-byte copier header if that is what
 * stands between them. applyPatch(patch, rom) returns the patched ROM. Every
 * failure is a PatchError whose message can be shown to a player.
 */

import { applyBps, BpsError, crc32, readU32 } from './bps'

export { crc32 };

export class PatchError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message)
  }
}

export type PatchFormat = 'bps' | 'ips' | 'ups'

export function crcHex(value: number): string {
  return value.toString(16).toUpperCase().padStart(8, '0');
}

function startsWith(bytes: Uint8Array, text: string): boolean {
  if (bytes.length < text.length) return false;
  for (let i = 0; i < text.length; i++) if (bytes[i] !== text.charCodeAt(i)) return false
  return true;
}

export function patchFormat(patch: Uint8Array): PatchFormat | null {
  if (startsWith(patch, 'BPS1')) return 'bps';
  if (startsWith(patch, 'UPS1')) return 'ups';
  if (startsWith(patch, 'PATCH')) return 'ips';
  return null;
}

/** {format, sourceCrc}: sourceCrc is a number, or null when the format names no ROM. */
export function patchInfo(patch: Uint8Array): { format: PatchFormat; sourceCrc: number | null } {
  const format = patchFormat(patch);
  if (!format) throw new PatchError('patch', "That file isn't a BPS, IPS or UPS patch.");
  if (format === 'ips') return { format, sourceCrc: null };
  if (patch.length < 16 || crc32(patch, 0, patch.length - 4) !== readU32(patch, patch.length - 4)) {
    throw new PatchError('patch', "That patch is damaged: its checksum doesn't match.");
  }
  return { format, sourceCrc: readU32(patch, patch.length - 12) };
}

/**
 * The ROM as the patch wants it, or null if it is the wrong ROM. A dump with a
 * 512-byte copier header matches once the header is dropped.
 */
export function matchRom(rom: Uint8Array, sourceCrc: number | null): Uint8Array | null {
  if (sourceCrc == null) return rom;
  if (crc32(rom) === sourceCrc) return rom;
  if (rom.length % 0x400 === 0x200) {
    const bare = rom.subarray(0x200);
    if (crc32(bare) === sourceCrc) return bare;
  }
  return null;
}

export function wrongRom(rom: Uint8Array, sourceCrc: number): PatchError {
  return new PatchError(
    'source',
    `The ROM you picked is CRC32 ${crcHex(crc32(rom))}. This patch needs ${crcHex(sourceCrc)}, `
      + 'usually a different region or revision.',
  );
}

function varintReader(patch: Uint8Array, end: number) {
  let at = 4;
  return {
    get at() { return at },
    set at(v: number) { at = v },
    number() {
      let value = 0;
      let shift = 1;
      for (;;) {
        if (at >= end) throw new PatchError('patch', 'That patch is damaged.');
        const x = patch[at++]!;
        value += (x & 0x7F) * shift;
        if (x & 0x80) return value;
        shift *= 128;
        value += shift;
      }
    },
  };
}

export function applyUps(patch: Uint8Array, rom: Uint8Array): Uint8Array {
  const end = patch.length - 12;
  const reader = varintReader(patch, end);
  const sourceSize = reader.number();
  const targetSize = reader.number();
  if (rom.length !== sourceSize) throw wrongRom(rom, readU32(patch, end))
  const target = new Uint8Array(targetSize);
  target.set(rom.subarray(0, Math.min(rom.length, targetSize)));
  let out = 0;
  while (reader.at < end) {
    out += reader.number();
    let at = reader.at;
    for (;;) {
      if (at >= end) throw new PatchError('patch', 'That patch is damaged.');
      const x = patch[at++]!;
      if (x === 0) break;
      if (out < targetSize) target[out] = (out < rom.length ? rom[out]! : 0) ^ x;
      out++;
    }
    out++;
    reader.at = at;
  }
  if (crc32(target) !== readU32(patch, end + 4)) {
    throw new PatchError('target', 'The patched ROM failed its check.');
  }
  return target;
}

export function applyIps(patch: Uint8Array, rom: Uint8Array): Uint8Array {
  const records: { offset: number; length: number; fill?: number; data?: Uint8Array }[] = []
  let size = rom.length;
  let truncate: number | null = null
  let at = 5;
  for (;;) {
    if (at + 3 > patch.length) throw new PatchError('patch', 'That IPS patch is cut short.');
    const rest = patch.length - at;
    if (patch[at] === 0x45 && patch[at + 1] === 0x4F && patch[at + 2] === 0x46 && (rest === 3 || rest === 6)) {
      if (rest === 6) truncate = (patch[at + 3]! << 16) | (patch[at + 4]! << 8) | patch[at + 5]!;
      break;
    }
    if (at + 5 > patch.length) throw new PatchError('patch', 'That IPS patch is cut short.');
    const offset = (patch[at]! << 16) | (patch[at + 1]! << 8) | patch[at + 2]!;
    let length = (patch[at + 3]! << 8) | patch[at + 4]!;
    at += 5;
    if (length === 0) {
      if (at + 3 > patch.length) throw new PatchError('patch', 'That IPS patch is cut short.');
      length = (patch[at]! << 8) | patch[at + 1]!;
      records.push({ offset, length, fill: patch[at + 2]! });
      at += 3;
    } else {
      if (at + length > patch.length) throw new PatchError('patch', 'That IPS patch is cut short.');
      records.push({ offset, length, data: patch.subarray(at, at + length) });
      at += length;
    }
    size = Math.max(size, offset + length);
  }
  const target = new Uint8Array(truncate ?? size);
  target.set(rom.subarray(0, Math.min(rom.length, target.length)));
  for (const r of records) {
    const stop = Math.min(r.offset + r.length, target.length);
    if (r.offset >= stop) continue;
    if (r.data) target.set(r.data.subarray(0, stop - r.offset), r.offset);
    else target.fill(r.fill ?? 0, r.offset, stop)
  }
  return target;
}

/** Patch a ROM. `rom` is what the player picked; a header is dropped if the patch needs that. */
export function applyPatch(patch: Uint8Array, rom: Uint8Array): Uint8Array {
  const { format, sourceCrc } = patchInfo(patch);
  const source = matchRom(rom, sourceCrc);
  if (!source) throw wrongRom(rom, sourceCrc as number);
  if (format === 'ips') return applyIps(patch, source);
  if (format === 'ups') return applyUps(patch, source);
  try {
    return applyBps(patch, source);
  } catch (e) {
    if (e instanceof BpsError) throw new PatchError(e.code, e.code === 'target' ? 'The patched ROM failed its check.' : 'That patch is damaged.');
    throw e;
  }
}
