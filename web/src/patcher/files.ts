/**
 * Where the patchers keep a player's ROMs, and how files come in and go out.
 *
 * ROMs never leave the browser. They live in IndexedDB on this device: the
 * door patcher keeps the one Japanese 1.0 ROM, the generic patcher keeps each
 * ROM under its CRC32 so the next patch for it finds it without asking. The
 * database names match the website's, so a ROM picked there is still here.
 */
import { authedFetch } from '../api/client'
import { API_BASE } from '../auth/config'
import { readStored, writeStored } from '../lib/storage'

function openDb(name: string, store: string): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(name, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(store)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function dbGet<T>(name: string, store: string, key: string): Promise<T | null> {
  try {
    const db = await openDb(name, store)
    return await new Promise<T | null>((resolve, reject) => {
      const req = db.transaction(store).objectStore(store).get(key)
      req.onsuccess = () => resolve((req.result as T | undefined) ?? null)
      req.onerror = () => reject(req.error)
    })
  } catch {
    return null
  }
}

async function dbPut(name: string, store: string, key: string, value: unknown): Promise<void> {
  try {
    const db = await openDb(name, store)
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(store, 'readwrite')
      tx.objectStore(store).put(value, key)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // Private windows can refuse storage; the ROM still works for this visit.
  }
}

export const doorRom = {
  get: () => dbGet<Uint8Array>('wiz-door-patcher', 'files', 'jp10'),
  put: (bytes: Uint8Array) => dbPut('wiz-door-patcher', 'files', 'jp10', bytes),
}

export interface SavedRom {
  name: string
  crc: string
  bytes: Uint8Array
}

const GAMES_KEY = 'wiz-rom-patcher-games'

function games(): Record<string, string> {
  try {
    const parsed: unknown = JSON.parse(readStored(GAMES_KEY) || '{}')
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, string>) : {}
  } catch {
    return {}
  }
}

export const savedRoms = {
  get: (crc: string | null | undefined) => (crc ? dbGet<SavedRom>('wiz-rom-patcher', 'roms', crc) : Promise.resolve(null)),
  put: (rom: SavedRom) => dbPut('wiz-rom-patcher', 'roms', rom.crc, rom),
  /** An IPS names no ROM, so a hosted one remembers the ROM by the game staff named. */
  forGame: (game: string) => games()[game] ?? null,
  rememberGame: (game: string, crc: string) => writeStored(GAMES_KEY, JSON.stringify({ ...games(), [game]: crc })),
}

/** A file the API serves, as bytes; the error says what couldn't load. */
export async function fetchBytes(path: string, what: string): Promise<Uint8Array> {
  const response = await authedFetch(new Request(`${API_BASE}${path}`))
  if (!response.ok) throw new Error(`Couldn't load the ${what} (${response.status}). Try again.`)
  return new Uint8Array(await response.arrayBuffer())
}

/** Bytes from anywhere else (a sprite on alttpr.com's storage). */
export async function fetchOutside(url: string, what: string): Promise<Uint8Array> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Couldn't load the ${what} (${response.status}). Try again.`)
  return new Uint8Array(await response.arrayBuffer())
}

export function saveBytes(bytes: Uint8Array, filename: string): void {
  const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type: 'application/octet-stream' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10000)
}

export async function readFile(file: File): Promise<Uint8Array> {
  return new Uint8Array(await file.arrayBuffer())
}

export function extension(name: string): string {
  return /\.[A-Za-z0-9]{1,5}$/.exec(name)?.[0] ?? '.bin'
}

export function baseName(name: string): string {
  return name.replace(/\.[A-Za-z0-9]{1,5}$/, '') || 'patched'
}

export const ROM_ACCEPT =
  '.sfc,.smc,.nes,.fds,.gb,.gbc,.gba,.n64,.z64,.v64,.gen,.md,.bin,.sms,.gg,.pce,.iso,application/octet-stream'
