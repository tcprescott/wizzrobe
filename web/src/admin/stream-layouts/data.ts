import { API_BASE } from '../../auth/config'
import { authedFetch, errorMessage, ApiError, type Schemas } from '../../api/client'

export type Board = Schemas['StreamLayoutBoardResponse']
export type StageLine = Schemas['StageLineResponse']
export type LayoutLine = Schemas['LayoutLineResponse']
export type OverlayMatch = Schemas['OverlayMatchResponse']
export type EditorOptions = Schemas['EditorOptionsResponse']

export type Align = 'left' | 'center' | 'right'

/** One box on a layout, in canvas pixels. The server checks and clamps every save. */
export interface Box {
  id: string
  kind: 'field' | 'hole'
  x: number
  y: number
  w: number
  h: number
  radius?: number
  label?: string
  field?: string
  which?: number
  font?: string
  size?: number
  weight?: number
  color?: string
  align?: Align
  upper?: boolean
  shadow?: boolean
  text?: string
}

export const asBoxes = (raw: Record<string, unknown>[]): Box[] => raw as unknown as Box[]

const FONT_STACKS: Record<string, string> = {
  Inter: "Inter, 'Segoe UI', system-ui, sans-serif",
  Arial: 'Arial, Helvetica, sans-serif',
  Impact: "Impact, 'Arial Black', sans-serif",
  Georgia: "Georgia, 'Times New Roman', serif",
  Courier: "'Courier New', Courier, monospace",
}
export const fontStack = (name?: string) => FONT_STACKS[name ?? 'Inter'] ?? FONT_STACKS.Inter

/** What a box list calls a box: "Player 1 · name", "Video hole · Left". */
export function boxLabel(box: Box, options?: EditorOptions): string {
  if (box.kind === 'hole') return box.label ? `Video hole · ${box.label}` : 'Video hole'
  const field = options?.fields.find((f) => f.key === box.field)
  if (!field) return 'Text box'
  if (field.per_player) return `Player ${box.which ?? 1} · ${field.label.replace('Player ', '').toLowerCase()}`
  return field.label
}

function newId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => b.toString(16).padStart(2, '0')).join('')
}

/** A new box centred on the canvas, sized for what it holds (the server's `default_box`). */
export function newBox(kind: 'field' | 'hole', field: string | null, width: number, height: number): Box {
  if (kind === 'hole') {
    const w = Math.floor(width / 3)
    const h = Math.floor(height / 3)
    return { id: newId(), kind, x: Math.floor((width - w) / 2), y: Math.floor((height - h) / 2), w, h, radius: 0, label: '' }
  }
  const w = Math.max(4, Math.floor(width / 4))
  const h = Math.max(4, Math.floor(height / 20))
  return {
    id: newId(),
    kind,
    x: Math.floor((width - w) / 2),
    y: Math.floor((height - h) / 2),
    w,
    h,
    field: field ?? 'custom_text',
    which: 1,
    font: 'Inter',
    size: Math.max(12, Math.floor((h * 2) / 3)),
    weight: 800,
    color: '#FFFFFF',
    align: 'center',
    upper: false,
    shadow: true,
    text: '',
  }
}

export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** "Dana vs Kai · ALTTPR". */
export function matchLabel(m: OverlayMatch, when?: string): string {
  return [when, m.who, m.tournament].filter(Boolean).join(' · ')
}

/** Upload art as the raw request body (the route takes the image itself, not a form). */
export async function uploadArt(layoutId: number, file: File): Promise<Schemas['SetArtResponse']> {
  const response = await authedFetch(
    new Request(`${API_BASE}/api/admin/stream-layouts/layouts/${layoutId}/art`, {
      method: 'PUT',
      body: file,
      headers: { 'Content-Type': file.type || 'application/octet-stream' },
    }),
  )
  const body = await response.json().catch(() => undefined)
  if (!response.ok) throw new ApiError(errorMessage(body, response.status), response.status)
  return body as Schemas['SetArtResponse']
}

/** Save text as a file the person downloads. */
export function saveFile(filename: string, content: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
