/**
 * Builds a Door Randomizer seed ROM in the browser, from the player's own
 * Japanese 1.0 ROM. Nothing here touches the network or the page: the caller
 * fetches the base patch, the seed's writes and any sprite, and hands bytes in.
 *
 * The addresses mirror the randomizer's own Rom.apply_rom_settings, so a ROM
 * made here matches what the randomizer would write with the same settings.
 */
import { randomize as randomizePalettes } from '@maseya/z3pr'
import { applyBps, BpsError } from './bps'

const ROM_SIZE = 0x200000
const JP10_SIZE = 0x100000

export class RomError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message)
  }
}

export const CHOICES = {
  heartBeep: ['off', 'quarter', 'half', 'normal', 'double'],
  heartColor: ['red', 'blue', 'green', 'yellow', 'random'],
  menuSpeed: ['half', 'normal', 'double', 'triple', 'quadruple', 'instant'],
  owPalettes: ['default', 'shuffled', 'legacy', 'grayscale', 'blackout', 'puke'],
  uwPalettes: ['default', 'shuffled', 'legacy', 'grayscale', 'blackout', 'puke'],
} as const

export type ChoiceKey = keyof typeof CHOICES

export interface SpriteChoice {
  name: string
  file?: string
  author?: string
  preview?: string
  default?: boolean
  random?: boolean
}

export interface DoorOptions {
  heartBeep: (typeof CHOICES.heartBeep)[number]
  heartColor: (typeof CHOICES.heartColor)[number]
  menuSpeed: (typeof CHOICES.menuSpeed)[number]
  owPalettes: (typeof CHOICES.owPalettes)[number]
  uwPalettes: (typeof CHOICES.uwPalettes)[number]
  quickswap: boolean
  music: boolean
  reduceFlashing: boolean
  sprite: SpriteChoice | null
}

export const DEFAULT_OPTIONS: Readonly<DoorOptions> = Object.freeze({
  heartBeep: 'normal',
  heartColor: 'red',
  menuSpeed: 'normal',
  quickswap: true,
  music: true,
  reduceFlashing: false,
  owPalettes: 'default',
  uwPalettes: 'default',
  sprite: null,
})

/** Saved options read back safely: anything unknown falls back to the default. */
export function cleanOptions(raw: unknown): DoorOptions {
  const o: DoorOptions = { ...DEFAULT_OPTIONS }
  if (!raw || typeof raw !== 'object') return o
  const r = raw as Record<string, unknown>
  for (const key of Object.keys(CHOICES) as ChoiceKey[]) {
    const v = r[key]
    if (typeof v === 'string' && (CHOICES[key] as readonly string[]).includes(v)) (o as unknown as Record<string, string>)[key] = v
  }
  for (const key of ['quickswap', 'music', 'reduceFlashing'] as const) {
    if (typeof r[key] === 'boolean') o[key] = r[key]
  }
  const s = r.sprite
  if (s && typeof s === 'object' && typeof (s as SpriteChoice).name === 'string') {
    const c = s as SpriteChoice
    o.sprite = { name: c.name, file: typeof c.file === 'string' ? c.file : undefined, author: c.author, preview: c.preview, default: !!c.default, random: !!c.random }
  }
  return o
}

/** The player's ROM without a copier header, or a RomError saying why not. */
export function stripHeader(bytes: Uint8Array): Uint8Array {
  if (bytes.length % 0x400 === 0x200) bytes = bytes.subarray(0x200)
  if (bytes.length !== JP10_SIZE) {
    throw new RomError('size', 'That file is not a Link to the Past ROM: it is the wrong size.')
  }
  return bytes
}

