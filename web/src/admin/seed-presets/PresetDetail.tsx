import { useEffect, useState, type ReactNode } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, FormDialog, Panel, PanelRow } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { done, message } from '../../me/data'
import type { Preset, RandomizerInfo } from './SeedPresetsScreen'

type Check = Schemas['PasteCheckResponse']

function pretty(settings: Record<string, unknown>): string {
  return JSON.stringify(settings, null, 2)
}

/** One preset: its name and note, its settings (a pasted value or JSON), a test roll, and where it's used. */
export function PresetDetail({
  preset,
  info,
  tournaments,
  onChange,
  onDeleted,
}: {
  preset: Preset
  info: RandomizerInfo | undefined
  tournaments: string[]
  onChange: () => void
  onDeleted: () => void
}) {
  const Heading = usePhone() ? 'h1' : 'h2'
  const paste = info?.paste_field ?? null
  const settings = preset.settings as Record<string, unknown>
  const initialPaste = paste ? String(settings[paste.key] ?? '') : ''
  const [name, setName] = useState(preset.name)
  const [description, setDescription] = useState(preset.description ?? '')
  const [json, setJson] = useState(pretty(settings))
  const [pasted, setPasted] = useState(initialPaste)
  const [check, setCheck] = useState<Check | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: ReactNode } | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    setName(preset.name)
    setDescription(preset.description ?? '')
    setJson(pretty(preset.settings as Record<string, unknown>))
    setPasted(initialPaste)
  }, [preset, initialPaste])

  useEffect(() => {
    if (!paste || !pasted.trim()) {
      setCheck(null)
      return
    }
    const timer = setTimeout(() => {
      api
        .POST('/api/admin/presets/check-paste', { body: { randomizer: preset.randomizer, value: pasted.trim() } })
        .then(unwrap)
        .then(setCheck)
        .catch(() => setCheck(null))
    }, 400)
    return () => clearTimeout(timer)
  }, [paste, pasted, preset.randomizer])

  const settingsDirty = paste ? pasted !== initialPaste : json !== pretty(settings)
  const dirty = name !== preset.name || description !== (preset.description ?? '') || settingsDirty

  async function save() {
    let next: Record<string, unknown> | undefined
    if (settingsDirty) {
      if (paste) next = { ...settings, [paste.key]: pasted.trim() }
      else {
        try {
          const parsed: unknown = JSON.parse(json)
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
          next = parsed as Record<string, unknown>
        } catch {
          setNote({ tone: 'error', text: "The settings aren't a valid JSON object." })
          return
        }
      }
    }
    setBusy('save')
    setNote(null)
    try {
      await api
        .PATCH('/api/presets/{preset_id}', {
          params: { path: { preset_id: preset.id } },
          body: { name: name.trim(), description, ...(next ? { settings: next } : {}) },
        })
        .then(unwrap)
      setNote({ tone: 'info', text: 'Saved.' })
      onChange()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(null)
    }
  }

  async function testRoll() {
    setBusy('roll')
    setNote(null)
    try {
      const r = await api.POST('/api/admin/presets/{preset_id}/test-roll', { params: { path: { preset_id: preset.id } } }).then(unwrap)
      setNote({
        tone: 'info',
        text: (
          <>
            Rolled a test seed:{' '}
            <a href={r.seed_url} target="_blank" rel="noopener noreferrer">
              {r.seed_url}
            </a>
            {r.used_preset_settings ? '' : ` (${info?.name ?? r.randomizer} ignores preset settings)`}
          </>
        ),
      })
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <div className="detail-head">
        <div className="detail-head__text">
          <Heading>{preset.name}</Heading>
          <div className="detail-head__sub">
            <span>{info?.name ?? preset.randomizer}</span>
            {info && !info.available && <Chip tone="pending">Needs an API key</Chip>}
          </div>
        </div>
        <ActionsMenu
          label="Actions"
          items={[
            { label: busy === 'roll' ? 'Rolling…' : 'Roll a test seed', icon: 'sync', disabled: busy === 'roll' || !info?.available, onSelect: () => void testRoll() },
            { label: 'Delete preset…', icon: 'delete', destructive: true, onSelect: () => setDeleting(true) },
          ]}
        />
      </div>
      {note && <Banner tone={note.tone}>{note.text}</Banner>}
      <form
        className="t-form"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <Panel title="Preset">
          <PanelRow>
            <div className="t-form">
              <label className="field">
                <span className="field__label">Name</span>
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="field">
                <span className="field__label">Note</span>
                <input className="input" value={description} placeholder="Shown next to the preset" onChange={(e) => setDescription(e.target.value)} />
              </label>
            </div>
          </PanelRow>
        </Panel>
        <Panel title="Settings">
          <PanelRow>
            {paste ? (
              <div className="t-form">
                <label className="field">
                  <span className="field__label">{paste.label}</span>
                  <input className="input mono" value={pasted} placeholder={paste.placeholder} onChange={(e) => setPasted(e.target.value)} />
                  <span className="field__hint">{paste.hint}</span>
                </label>
                {check && check.kind !== 'none' && <PasteSummary check={check} />}
              </div>
            ) : (
              <label className="field" style={{ width: '100%' }}>
                <span className="field__label">Settings (JSON)</span>
                <textarea className="input mono" rows={14} spellCheck={false} value={json} onChange={(e) => setJson(e.target.value)} />
              </label>
            )}
          </PanelRow>
        </Panel>
        <Panel title="Used by">
          <PanelRow>
            {tournaments.length ? (
              <span>{tournaments.join(', ')}</span>
            ) : (
              <span className="t-row__sub">No tournament rolls from this preset.</span>
            )}
          </PanelRow>
        </Panel>
        <div className="t-actions">
          <Button
            variant="ghost"
            disabled={!dirty || !!busy}
            onClick={() => {
              setName(preset.name)
              setDescription(preset.description ?? '')
              setJson(pretty(settings))
              setPasted(initialPaste)
            }}
          >
            Undo changes
          </Button>
          <Button type="submit" disabled={!dirty || !name.trim()} busy={busy === 'save'} busyLabel="Saving…">
            Save
          </Button>
        </div>
      </form>
      {deleting && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setDeleting(false)}
          title={`Delete ${preset.name}?`}
          description={
            tournaments.length
              ? `${tournaments.join(', ')} ${tournaments.length === 1 ? 'rolls' : 'roll'} from it and will fall back to the randomizer's defaults.`
              : 'No tournament uses it.'
          }
          primaryLabel="Delete preset"
          destructive
          busy={busy === 'delete'}
          busyLabel="Deleting…"
          onPrimary={async () => {
            setBusy('delete')
            try {
              done(await api.DELETE('/api/presets/{preset_id}', { params: { path: { preset_id: preset.id } } }))
              setDeleting(false)
              onDeleted()
            } catch (err) {
              setNote({ tone: 'error', text: message(err) })
              setDeleting(false)
            } finally {
              setBusy(null)
            }
          }}
        >
          <p style={{ margin: 0 }}>This can't be undone.</p>
        </FormDialog>
      )}
    </>
  )
}

function PasteSummary({ check }: { check: Check }) {
  return (
    <dl className="t-kv">
      {check.site_version && (
        <>
          <dt>Site version</dt>
          <dd>{check.site_version}</dd>
        </>
      )}
      {check.example && (
        <>
          <dt>Players get</dt>
          <dd className="mono">{check.example}</dd>
        </>
      )}
      {check.build && (
        <>
          <dt>Build</dt>
          <dd>
            {check.build}
            {check.version ? ` ${check.version}` : ''}
          </dd>
        </>
      )}
      {check.summary && check.summary.length > 0 && (
        <>
          <dt>Headline settings</dt>
          <dd style={{ whiteSpace: 'pre-line' }}>{check.summary.join('\n')}</dd>
        </>
      )}
    </dl>
  )
}
