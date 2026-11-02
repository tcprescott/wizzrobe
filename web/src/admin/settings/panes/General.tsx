import { useEffect, useId, useRef, useState } from 'react'
import { api, authedFetch, errorMessage, unwrap, type Schemas } from '../../../api/client'
import { useQuery } from '../../../api/useQuery'
import { API_BASE } from '../../../auth/config'
import { Button, FormDialog, Spinner } from '../../../components'
import { formatDayTime } from '../../../lib/time'
import { LoadError, RadioRows, SettingsCard, useDraft } from '../../kit'

type Colours = Schemas['ThemeColours']
type Appearance = Schemas['AppearanceResponse']

const FIELDS: { key: keyof Colours; label: string; hint: string }[] = [
  { key: 'primary', label: 'Primary', hint: 'Buttons, links and the selected menu item, in light mode.' },
  { key: 'secondary', label: 'Secondary', hint: 'Secondary accents and highlights.' },
  { key: 'accent', label: 'Accent', hint: 'Highlights and links in dark mode.' },
  { key: 'header', label: 'Brand mark', hint: "The square beside your community's name when there's no logo." },
]

const HEX = /^#[0-9a-fA-F]{6}$/

function LogoCard({ data, onChanged }: { data: Appearance; onChanged: (next: Appearance) => void }) {
  const picker = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const logo = data.logo_url ? `${API_BASE}${data.logo_url}` : null

  async function upload(file: File) {
    setBusy(true)
    setError(null)
    try {
      if (file.size > 1024 * 1024) throw new Error('Logos can be up to 1 MB. Try exporting a smaller file.')
      const response = await authedFetch(
        new Request(`${API_BASE}/api/settings/logo`, {
          method: 'PUT',
          body: file,
          headers: { 'Content-Type': file.type || 'application/octet-stream' },
        }),
      )
      const body = await response.json().catch(() => undefined)
      if (!response.ok) throw new Error(errorMessage(body, response.status))
      onChanged(body as Appearance)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
      if (picker.current) picker.current.value = ''
    }
  }

  async function remove() {
    setBusy(true)
    setError(null)
    try {
      onChanged(await api.DELETE('/api/settings/logo').then(unwrap))
      setConfirm(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel settings-card" aria-labelledby="logo-title">
      <header className="settings-card__head">
        <div className="settings-card__heading">
          <h2 className="settings-card__title" id="logo-title">
            Logo
          </h2>
          <p className="settings-card__sub">
            Replaces the brand mark beside your community's name and becomes the browser tab icon. An SVG, or a square PNG, JPEG
            or WebP at least 64×64, up to 1 MB. Saves as soon as you pick one.
          </p>
        </div>
      </header>
      <div className="settings-card__body">
        <div className="settings-logos">
          {(['light', 'dark'] as const).map((tone) => (
            <div key={tone} className={`settings-logo settings-logo--${tone}`}>
              {logo ? (
                <img src={logo} alt="" width={28} height={28} />
              ) : (
                <span className="settings-logo__mark" style={{ background: data.colours.header }} />
              )}
              <span>{tone === 'light' ? 'Light header' : 'Dark header'}</span>
            </div>
          ))}
        </div>
        <input
          ref={picker}
          type="file"
          accept=".svg,.png,.jpg,.jpeg,.webp"
          className="visually-hidden"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void upload(file)
          }}
        />
        <div className="settings-actions">
          {logo && (
            <Button variant="ghost" icon="delete" onClick={() => setConfirm(true)} disabled={busy}>
              Remove logo
            </Button>
          )}
          <Button
            variant="outline"
            icon="upload"
            busy={busy && !confirm}
            busyLabel="Uploading…"
            onClick={() => picker.current?.click()}
          >
            {logo ? 'Replace logo' : 'Upload logo'}
          </Button>
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <FormDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Remove logo?"
        description="The coloured brand mark shows in its place until someone uploads a new one."
        primaryLabel="Remove"
        destructive
        busy={busy}
        busyLabel="Removing…"
        onPrimary={() => void remove()}
        size="sm"
      >
        {null}
      </FormDialog>
    </section>
  )
}

function ColourField({
  label,
  hint,
  value,
  issues,
  onChange,
}: {
  label: string
  hint: string
  value: string
  issues: string[]
  onChange: (v: string) => void
}) {
  const id = useId()
  return (
    <div className="field settings-colour">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      <div className="settings-colour__inputs">
        <input
          type="color"
          className="settings-colour__swatch"
          value={HEX.test(value) ? value : '#000000'}
          aria-label={`${label} colour picker`}
          onChange={(e) => onChange(e.target.value)}
        />
        <input
          id={id}
          className="input settings-colour__hex"
          value={value}
          maxLength={7}
          spellCheck={false}
          aria-describedby={`${id}-hint`}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
      <span className="field__hint" id={`${id}-hint`}>
        {hint}
      </span>
      {issues.map((issue) => (
        <span key={issue} className="settings-warning">
          {issue}
        </span>
      ))}
    </div>
  )
}

