import { useRef, useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, FormDialog, Spinner } from '../../components'
import { done, message } from '../../me/data'
import { useAdminQuery } from '../kit'
import { asBoxes, uploadArt, type LayoutLine } from './data'
import { OverlayView } from './OverlayView'

function useSubmit() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (call: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await call()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, setError, submit }
}

export function useEditorOptions() {
  return useAdminQuery('stream-layouts/editor-options', () => api.GET('/api/admin/stream-layouts/editor-options').then(unwrap))
}

function Swatches({ value, onPick, swatches }: { value: string; onPick: (c: string) => void; swatches: { label: string; colour: string }[] }) {
  return (
    <div className="sl-swatches" role="radiogroup" aria-label="Background colour">
      {swatches.map((s) => (
        <button
          key={s.colour}
          type="button"
          role="radio"
          aria-checked={value.toUpperCase() === s.colour.toUpperCase()}
          aria-label={s.label}
          title={s.label}
          className="sl-swatch"
          style={{ background: s.colour }}
          onClick={() => onPick(s.colour)}
        />
      ))}
    </div>
  )
}

/** Pick a starter (drawn small on the chosen colour), name it, create it. */
export function NewLayoutDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const options = useEditorOptions()
  const [starter, setStarter] = useState<string | null>(null)
  const [name, setName] = useState<string | null>(null)
  const [colour, setColour] = useState<string | null>(null)
  const { busy, error, submit } = useSubmit()
  const o = options.data
  const key = starter ?? o?.default_starter ?? 'blank'
  const picked = o?.starters.find((s) => s.key === key)
  const bg = colour ?? o?.backgrounds[0]?.colour ?? '#1E2A44'

  return (
    <FormDialog
      open
      size="lg"
      onOpenChange={(v) => !v && onClose()}
      title="New layout"
      description="Start from a layout and recolour it, or upload your own art from the layout's menu later."
      primaryLabel="Create"
      busy={busy}
      busyLabel="Creating…"
      onPrimary={() =>
        void submit(async () => {
          const r = await api
            .POST('/api/admin/stream-layouts/layouts', { body: { name: (name ?? picked?.label ?? '').trim(), starter: key, background: bg } })
            .then(unwrap)
          onCreated(r.layout.id)
        })
      }
    >
      <div className="t-form">
        {(error || options.error) && <Banner tone="error">{error ?? options.error?.message}</Banner>}
        {!o && !options.error && <Spinner label="Loading starters" />}
        {o && (
          <>
            <label className="field">
              <span className="field__label">Name</span>
              <input className="input" value={name ?? picked?.label ?? ''} maxLength={80} onChange={(e) => setName(e.target.value)} />
            </label>
            <div className="sl-gallery" role="radiogroup" aria-label="Start from">
              {o.starters.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  role="radio"
                  aria-checked={s.key === key}
                  className="sl-gallery__tile"
                  onClick={() => {
                    setStarter(s.key)
                    setName(null)
                  }}
                >
                  <span className="sl-gallery__thumb" style={{ background: bg }}>
                    <OverlayView width={o.starter_width} height={o.starter_height} boxes={asBoxes(s.boxes)} values={{}} outlines label="" />
                  </span>
                  <span className="sl-gallery__name">{s.label}</span>
                  <span className="sl-gallery__blurb">{s.blurb}</span>
                </button>
              ))}
            </div>
            <div className="field">
              <span className="field__label">Background</span>
              <Swatches value={bg} onPick={setColour} swatches={o.backgrounds} />
            </div>
          </>
        )}
      </div>
    </FormDialog>
  )
}

export function RenameDialog({ layout, onClose, onDone }: { layout: LayoutLine; onClose: () => void; onDone: (text: string) => void }) {
  const [name, setName] = useState(layout.name)
  const { busy, error, submit } = useSubmit()
  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(v) => !v && onClose()}
      title={`Rename ${layout.name}`}
      primaryLabel="Save"
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() =>
        void submit(async () => {
          await api.PATCH('/api/admin/stream-layouts/layouts/{layout_id}', { params: { path: { layout_id: layout.id } }, body: { name: name.trim() } }).then(unwrap)
          onDone(`Renamed to ${name.trim()}.`)
        })
      }
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}

export function BackgroundDialog({ layout, onClose, onDone }: { layout: LayoutLine; onClose: () => void; onDone: (text: string) => void }) {
  const options = useEditorOptions()
  const [colour, setColour] = useState('#1E2A44')
  const { busy, error, submit } = useSubmit()
  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(v) => !v && onClose()}
      title="Plain background"
      description="Swaps the art for one colour. Boxes, video holes and the art's see-through areas stay where they are."
      primaryLabel="Apply"
      busy={busy}
      busyLabel="Applying…"
      onPrimary={() =>
        void submit(async () => {
          await api
            .PUT('/api/admin/stream-layouts/layouts/{layout_id}/background', { params: { path: { layout_id: layout.id } }, body: { colour } })
            .then(unwrap)
          onDone(`${layout.name} has a plain background now.`)
        })
      }
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Colour</span>
          <span className="sl-colour">
            <input type="color" className="sl-colour__pick" aria-label="Pick a colour" value={colour} onChange={(e) => setColour(e.target.value.toUpperCase())} />
            <input className="input" value={colour} maxLength={7} onChange={(e) => setColour(e.target.value)} />
          </span>
        </label>
        {options.data && <Swatches value={colour} onPick={setColour} swatches={options.data.backgrounds} />}
      </div>
    </FormDialog>
  )
}

export function UploadArtDialog({ layout, onClose, onDone }: { layout: LayoutLine; onClose: () => void; onDone: (text: string) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const { busy, error, setError, submit } = useSubmit()
  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(v) => !v && onClose()}
      title="Upload art"
      description="A PNG, JPEG or WebP up to 10 MB, ideally 1920×1080. The canvas takes the art's size and the boxes scale to match."
      primaryLabel="Upload"
      busy={busy}
      busyLabel="Uploading…"
      onPrimary={() =>
        void submit(async () => {
          if (!file) {
            setError('Pick an image first.')
            return
          }
          const r = await uploadArt(layout.id, file)
          onDone(
            r.transparent
              ? `Art uploaded at ${r.layout.width}×${r.layout.height}. Its see-through areas let the video show.`
              : `Art uploaded at ${r.layout.width}×${r.layout.height}. It has no see-through areas, so add video holes where the feeds go.`,
          )
        })
      }
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Image</span>
          <input ref={input} className="input" type="file" accept=".png,.jpg,.jpeg,.webp" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
      </div>
    </FormDialog>
  )
}

export function DeleteLayoutDialog({ layout, onClose, onDone }: { layout: LayoutLine; onClose: () => void; onDone: () => void }) {
  const { busy, error, submit } = useSubmit()
  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(v) => !v && onClose()}
      title={`Delete ${layout.name}?`}
      primaryLabel="Delete layout"
      destructive
      busy={busy}
      busyLabel="Deleting…"
      onPrimary={() =>
        void submit(async () => {
          done(await api.DELETE('/api/admin/stream-layouts/layouts/{layout_id}', { params: { path: { layout_id: layout.id } } }))
          onDone()
        })
      }
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <p className="sl-note">
          {layout.stages.length
            ? `${layout.stages.join(', ')} ${layout.stages.length === 1 ? 'shows' : 'show'} a blank overlay until you pick another layout.`
            : 'No stage uses it.'}{' '}
          This can't be undone.
        </p>
      </div>
    </FormDialog>
  )
}
