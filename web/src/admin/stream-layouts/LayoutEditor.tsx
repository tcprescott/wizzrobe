import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Icon, Menu, Panel, PanelRow, Spinner, type MenuItem } from '../../components'
import { effectiveZone, formatDayTime } from '../../lib/time'
import { message } from '../../me/data'
import { useAdminQuery, useDraft, useUnsavedFlag } from '../kit'
import { asBoxes, boxLabel, clamp, matchLabel, newBox, type Box, type EditorOptions } from './data'
import { useEditorOptions } from './LayoutDialogs'
import { OverlayView } from './OverlayView'
import { useBoard } from './StreamLayoutsScreen'

const SNAP = 4
const MIN = 4
const snap = (v: number) => Math.round(v / SNAP) * SNAP

interface Drag {
  id: string
  pointer: number
  resize: boolean
  sx: number
  sy: number
  k: number
  start: Box
  moved: boolean
}

/** The full-width box editor: the canvas, and the selected box's settings beside it. Desktop only. */
export function LayoutEditor({ layoutId, back, base }: { layoutId: number; back: string; base: string }) {
  const board = useBoard()
  const options = useEditorOptions()
  const detail = useAdminQuery(`stream-layouts/layouts/${layoutId}/edit`, () =>
    api.GET('/api/admin/stream-layouts/layouts/{layout_id}', { params: { path: { layout_id: layoutId } } }).then(unwrap),
  )
  const matches = useAdminQuery('stream-layouts/preview-matches', () => api.GET('/api/admin/stream-layouts/preview-matches').then(unwrap))
  const saved = detail.data ? asBoxes(detail.data.boxes) : undefined
  const draft = useDraft<Box[]>(saved)
  useUnsavedFlag(draft.dirty)
  const [selected, setSelected] = useState<string | null>(null)
  const [previewMatch, setPreviewMatch] = useState<number | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const canvas = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const boxes = draft.value ?? []
  const boxesKey = JSON.stringify(boxes)

  useEffect(() => {
    if (!detail.data) return
    const t = setTimeout(() => {
      api
        .POST('/api/admin/stream-layouts/layouts/{layout_id}/preview', {
          params: { path: { layout_id: layoutId } },
          body: { match_id: previewMatch, boxes: JSON.parse(boxesKey) },
        })
        .then(unwrap)
        .then((r) => setValues(r.values))
        .catch(() => undefined)
    }, 300)
    return () => clearTimeout(t)
  }, [boxesKey, previewMatch, layoutId, detail.data])

  if ((detail.error && !detail.data) || (options.error && !options.data)) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={detail.reload}>Try again</Button>}>
        {(detail.error ?? options.error)?.message}
      </Banner>
    )
  }
  if (!detail.data || !options.data) return <Spinner label="Loading the editor" />
  const layout = detail.data.layout
  const o = options.data
  const W = layout.width
  const H = layout.height
  const zone = effectiveZone(board.data?.zone ?? null)
  const sel = boxes.find((b) => b.id === selected) ?? null

  const update = (id: string, part: Partial<Box>) => draft.set(boxes.map((b) => (b.id === id ? { ...b, ...part } : b)))

  function place(start: Box, dx: number, dy: number, resize: boolean): Partial<Box> {
    if (resize) return { w: clamp(snap(start.w + dx), MIN, W - start.x), h: clamp(snap(start.h + dy), MIN, H - start.y) }
    return { x: clamp(snap(start.x + dx), 0, W - start.w), y: clamp(snap(start.y + dy), 0, H - start.h) }
  }

  function onPointerDown(e: PointerEvent<HTMLDivElement>, box: Box) {
    const rect = canvas.current?.getBoundingClientRect()
    if (!rect) return
    drag.current = {
      id: box.id,
      pointer: e.pointerId,
      resize: !!(e.target as HTMLElement).closest('.sl-handle'),
      sx: e.clientX,
      sy: e.clientY,
      k: W / rect.width,
      start: box,
      moved: false,
    }
    e.currentTarget.setPointerCapture(e.pointerId)
    e.preventDefault()
  }

  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    const d = drag.current
    if (!d || e.pointerId !== d.pointer) return
    const dx = (e.clientX - d.sx) * d.k
    const dy = (e.clientY - d.sy) * d.k
    if (!d.moved && Math.abs(dx) + Math.abs(dy) < 3 * d.k) return
    d.moved = true
    update(d.id, place(d.start, dx, dy, d.resize))
  }

  function onPointerUp(e: PointerEvent<HTMLDivElement>) {
    const d = drag.current
    if (!d || e.pointerId !== d.pointer) return
    drag.current = null
    if (!d.moved) setSelected(d.id)
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>, box: Box) {
    if (e.key === 'Enter' || e.key === ' ') {
      setSelected(box.id)
      e.preventDefault()
      return
    }
    const step = e.shiftKey ? 10 : 1
    const delta: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }
    const dir = delta[e.key]
    if (!dir) return
    e.preventDefault()
    update(box.id, { x: clamp(box.x + dir[0], 0, W - box.w), y: clamp(box.y + dir[1], 0, H - box.h) })
  }

  function add(kind: 'field' | 'hole', field: string | null) {
    if (boxes.length >= o.max_boxes) {
      setNote({ tone: 'error', text: `A layout can have up to ${o.max_boxes} boxes.` })
      return
    }
    const box = newBox(kind, field, W, H)
    draft.set([...boxes, box])
    setSelected(box.id)
  }

  async function save() {
    setBusy(true)
    setNote(null)
    try {
      const r = await api
        .PUT('/api/admin/stream-layouts/layouts/{layout_id}/boxes', {
          params: { path: { layout_id: layoutId } },
          body: { boxes: boxes as unknown as Record<string, unknown>[], expected_updated_at: layout.updated_at },
        })
        .then(unwrap)
      draft.set(asBoxes(r.boxes))
      detail.reload()
      board.reload()
      setNote({ tone: 'info', text: 'Saved. Stages using this layout update within two seconds.' })
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  const groups = Array.from(new Set(o.fields.map((f) => f.group)))
  const addItems: MenuItem[] = [
    ...groups.flatMap((g) => o.fields.filter((f) => f.group === g).map((f) => ({ label: `${g}: ${f.label}`, icon: 'text_fields' as const, onSelect: () => add('field', f.key) }))),
    { label: 'Video hole', icon: 'tv', onSelect: () => add('hole', null) },
  ]

  const renderBox = (box: Box, style: CSSProperties, content: ReactNode) => (
    <div
      key={box.id}
      className={`ovl__box ovl__box--${box.kind} sl-box${box.id === selected ? ' sl-box--on' : ''}`}
      style={style}
      tabIndex={0}
      role="button"
      aria-pressed={box.id === selected}
      aria-label={`${boxLabel(box, o)}. Arrow keys move it.`}
      onPointerDown={(e) => onPointerDown(e, box)}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={(e) => onKeyDown(e, box)}
    >
      {content}
      {box.kind === 'hole' && box.label && <span className="sl-box__tag">{box.label}</span>}
      <span className="sl-handle" aria-hidden="true" />
    </div>
  )

  return (
    <div className="sl-editor">
      <Link to={back} className="admin-back">
        <Icon name="chevron_left" />
        {layout.name}
      </Link>
      <div className="detail-head">
        <div className="detail-head__text">
          <h1>Edit {layout.name}</h1>
          <div className="detail-head__sub">
            <span>
              {W}×{H} · {boxes.length} {boxes.length === 1 ? 'box' : 'boxes'}
              {draft.dirty ? ' · unsaved changes' : ''}
            </span>
          </div>
        </div>
        <div className="t-actions sl-editor__tools">
          <Menu
            trigger={
              <Button variant="outline" icon="add">
                Add box
              </Button>
            }
            items={addItems}
          />
          <label className="sl-editor__preview">
            <span className="visually-hidden">Preview with a match</span>
            <select className="input" value={previewMatch ?? ''} onChange={(e) => setPreviewMatch(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Preview: sample text</option>
              {(matches.data ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  Preview: {matchLabel(m, m.scheduled_at ? formatDayTime(m.scheduled_at, zone) : undefined)}
                </option>
              ))}
            </select>
          </label>
          <Button variant="outline" icon="undo" disabled={!draft.dirty || busy} onClick={() => { draft.discard(); setSelected(null); setNote(null) }}>
            Undo
          </Button>
          <Button disabled={!draft.dirty} busy={busy} busyLabel="Saving…" onClick={() => void save()}>
            Save
          </Button>
        </div>
      </div>

      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}

      <div className="sl-editor__body">
        <Panel>
          <PanelRow>
            <div ref={canvas} className="sl-canvas">
              <OverlayView width={W} height={H} artUrl={layout.art_url} boxes={boxes} values={values} outlines renderBox={renderBox} label={`${layout.name} canvas`} />
            </div>
          </PanelRow>
          <PanelRow>
            <span className="t-row__sub">Drag a box to move it, its corner to resize. Tab to a box and use the arrow keys to nudge it (Shift for 10).</span>
          </PanelRow>
        </Panel>
        <aside className="sl-editor__side" aria-label="Box settings">
          {sel ? (
            <BoxSettings
              key={sel.id}
              box={sel}
              options={o}
              width={W}
              height={H}
              onChange={(part) => update(sel.id, part)}
              onRemove={() => {
                draft.set(boxes.filter((b) => b.id !== sel.id))
                setSelected(null)
              }}
              onDone={() => setSelected(null)}
            />
          ) : (
            <Panel title="Boxes">
              {boxes.length === 0 && (
                <PanelRow>
                  <span className="t-row__sub">No boxes yet. Add box puts one in the middle.</span>
                </PanelRow>
              )}
              {boxes.map((b) => (
                <PanelRow key={b.id}>
                  <button type="button" className="sl-boxlist__item" onClick={() => setSelected(b.id)}>
                    <Icon name={b.kind === 'hole' ? 'tv' : 'text_fields'} size={20} />
                    <span>{boxLabel(b, o)}</span>
                    <Icon name="chevron_right" size={18} />
                  </button>
                </PanelRow>
              ))}
            </Panel>
          )}
        </aside>
      </div>
      <p className="t-row__sub">
        <Link to={base}>Back to Stream Layouts</Link>
      </p>
    </div>
  )
}

function Num({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      <input
        className="input"
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => {
          const n = Number(e.target.value)
          if (Number.isFinite(n)) onChange(clamp(Math.round(n), min, max))
        }}
      />
    </label>
  )
}

