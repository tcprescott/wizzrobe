import { useEffect, useRef, useState } from 'react'
import { Link, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, authedFetch, errorMessage, unwrap, type Schemas } from '../../api/client'
import { API_BASE } from '../../auth/config'
import { useCommunity } from '../../community/CommunityProvider'
import { Banner, Button, Chip, EmptyState, FormDialog, PageHeader, RowMenu, Spinner, type MenuItem } from '../../components'
import { effectiveZone, formatDay } from '../../lib/time'
import { patchInfo, PatchError } from '../../patcher/formats'
import { Detail, DetailEmpty, DetailLine, DetailSection, SideListCard, SideListLayout, SideListRow } from '../kit/SideList'
import { useAdmin } from '../kit/useAdminNav'
import { message, useAction, useAdminData } from '../peopleKit'

type Patch = Schemas['AdminPatchResponse']
type Board = Schemas['AdminPatchesResponse']

const MAX_BYTES = 16 * 1024 * 1024
const FORMAT: Record<string, string> = { bps: 'BPS', ips: 'IPS', ups: 'UPS' }

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function downloads(n: number): string {
  return n === 1 ? '1 download' : `${n} downloads`
}

/** Sends a patch file as the raw body, as the API wants; the rest rides in the query. */
async function sendFile(method: 'POST' | 'PUT', path: string, file: File, query: Record<string, string | number | null | undefined>): Promise<Patch> {
  if (file.size > MAX_BYTES) throw new Error('Patches can be up to 16 MB.')
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) if (v !== null && v !== undefined && v !== '') params.set(k, String(v))
  const response = await authedFetch(
    new Request(`${API_BASE}${path}?${params}`, { method, body: file, headers: { 'Content-Type': 'application/octet-stream' } }),
  )
  const body: unknown = await response.json().catch(() => undefined)
  if (!response.ok) throw new Error(errorMessage(body, response.status))
  return body as Patch
}

/** Reads the patch's own format and CRC before upload, so an IPS can ask for one and a bad file fails here. */
async function inspect(file: File): Promise<{ format: string; crc: string | null }> {
  try {
    const info = patchInfo(new Uint8Array(await file.arrayBuffer()))
    return { format: info.format, crc: info.sourceCrc == null ? null : info.sourceCrc.toString(16).toUpperCase().padStart(8, '0') }
  } catch (e) {
    throw new Error(e instanceof PatchError ? e.message : "That file isn't a BPS, IPS or UPS patch.")
  }
}

interface Form {
  title: string
  game: string
  description: string
  tournament: string
  crc: string
}

