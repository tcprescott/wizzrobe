/**
 * BPS patch apply (format: https://www.romhacking.net/documents/746/).
 *
 * applyBps(patch, source) returns the patched bytes, or throws a BpsError
 * whose `code` says which check failed: 'patch' (not a BPS file, or corrupt),
 * 'source' (the ROM is not the one the patch was made from), 'target'.
 */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

export function crc32(bytes: Uint8Array, start = 0, end = bytes.length): number {
  let crc = 0xffffffff
  for (let i = start; i < end; i++) crc = CRC_TABLE[(crc ^ bytes[i]!) & 0xff]! ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

export type BpsErrorCode = 'patch' | 'source' | 'target'

export class BpsError extends Error {
  constructor(
    readonly code: BpsErrorCode,
    message: string,
  ) {
    super(message)
  }
}

export function readU32(bytes: Uint8Array, at: number): number {
  return (bytes[at]! | (bytes[at + 1]! << 8) | (bytes[at + 2]! << 16) | (bytes[at + 3]! << 24)) >>> 0
}

/** The CRC32 the patch expects its source file to have. */
export function bpsSourceCrc(patch: Uint8Array): number {
  return readU32(patch, patch.length - 12)
}

export function applyBps(patch: Uint8Array, source: Uint8Array): Uint8Array {
  const end = patch.length - 12
  if (end < 4 || patch[0] !== 0x42 || patch[1] !== 0x50 || patch[2] !== 0x53 || patch[3] !== 0x31) {
    throw new BpsError('patch', 'Not a BPS patch.')
  }
  if (crc32(patch, 0, patch.length - 4) !== readU32(patch, patch.length - 4)) {
    throw new BpsError('patch', 'The patch is corrupt.')
  }
  if (crc32(source) !== readU32(patch, end)) {
    throw new BpsError('source', 'The ROM does not match the one this patch was made for.')
  }

  let at = 4
  const number = () => {
    let value = 0
    let shift = 1
    for (;;) {
      if (at >= end) throw new BpsError('patch', 'The patch is corrupt.')
      const x = patch[at++]!
      value += (x & 0x7f) * shift
      if (x & 0x80) return value
      shift *= 128
      value += shift
    }
  }
  const signed = () => {
    const n = number()
    return (n & 1 ? -1 : 1) * Math.floor(n / 2)
  }

  number()
  const target = new Uint8Array(number())
  const metadataLength = number()
  at += metadataLength

  let out = 0
  let sourceRel = 0
  let targetRel = 0
  while (at < end) {
    const data = number()
    const action = data & 3
    const length = Math.floor(data / 4) + 1
    if (out + length > target.length) throw new BpsError('patch', 'The patch is corrupt.')
    if (action === 0) {
      target.set(source.subarray(out, out + length), out)
      out += length
    } else if (action === 1) {
      target.set(patch.subarray(at, at + length), out)
      at += length
      out += length
    } else if (action === 2) {
      sourceRel += signed()
      target.set(source.subarray(sourceRel, sourceRel + length), out)
      sourceRel += length
      out += length
    } else {
      targetRel += signed()
      // Byte by byte: a target copy may overlap what it is writing (RLE).
      for (let i = 0; i < length; i++) target[out++] = target[targetRel++]!
    }
  }
  if (crc32(target) !== readU32(patch, end + 4)) {
    throw new BpsError('target', 'The patched ROM failed its check.')
  }
  return target
}