function ColoursCard({ data, onChanged }: { data: Appearance; onChanged: (next: Appearance) => void }) {
  const draft = useDraft<Colours>(data.colours)
  const value = draft.value ?? data.colours
  const [issues, setIssues] = useState<Record<string, string[]>>(data.issues ?? {})
  const presetId = useId()

  useEffect(() => {
    if (!draft.dirty) {
      setIssues(data.issues ?? {})
      return
    }
    const colours: Record<string, string> = { ...value }
    if (data.logo_url) delete colours.header
    const timer = window.setTimeout(() => {
      void api
        .POST('/api/settings/appearance/contrast', { body: { colours } })
        .then(unwrap)
        .then((r) => setIssues(r.issues))
        .catch(() => undefined)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [draft.dirty, value, data.issues, data.logo_url])

  const fields = data.logo_url ? FIELDS.filter((f) => f.key !== 'header') : FIELDS
  return (
    <SettingsCard
      title="Colours"
      icon="palette"
      subtitle="Your community's brand colours. Status colours stay fixed so everyone reads them the same way."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        onChanged(await api.PUT('/api/settings/appearance', { body: value }).then(unwrap))
        draft.discard()
      }}
    >
      <div className="field">
        <label className="field__label" htmlFor={presetId}>
          Start from a preset
        </label>
        <select
          id={presetId}
          className="input"
          value=""
          onChange={(e) => {
            const preset = data.presets.find((p) => p.name === e.target.value)
            if (preset) draft.set(preset.colours)
          }}
        >
          <option value="">Pick a preset…</option>
          {data.presets.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
        <span className="field__hint">Presets already meet AA contrast. Pick one, then fine-tune below.</span>
      </div>
      <div className="field-grid">
        {fields.map((f) => (
          <ColourField
            key={f.key}
            label={f.label}
            hint={f.hint}
            value={value[f.key]}
            issues={issues[f.key] ?? []}
            onChange={(v) => draft.set({ ...value, [f.key]: v })}
          />
        ))}
      </div>
      <Button variant="ghost" icon="restart_alt" className="settings-inline-btn" onClick={() => draft.set(data.defaults)}>
        Use the default colours
      </Button>
    </SettingsCard>
  )
}

function ClockCard() {
  const zoneId = useId()
  const { data, error, reload } = useQuery('settings-timezone', () => api.GET('/api/settings/timezone').then(unwrap))
  const saved = data && { mode: data.mode, name: data.name }
  const draft = useDraft(saved)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data || !draft.value) return <Spinner label="Loading the clock" />
  const value = draft.value
  let preview = ''
  try {
    preview = formatDayTime(new Date().toISOString(), value.name)
  } catch {
    preview = ''
  }
  return (
    <SettingsCard
      title="Clock"
      icon="schedule"
      subtitle="Times are stored precisely. This only sets the clock they're shown on."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api.PUT('/api/settings/timezone', { body: value }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      <RadioRows
        label="Whose clock"
        name="tz-mode"
        value={value.mode}
        options={[
          {
            value: 'pinned',
            label: 'Show one timezone to everybody',
            hint: 'For an on-site event, where 14:30 means 14:30 at the venue.',
          },
          {
            value: 'user',
            label: "Follow each person's own timezone",
            hint: 'For online play, where a player in Berlin and one in Los Angeles each see their own time.',
          },
        ]}
        onChange={(mode) => draft.patch({ mode })}
      />
      <div className="field settings-number">
        <label className="field__label" htmlFor={zoneId}>
          Timezone
        </label>
        <select id={zoneId} className="input" value={value.name} onChange={(e) => draft.patch({ name: e.target.value })}>
          {data.options.map((z) => (
            <option key={z} value={z}>
              {z.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
        <span className="field__hint">
          {value.mode === 'pinned'
            ? `Everyone sees times in ${value.name}.`
            : `The fallback for anyone whose timezone can't be detected, and for shared pages like public brackets.`}
          {preview && ` Right now it reads ${preview}.`}
        </span>
      </div>
    </SettingsCard>
  )
}

export function General() {
  const { data, error, reload } = useQuery('settings-appearance', () => api.GET('/api/settings/appearance').then(unwrap))
  const [fresh, setFresh] = useState<Appearance | null>(null)
  const current = fresh ?? data
  if (error && !current) return <LoadError error={error} onRetry={reload} />
  if (!current) return <Spinner label="Loading appearance" />
  return (
    <>
      <LogoCard data={current} onChanged={setFresh} />
      <ColoursCard data={current} onChanged={setFresh} />
      <ClockCard />
    </>
  )
}