function BoxSettings({
  box,
  options,
  width,
  height,
  onChange,
  onRemove,
  onDone,
}: {
  box: Box
  options: EditorOptions
  width: number
  height: number
  onChange: (part: Partial<Box>) => void
  onRemove: () => void
  onDone: () => void
}) {
  const field = options.fields.find((f) => f.key === box.field)
  return (
    <Panel
      title={boxLabel(box, options)}
      actions={
        <Button size="sm" variant="ghost" onClick={onDone}>
          All boxes
        </Button>
      }
    >
      <PanelRow>
        <div className="t-form sl-settings">
          {box.kind === 'field' ? (
            <>
              <label className="field">
                <span className="field__label">Shows</span>
                <select className="input" value={box.field} onChange={(e) => onChange({ field: e.target.value })}>
                  {options.fields.map((f) => (
                    <option key={f.key} value={f.key}>
                      {f.group}: {f.label}
                    </option>
                  ))}
                </select>
              </label>
              {field?.per_player && (
                <label className="field">
                  <span className="field__label">Player</span>
                  <select className="input" value={box.which ?? 1} onChange={(e) => onChange({ which: Number(e.target.value) })}>
                    {Array.from({ length: options.max_players }, (_, i) => (
                      <option key={i + 1} value={i + 1}>
                        Player {i + 1}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {box.field === 'custom_text' && (
                <label className="field">
                  <span className="field__label">Text</span>
                  <input className="input" value={box.text ?? ''} maxLength={options.max_text} onChange={(e) => onChange({ text: e.target.value })} />
                </label>
              )}
              <div className="sl-settings__pair">
                <label className="field">
                  <span className="field__label">Font</span>
                  <select className="input" value={box.font ?? 'Inter'} onChange={(e) => onChange({ font: e.target.value })}>
                    {options.fonts.map((f) => (
                      <option key={f}>{f}</option>
                    ))}
                  </select>
                </label>
                <Num label="Size" value={box.size ?? 24} min={6} max={400} onChange={(size) => onChange({ size })} />
              </div>
              <div className="sl-settings__pair">
                <label className="field">
                  <span className="field__label">Weight</span>
                  <select className="input" value={box.weight ?? 800} onChange={(e) => onChange({ weight: Number(e.target.value) })}>
                    {options.weights.map((w) => (
                      <option key={w} value={w}>
                        {w === 400 ? 'Regular' : w === 600 ? 'Semibold' : 'Bold'}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span className="field__label">Align</span>
                  <select className="input" value={box.align ?? 'center'} onChange={(e) => onChange({ align: e.target.value as Box['align'] })}>
                    {options.aligns.map((a) => (
                      <option key={a} value={a}>
                        {a[0]!.toUpperCase() + a.slice(1)}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <label className="field">
                <span className="field__label">Colour</span>
                <span className="sl-colour">
                  <input
                    type="color"
                    className="sl-colour__pick"
                    aria-label="Pick a colour"
                    value={box.color ?? '#FFFFFF'}
                    onChange={(e) => onChange({ color: e.target.value.toUpperCase() })}
                  />
                  <input
                    className="input"
                    value={box.color ?? '#FFFFFF'}
                    maxLength={7}
                    onChange={(e) => /^#[0-9a-fA-F]{6}$/.test(e.target.value) && onChange({ color: e.target.value.toUpperCase() })}
                  />
                </span>
              </label>
              <label className="t-check">
                <input type="checkbox" checked={!!box.upper} onChange={(e) => onChange({ upper: e.target.checked })} />
                <span>Capitals</span>
              </label>
              <label className="t-check">
                <input type="checkbox" checked={box.shadow ?? true} onChange={(e) => onChange({ shadow: e.target.checked })} />
                <span>Shadow behind the text</span>
              </label>
            </>
          ) : (
            <>
              <label className="field">
                <span className="field__label">Name (shows in OBS)</span>
                <input className="input" value={box.label ?? ''} maxLength={options.max_label} placeholder="e.g. Player 1 game" onChange={(e) => onChange({ label: e.target.value })} />
              </label>
              <Num label="Corner radius" value={box.radius ?? 0} min={0} max={Math.floor(Math.min(box.w, box.h) / 2)} onChange={(radius) => onChange({ radius })} />
            </>
          )}
          <div className="sl-settings__pair">
            <Num label="X" value={box.x} min={0} max={width - box.w} onChange={(x) => onChange({ x })} />
            <Num label="Y" value={box.y} min={0} max={height - box.h} onChange={(y) => onChange({ y })} />
          </div>
          <div className="sl-settings__pair">
            <Num label="Width" value={box.w} min={MIN} max={width - box.x} onChange={(w) => onChange({ w })} />
            <Num label="Height" value={box.h} min={MIN} max={height - box.y} onChange={(h) => onChange({ h })} />
          </div>
          <div className="t-actions">
            <Button variant="danger" icon="delete" onClick={onRemove}>
              Remove box
            </Button>
          </div>
        </div>
      </PanelRow>
    </Panel>
  )
}