export function buildSeedRom(romFile: Uint8Array, basePatch: Uint8Array, writes: Record<string, number[]>): Uint8Array {
  const source = stripHeader(romFile)
  let rom: Uint8Array
  try {
    rom = applyBps(basePatch, source)
  } catch (e) {
    if (e instanceof BpsError && e.code === 'source') {
      throw new RomError('version', 'That ROM is not the Japanese 1.0 release. Door Randomizer needs that one.')
    }
    throw e
  }
  if (rom.length < ROM_SIZE) {
    const grown = new Uint8Array(ROM_SIZE)
    grown.set(rom)
    rom = grown
  }
  for (const [address, bytes] of Object.entries(writes)) {
    const at = Number(address)
    if (!Number.isInteger(at) || at < 0 || !Array.isArray(bytes)) {
      throw new RomError('patch', 'The seed patch is damaged.')
    }
    if (at + bytes.length > rom.length) {
      const grown = new Uint8Array(at + bytes.length)
      grown.set(rom)
      rom = grown
    }
    rom.set(bytes, at)
  }
  return rom
}

const MENU_SPEED: Record<string, number> = { instant: 0xe8, double: 0x10, triple: 0x18, quadruple: 0x20, half: 0x04, normal: 0x08 }
const HEART_BEEP: Record<string, number> = { off: 0x00, half: 0x40, quarter: 0x80, normal: 0x20, double: 0x10 }
const HEART_COLOR: Record<string, number> = { red: 0, blue: 1, green: 2, yellow: 3 }
const PALETTE_MODE: Record<string, string> = {
  shuffled: 'maseya',
  legacy: 'classic',
  grayscale: 'grayscale',
  blackout: 'blackout',
  puke: 'puke',
}

export interface Sprite {
  sprite: Uint8Array
  palette: Uint8Array | null
  gloves: Uint8Array | null
  author: string
}

export function adjustRom(rom: Uint8Array, options: Partial<DoorOptions> = {}, sprite: Sprite | null = null, random: () => number = Math.random): Uint8Array {
  const o = { ...DEFAULT_OPTIONS, ...options }

  const instant = o.menuSpeed === 'instant'
  rom[0x6dd9a] = instant ? 0x20 : 0x11
  rom[0x6df2a] = instant ? 0x20 : 0x12
  rom[0x6e0e9] = instant ? 0x20 : 0x12
  rom[0x180048] = MENU_SPEED[o.menuSpeed] ?? MENU_SPEED.normal!

  rom[0x18004b] = o.quickswap ? 0x01 : 0x00

  const off = !o.music
  rom[0x0cfe18] = off ? 0x00 : 0x70
  rom[0x0cfec1] = off ? 0x00 : 0xc0
  rom.set(off ? [0x00, 0x00] : [0xda, 0x58], 0x0d0000)
  rom.set(off ? [0xc4, 0x58] : [0xda, 0x58], 0x0d00e7)
  rom[0x18021a] = off ? 0x01 : 0x00

  rom[0x180033] = HEART_BEEP[o.heartBeep] ?? HEART_BEEP.normal!
  let color: string = o.heartColor
  if (color === 'random') color = ['red', 'blue', 'green', 'yellow'][Math.floor(random() * 4)]!
  rom[0x187020] = HEART_COLOR[color] ?? HEART_COLOR.red!

  if (o.reduceFlashing) rom[0x18017f] = 1

  if (sprite) {
    rom.set(sprite.sprite, 0x80000)
    if (sprite.palette) rom.set(sprite.palette, 0xdd308)
    if (sprite.gloves) rom.set(sprite.gloves, 0xdedf5)
  }
  writeCredits(rom, sprite ? sprite.author : 'Nintendo')

  const seed = () => Math.floor(random() * 0x100000000)
  const ow = PALETTE_MODE[o.owPalettes]
  if (ow) randomizePalettes(rom, { randomize_overworld: true, mode: ow, seed: seed() })
  const uw = PALETTE_MODE[o.uwPalettes]
  if (uw) randomizePalettes(rom, { randomize_dungeon: true, mode: uw, seed: seed() })

  writeChecksum(rom)
  return rom
}