function PatchFields({ form, set, tournaments, ips }: { form: Form; set: (f: Partial<Form>) => void; tournaments: Board['tournaments']; ips: boolean }) {
  return (
    <>
      <label className="field">
        <span className="field__label">Title</span>
        <input className="input" value={form.title} maxLength={120} onChange={(e) => set({ title: e.target.value })} />
      </label>
      <label className="field">
        <span className="field__label">ROM it needs</span>
        <input className="input" value={form.game} maxLength={120} placeholder="Super Metroid (JU)" onChange={(e) => set({ game: e.target.value })} />
        <span className="field__hint">Players see this before they pick a ROM.</span>
      </label>
      {ips && (
        <label className="field">
          <span className="field__label">ROM CRC32 (optional)</span>
          <input className="input" value={form.crc} maxLength={8} placeholder="D63ED5F8" onChange={(e) => set({ crc: e.target.value })} />
          <span className="field__hint">An IPS doesn't name its ROM. Add the CRC32 and the wrong ROM is caught before patching.</span>
        </label>
      )}
      <label className="field">
        <span className="field__label">Tournament</span>
        <select className="input" value={form.tournament} onChange={(e) => set({ tournament: e.target.value })}>
          <option value="">None</option>
          {tournaments.map((t) => (
            <option key={t.id} value={String(t.id)}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field__label">Description</span>
        <textarea className="input" rows={4} value={form.description} maxLength={2000} onChange={(e) => set({ description: e.target.value })} />
      </label>
    </>
  )
}

function UploadDialog({ open, onOpenChange, tournaments, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; tournaments: Board['tournaments']; onSaved: (p: Patch) => void }) {
  const blank: Form = { title: '', game: '', description: '', tournament: '', crc: '' }
  const [form, setForm] = useState<Form>(blank)
  const [file, setFile] = useState<{ file: File; format: string; crc: string | null } | null>(null)
  const { busy, error, run, setError } = useAction()
  const picker = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (open) {
      setForm(blank)
      setFile(null)
      setError(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  async function pick(f: File) {
    setError(null)
    try {
      const info = await inspect(f)
      setFile({ file: f, ...info })
      setForm((cur) => ({ ...cur, title: cur.title || f.name.replace(/\.[A-Za-z0-9]{1,5}$/, '') }))
    } catch (e) {
      setFile(null)
      setError(message(e))
    } finally {
      if (picker.current) picker.current.value = ''
    }
  }

  async function save() {
    if (!file) {
      setError('Choose the patch file first.')
      return
    }
    let saved: Patch | null = null
    const ok = await run('save', async () => {
      saved = await sendFile('POST', '/api/admin/patches', file.file, {
        title: form.title.trim(),
        game: form.game.trim(),
        filename: file.file.name,
        description: form.description.trim() || null,
        tournament_id: form.tournament || null,
        source_crc: file.format === 'ips' ? form.crc.trim() || null : null,
      })
    })
    if (ok && saved) {
      onOpenChange(false)
      onSaved(saved)
    }
  }

  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Upload patch" primaryLabel="Upload" busy={busy === 'save'} busyLabel="Uploading…" onPrimary={() => void save()}>
      <input ref={picker} type="file" accept=".bps,.ips,.ups" hidden onChange={(e) => e.target.files?.[0] && void pick(e.target.files[0])} />
      <div className="field">
        <span className="field__label">Patch file</span>
        <div className="admin-inline">
          <span className="admin-patch-file">{file ? `${file.file.name} · ${FORMAT[file.format]} · ${size(file.file.size)}` : 'BPS, IPS or UPS, up to 16 MB'}</span>
          <Button variant="outline" icon="upload_file" onClick={() => picker.current?.click()}>
            {file ? 'Change' : 'Choose file…'}
          </Button>
        </div>
        {file?.crc && <span className="field__hint">Made for a ROM with CRC32 {file.crc}.</span>}
      </div>
      <PatchFields form={form} set={(f) => setForm((cur) => ({ ...cur, ...f }))} tournaments={tournaments} ips={file?.format === 'ips'} />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </FormDialog>
  )
}

function EditDialog({ open, onOpenChange, patch, tournaments, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; patch: Patch; tournaments: Board['tournaments']; onSaved: () => void }) {
  const from = (p: Patch): Form => ({
    title: p.title,
    game: p.game,
    description: p.description ?? '',
    tournament: p.tournament_id ? String(p.tournament_id) : '',
    crc: p.source_crc ?? '',
  })
  const [form, setForm] = useState<Form>(() => from(patch))
  const { busy, error, run, setError } = useAction()
  useEffect(() => {
    if (open) {
      setForm(from(patch))
      setError(null)
    }
  }, [open, patch, setError])
  async function save() {
    const ok = await run('save', () =>
      api
        .PUT('/api/admin/patches/{patch_id}', {
          params: { path: { patch_id: patch.id } },
          body: {
            title: form.title.trim(),
            game: form.game.trim(),
            description: form.description.trim() || null,
            tournament_id: form.tournament ? Number(form.tournament) : null,
            source_crc: patch.format === 'ips' ? form.crc.trim() || null : null,
          },
        })
        .then(unwrap),
    )
    if (ok) {
      onOpenChange(false)
      onSaved()
    }
  }
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Edit patch" primaryLabel="Save" busy={busy === 'save'} onPrimary={() => void save()}>
      <PatchFields form={form} set={(f) => setForm((cur) => ({ ...cur, ...f }))} tournaments={tournaments} ips={patch.format === 'ips'} />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </FormDialog>
  )
}

function PatchDetail({ patch, board, reload, base }: { patch: Patch; board: Board; reload: () => void; base: string }) {
  const { community } = useCommunity()
  const navigate = useNavigate()
  const [dialog, setDialog] = useState<'edit' | 'delete' | null>(null)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const { busy, run } = useAction()
  const picker = useRef<HTMLInputElement>(null)
  const playerPath = `/t/${community.slug}/patch/${patch.token}`
  const link = new URL(playerPath, window.location.href).toString()
  const zone = effectiveZone(null)

  async function replace(file: File) {
    setNote(null)
    try {
      await inspect(file)
      const ok = await run('replace', () => sendFile('PUT', `/api/admin/patches/${patch.id}/file`, file, { filename: file.name }))
      if (ok) {
        setNote({ tone: 'info', text: 'File replaced. The link stays the same.' })
        reload()
      } else {
        setNote({ tone: 'error', text: "The file wasn't replaced. Try again." })
      }
    } catch (e) {
      setNote({ tone: 'error', text: message(e) })
    } finally {
      if (picker.current) picker.current.value = ''
    }
  }

  const menu: MenuItem[] = [
    { label: 'Edit…', icon: 'edit', onSelect: () => setDialog('edit') },
    { label: 'Replace file…', icon: 'upload_file', onSelect: () => picker.current?.click() },
    { label: 'Open player page', icon: 'open_in_new', onSelect: () => navigate(playerPath) },
    { label: 'Delete patch…', icon: 'delete', destructive: true, onSelect: () => setDialog('delete') },
  ]

  return (
    <Detail
      title={patch.title}
      sub={<Chip>{FORMAT[patch.format] ?? patch.format}</Chip>}
      actions={<RowMenu label={`More for ${patch.title}`} items={menu} />}
    >
      <input ref={picker} type="file" accept=".bps,.ips,.ups" hidden onChange={(e) => e.target.files?.[0] && void replace(e.target.files[0])} />
      {busy === 'replace' && <Spinner label="Replacing the file" />}
      {note && <Banner tone={note.tone}>{note.text}</Banner>}
      <DetailSection title="Player link">
        <div className="admin-inline">
          <input className="input" readOnly value={link} aria-label="Player link" onFocus={(e) => e.target.select()} />
          <Button
            variant="outline"
            icon="content_copy"
            onClick={() => void navigator.clipboard?.writeText(link).then(() => setNote({ tone: 'info', text: 'Link copied.' }))}
          >
            Copy
          </Button>
        </div>
        <p className="admin-muted">
          Anyone with it can patch their own ROM. <Link to={playerPath}>Open it</Link>
        </p>
      </DetailSection>
      <DetailSection>
        <DetailLine icon="videogame_asset" sub={patch.source_crc ? `CRC32 ${patch.source_crc}` : "Doesn't name an exact ROM"}>
          Needs {patch.game}
        </DetailLine>
        {patch.tournament && <DetailLine icon="trophy">{patch.tournament}</DetailLine>}
        <DetailLine icon="download">{downloads(patch.download_count)}</DetailLine>
        <DetailLine icon="description" sub={`${size(patch.size)} · updated ${formatDay(patch.updated_at, zone)}${patch.uploaded_by ? ` by ${patch.uploaded_by}` : ''}`}>
          {patch.filename}
        </DetailLine>
        {patch.description && <p className="prewrap">{patch.description}</p>}
      </DetailSection>

      <EditDialog open={dialog === 'edit'} onOpenChange={(o) => setDialog(o ? 'edit' : null)} patch={patch} tournaments={board.tournaments} onSaved={reload} />
      <FormDialog
        open={dialog === 'delete'}
        onOpenChange={(o) => setDialog(o ? 'delete' : null)}
        title={`Delete ${patch.title}?`}
        size="sm"
        primaryLabel="Delete patch"
        destructive
        busy={busy === 'delete'}
        busyLabel="Deleting…"
        onPrimary={() =>
          void run('delete', () => api.DELETE('/api/admin/patches/{patch_id}', { params: { path: { patch_id: patch.id } } })).then((ok) => {
            if (!ok) return
            setDialog(null)
            reload()
            navigate(base)
          })
        }
      >
        <p style={{ margin: 0 }}>Its link stops working for everyone. This can't be undone.</p>
      </FormDialog>
    </Detail>
  )
}

function PatchesPage({ board, reload, base }: { board: Board; reload: () => void; base: string }) {
  const { patchId } = useParams()
  const navigate = useNavigate()
  const id = patchId ? Number(patchId) : null
  const open = board.patches.find((p) => p.id === id) ?? null
  const [uploading, setUploading] = useState(false)
  return (
    <>
      <PageHeader
        title="Patches"
        lede="Patches players apply to their own ROM in the browser. Share a patch's link; the ROM never leaves their device."
        actions={
          <Button icon="upload_file" onClick={() => setUploading(true)}>
            Upload patch
          </Button>
        }
      />
      <SideListLayout
        open={id !== null}
        backTo={base}
        backLabel="Patches"
        list={
          board.patches.length === 0 ? (
            <EmptyState icon="difference" title="No patches yet">
              Upload a BPS, IPS or UPS patch and share its link with players.
            </EmptyState>
          ) : (
            <SideListCard label="Patches">
              {board.patches.map((p) => (
                <SideListRow
                  key={p.id}
                  to={`${base}/${p.id}`}
                  title={p.title}
                  sub={`${p.game} · ${downloads(p.download_count)}`}
                  end={<Chip>{FORMAT[p.format] ?? p.format}</Chip>}
                />
              ))}
            </SideListCard>
          )
        }
        detail={
          open ? (
            <PatchDetail key={open.id} patch={open} board={board} reload={reload} base={base} />
          ) : id !== null ? (
            <DetailEmpty>That patch is gone. Pick another.</DetailEmpty>
          ) : (
            <DetailEmpty>Pick a patch to see its link and details.</DetailEmpty>
          )
        }
      />
      <UploadDialog
        open={uploading}
        onOpenChange={setUploading}
        tournaments={board.tournaments}
        onSaved={(p) => {
          reload()
          navigate(`${base}/${p.id}`)
        }}
      />
    </>
  )
}

/** Admin → Patches: hosted ROM patches, the list beside the open patch. */
export function PatchesScreen() {
  const { adminBase } = useAdmin()
  const base = `${adminBase}/patches`
  const { data, error, reload } = useAdminData('patches', () => api.GET('/api/admin/patches').then(unwrap))
  if (error && !data) {
    return (
      <>
        <PageHeader title="Patches" />
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if (!data) return <Spinner label="Loading patches" />
  return (
    <Routes>
      <Route path=":patchId" element={<PatchesPage board={data} reload={reload} base={base} />} />
      <Route path="*" element={<PatchesPage board={data} reload={reload} base={base} />} />
    </Routes>
  )
}