/** The SNES header checksum, as the randomizer writes it. */
export function writeChecksum(rom: Uint8Array): void {
  let sum = 0
  for (let i = 0; i < rom.length; i++) {
    if (i < 0x7fdc || i >= 0x7fe0) sum += rom[i]!
  }
  const crc = (sum + 0x01fe) & 0xffff
  const inv = crc ^ 0xffff
  rom.set([inv & 0xff, inv >> 8, crc & 0xff, crc >> 8], 0x7fdc)
}

const CREDIT_LETTERS = ' 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
const CREDIT_PUNCTUATION: Record<string, [number, number]> = {
  "'": [0xd9, 0xec],
  '.': [0xdc, 0xef],
  '/': [0xdb, 0xee],
  ':': [0xdd, 0xf0],
  _: [0xde, 0xf1],
}

function creditBytes(ch: string): [number, number] {
  if (ch === ' ') return [0x9f, 0x9f]
  const p = CREDIT_PUNCTUATION[ch]
  if (p) return p
  const i = CREDIT_LETTERS.indexOf(ch)
  return i > 0 ? [0x52 + i, 0x78 + i] : [0x9f, 0x9f]
}

function writeCredits(rom: Uint8Array, author: string): void {
  const name = String(author || '').slice(0, 28).toUpperCase()
  const left = Math.floor((28 - name.length) / 2)
  const padded = ' '.repeat(left) + name + ' '.repeat(28 - name.length - left)
  for (let i = 0; i < 28; i++) {
    const [top, bottom] = creditBytes(padded[i]!)
    rom[0x118002 + i] = top
    rom[0x118020 + i] = bottom
  }
}

/**
 * A sprite from a .zspr file, or a raw 0x7000/0x7078/0x707C dump. A missing
 * palette or glove colours leave Link's own in place.
 */
export function parseSprite(bytes: Uint8Array): Sprite {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const isZspr = bytes.length > 0x1d && String.fromCharCode(...bytes.subarray(0, 4)) === 'ZSPR'
  let sprite: Uint8Array
  let palette: Uint8Array | null = null
  let gloves: Uint8Array | null = null
  let author = ''
  if (isZspr) {
    const spriteAt = view.getUint32(9, true)
    const spriteSize = view.getUint16(13, true)
    const paletteAt = view.getUint32(15, true)
    const paletteSize = view.getUint16(19, true)
    if (spriteSize !== 0x7000 || spriteAt + spriteSize > bytes.length || paletteAt + paletteSize > bytes.length) {
      throw new RomError('sprite', 'That sprite file is damaged.')
    }
    sprite = bytes.slice(spriteAt, spriteAt + spriteSize)
    if (paletteSize >= 0x78) palette = bytes.slice(paletteAt, paletteAt + 0x78)
    if (paletteSize >= 0x7c) gloves = bytes.slice(paletteAt + 0x78, paletteAt + 0x7c)
    author = zsprAuthor(bytes)
  } else if ([0x7000, 0x7078, 0x707c].includes(bytes.length)) {
    sprite = bytes.slice(0, 0x7000)
    if (bytes.length >= 0x7078) palette = bytes.slice(0x7000, 0x7078)
    if (bytes.length === 0x707c) gloves = bytes.slice(0x7078, 0x707c)
  } else {
    throw new RomError('sprite', 'That is not a sprite file.')
  }
  return { sprite, palette, gloves, author }
}

// After the 29-byte header: display name and author in UTF-16LE, each
// null-terminated, then the author again in ASCII for the credits.
function zsprAuthor(bytes: Uint8Array): string {
  let at = 0x1d
  const skipUtf16 = () => {
    while (at + 1 < bytes.length && (bytes[at] || bytes[at + 1])) at += 2
    at += 2
  }
  skipUtf16()
  skipUtf16()
  let end = at
  while (end < bytes.length && bytes[end]) end++
  return String.fromCharCode(...bytes.subarray(at, Math.min(end, at + 64)))
}
