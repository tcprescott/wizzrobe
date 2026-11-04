import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { useQuery } from '../api/useQuery'
import { Banner, Button, FormDialog, IconButton, SearchField, Spinner } from '../components'
import { message } from '../me/data'
import { MysteryOdds } from './MysteryOdds'
import { useMe } from '../screens/useMe'

type Detail = Schemas['LibraryPresetDetailResponse']
type Row = Schemas['LibraryPresetResponse']
type Space = Schemas['PresetSpaceResponse']
type Member = Schemas['PresetSpaceMember']
type Randomizer = Schemas['LibraryRandomizerResponse']

const NAME_HINT = 'Lowercase letters, numbers, - and _, up to 32. It goes after the slash.'

export function plainName(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '')
    .slice(0, 32)
}

function useRandomizers() {
  return useQuery('preset-library/randomizers', () => api.GET('/api/preset-library/randomizers').then(unwrap))
}

function SpacePicker({ spaces, value, onChange }: { spaces: Space[]; value: number | null; onChange: (id: number) => void }) {
  if (spaces.length < 2) return null
  return (
    <label className="field">
      <span className="field__label">Space</span>
      <select className="input" value={value ?? ''} onChange={(e) => onChange(Number(e.target.value))}>
        {spaces.map((s) => (
          <option key={s.id} value={s.id}>
            {s.slug}
            {s.is_owner ? ' (yours)' : ` (${s.owner.name}'s)`}
          </option>
        ))}
      </select>
    </label>
  )
}

/** New preset in a personal space, or editing one: game (new only), name, note and settings. */
export function PresetFormDialog({
  preset,
  spaces,
  onClose,
  onSaved,
}: {
  preset?: Detail
  spaces: Space[]
  onClose: () => void
  onSaved: (p: Detail) => void
}) {
  const randomizers = useRandomizers()
  const [spaceId, setSpaceId] = useState<number | null>(spaces[0]?.id ?? null)
  const [game, setGame] = useState(preset?.randomizer ?? 'alttpr')
  const [name, setName] = useState(preset?.name ?? '')
  const [note, setNote] = useState(preset?.description ?? '')
  const info: Randomizer | undefined = randomizers.data?.find((r) => r.slug === game)
  const pasteKey = info?.paste_key ?? null
  const settings = (preset?.settings ?? {}) as Record<string, unknown>
  const [json, setJson] = useState(JSON.stringify(settings, null, 2))
  const [pasted, setPasted] = useState(pasteKey ? String(settings[pasteKey] ?? '') : '')
  const mystery = game === 'alttprmystery'
  const [weightset, setWeightset] = useState(preset?.settings_yaml ?? '')
  const [odds, setOdds] = useState<Schemas['MysterySettingOdds'][] | null>(preset?.odds ?? null)
  const [busy, setBusy] = useState(false)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function checkWeightset(): Promise<Record<string, unknown> | null> {
    setChecking(true)
    setError(null)
    try {
      const checked = await api.POST('/api/preset-library/mystery/check', { body: { weightset } }).then(unwrap)
      setOdds(checked.odds)
      return checked.settings
    } catch (err) {
      setOdds(null)
      setError(message(err))
      return null
    } finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    if (pasteKey && preset) setPasted(String(settings[pasteKey] ?? ''))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pasteKey])

  async function save() {
    let next: Record<string, unknown>
    if (mystery) {
      const checked = await checkWeightset()
      if (!checked) return
      next = checked
    } else if (pasteKey) next = { ...settings, [pasteKey]: pasted.trim() }
    else {
      try {
        const parsed: unknown = JSON.parse(json || '{}')
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
        next = parsed as Record<string, unknown>
      } catch {
        setError("The settings aren't a valid JSON object.")
        return
      }
    }
    setBusy(true)
    setError(null)
    try {
      const saved = preset
        ? await api
            .PATCH('/api/preset-library/personal/{preset_id}', {
              params: { path: { preset_id: preset.id } },
              body: { name: name.trim(), description: note, settings: next },
            })
            .then(unwrap)
        : await api
            .POST('/api/preset-library/personal', {
              body: { name: name.trim(), randomizer: game, description: note || null, settings: next, space_id: spaceId },
            })
            .then(unwrap)
      onSaved(saved)
    } catch (err) {
      setError(message(err))
      setBusy(false)
    }
  }

  const where = preset ? preset.space : (spaces.find((s) => s.id === spaceId)?.slug ?? 'your space')
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={preset ? `Edit ${preset.reference}` : 'New preset'}
      description={preset ? 'Copies other people made keep their own settings.' : 'It shows in the library as soon as you save it, for anyone to read and roll.'}
      primaryLabel={preset ? 'Save' : 'Save preset'}
      onPrimary={() => void save()}
      busy={busy}
      busyLabel="Saving…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        {!preset && <SpacePicker spaces={spaces} value={spaceId} onChange={setSpaceId} />}
        {!preset &&
          (randomizers.data ? (
            <label className="field">
              <span className="field__label">Game</span>
              <select className="input" value={game} onChange={(e) => setGame(e.target.value)}>
                {randomizers.data.map((r) => (
                  <option key={r.slug} value={r.slug}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <Spinner label="Loading games" />
          ))}
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input mono" value={name} maxLength={32} onChange={(e) => setName(e.target.value.toLowerCase())} />
          <span className="field__hint">
            {NAME_HINT} Full name: <span className="preset-ref">{where}/{name || '…'}</span>
          </span>
        </label>
        <label className="field">
          <span className="field__label">Note</span>
          <textarea className="input" rows={2} maxLength={2000} value={note} placeholder="What it's for, who races it" onChange={(e) => setNote(e.target.value)} />
        </label>
        {mystery ? (
          <>
            <label className="field">
              <span className="field__label">Weightset (YAML)</span>
              <textarea
                className="input mono"
                rows={10}
                spellCheck={false}
                value={weightset}
                placeholder={'world_state:\n  open: 50\n  standard: 50\n…'}
                onChange={(e) => {
                  setWeightset(e.target.value)
                  setOdds(null)
                }}
              />
              <span className="field__hint">
                SahasrahBot's mystery format. To start from one, copy <span className="preset-ref">wizzrobe/friendly</span> to your presets and edit it.
                Rolled settings stay hidden from players.
              </span>
            </label>
            <div>
              <Button variant="outline" icon="casino" busy={checking} disabled={!weightset.trim() || busy} onClick={() => void checkWeightset()}>
                Check odds
              </Button>
            </div>
            {odds && <MysteryOdds odds={odds} />}
          </>
        ) : pasteKey ? (
          <label className="field">
            <span className="field__label">{info?.paste_label}</span>
            <input className="input mono" value={pasted} onChange={(e) => setPasted(e.target.value)} />
            {info?.paste_hint && <span className="field__hint">{info.paste_hint}</span>}
          </label>
        ) : (
          <label className="field">
            <span className="field__label">Settings (JSON)</span>
            <textarea className="input mono" rows={8} spellCheck={false} value={json} onChange={(e) => setJson(e.target.value)} />
            <span className="field__hint">The settings the randomizer takes. Leave it as {'{}'} for its defaults.</span>
          </label>
        )}
      </div>
    </FormDialog>
  )
}

export function CopyToMineDialog({
  preset,
  spaces,
  onClose,
  onCopied,
}: {
  preset: Row
  spaces: Space[]
  onClose: () => void
  onCopied: (p: Detail) => void
}) {
  const [name, setName] = useState(plainName(preset.name))
  const [spaceId, setSpaceId] = useState<number | null>(spaces[0]?.id ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function copy() {
    setBusy(true)
    setError(null)
    try {
      onCopied(
        await api
          .POST('/api/preset-library/{preset_id}/copy-to-mine', {
            params: { path: { preset_id: preset.id } },
            body: { name: name.trim(), space_id: spaceId },
          })
          .then(unwrap),
      )
    } catch (err) {
      setError(message(err))
      setBusy(false)
    }
  }
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Copy to my presets"
      size="sm"
      primaryLabel="Copy preset"
      busy={busy}
      onPrimary={() => void copy()}
      description={
        <>
          Your copy is yours to change. If <span className="preset-ref">{preset.reference}</span> changes later, you'll be offered an update.
        </>
      }
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <SpacePicker spaces={spaces} value={spaceId} onChange={setSpaceId} />
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input mono" value={name} maxLength={32} onChange={(e) => setName(e.target.value.toLowerCase())} />
          <span className="field__hint">{NAME_HINT}</span>
        </label>
      </div>
    </FormDialog>
  )
}

/** The owner renames their space and picks co-editors; a co-editor can only leave. */
export function SpaceDialog({ space, onClose, onChange }: { space: Space; onClose: () => void; onChange: () => void }) {
  const { me } = useMe()
  const [current, setCurrent] = useState(space)
  const [slug, setSlug] = useState(space.slug)
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<Member[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!current.is_owner || query.trim().length < 2) {
      setFound([])
      return
    }
    const timer = setTimeout(() => {
      api
        .GET('/api/preset-library/members', { params: { query: { q: query.trim() } } })
        .then(unwrap)
        .then(setFound)
        .catch(() => setFound([]))
    }, 300)
    return () => clearTimeout(timer)
  }, [query, current.is_owner])

  async function run(key: string, call: () => Promise<Space>, after?: () => void) {
    setBusy(key)
    setError(null)
    try {
      const next = await call()
      setCurrent(next)
      onChange()
      after?.()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(null)
    }
  }

  const path = { params: { path: { space_id: current.id } } }
  const editorIds = new Set(current.editors.map((e) => e.id))

  if (!current.is_owner) {
    return (
      <FormDialog
        open
        onOpenChange={(o) => !o && onClose()}
        title={current.slug}
        size="sm"
        description={`${current.owner.name} lets you add, edit and delete presets here. Only they can rename it or change who edits it.`}
        primaryLabel="Leave this space"
        destructive
        busy={busy === 'leave'}
        cancelLabel="Close"
        onPrimary={() =>
          void (async () => {
            if (!me) return
            setBusy('leave')
            setError(null)
            try {
              await api
                .DELETE('/api/preset-library/spaces/{space_id}/editors/{user_id}', {
                  params: { path: { space_id: current.id, user_id: me.id } },
                })
                .then(unwrap)
              onChange()
              onClose()
            } catch (err) {
              setError(message(err))
              setBusy(null)
            }
          })()
        }
      >
        {error && <Banner tone="error">{error}</Banner>}
      </FormDialog>
    )
  }

  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="My preset space"
      cancelLabel="Close"
      primaryLabel="Save name"
      busy={busy === 'slug'}
      busyLabel="Saving…"
      onPrimary={() =>
        void run('slug', () => api.PATCH('/api/preset-library/spaces/{space_id}', { ...path, body: { slug: slug.trim() } }).then(unwrap))
      }
      description={`${current.preset_count} of ${current.max_presets} presets. Everything here shows in the library.`}
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Space name</span>
          <input className="input mono" value={slug} maxLength={32} onChange={(e) => setSlug(e.target.value.toLowerCase())} />
          <span className="field__hint">
            The part before the slash, like <span className="preset-ref">{slug || '…'}/fastboots</span>. Lowercase letters, numbers, - and _.
          </span>
        </label>
        <fieldset className="field preset-fieldset">
          <legend className="field__label">
            Co-editors ({current.editors.length} of {current.max_editors})
          </legend>
          <span className="field__hint">They can add, edit and delete presets here. Only you can rename the space or change this list.</span>
          {current.editors.length === 0 ? (
            <p className="preset-muted">Nobody else edits your presets yet.</p>
          ) : (
            <ul className="preset-editors">
              {current.editors.map((e) => (
                <li key={e.id}>
                  <span>{e.name}</span>
                  <IconButton
                    icon="person_remove"
                    label={`Remove ${e.name}`}
                    disabled={busy !== null}
                    onClick={() =>
                      void run(`rm-${e.id}`, () =>
                        api
                          .DELETE('/api/preset-library/spaces/{space_id}/editors/{user_id}', {
                            params: { path: { space_id: current.id, user_id: e.id } },
                          })
                          .then(unwrap),
                      )
                    }
                  />
                </li>
              ))}
            </ul>
          )}
          {current.editors.length < current.max_editors && (
            <>
              <SearchField label="Find a member to add" value={query} onChange={setQuery} />
              {found.filter((m) => !editorIds.has(m.id)).length > 0 && (
                <ul className="preset-editors">
                  {found
                    .filter((m) => !editorIds.has(m.id))
                    .map((m) => (
                      <li key={m.id}>
                        <span>{m.name}</span>
                        <Button
                          size="sm"
                          variant="outline"
                          icon="person_add"
                          busy={busy === `add-${m.id}`}
                          disabled={busy !== null}
                          onClick={() =>
                            void run(
                              `add-${m.id}`,
                              () => api.POST('/api/preset-library/spaces/{space_id}/editors', { ...path, body: { user_id: m.id } }).then(unwrap),
                              () => setQuery(''),
                            )
                          }
                        >
                          Add
                        </Button>
                      </li>
                    ))}
                </ul>
              )}
            </>
          )}
        </fieldset>
      </div>
    </FormDialog>
  )
}

const GAME_NAMES: Record<string, string> = { alttpr: 'A Link to the Past', alttprmystery: 'ALTTPR mystery', sm: 'Super Metroid', smz3: 'SMZ3' }

/** Look up a SahasrahBot namespace, tick its presets, copy them into a personal space. */
export function SahasrahBotImportDialog({ spaces, onClose, onImported }: { spaces: Space[]; onClose: () => void; onImported: () => void }) {
  const [namespace, setNamespace] = useState('')
  const [looked, setLooked] = useState<string | null>(null)
  const [found, setFound] = useState<Schemas['SahasrahBotPresetResponse'][] | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [spaceId, setSpaceId] = useState<number | null>(spaces[0]?.id ?? null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<Schemas['SahasrahBotImportResult'][] | null>(null)
  const key = (p: { randomizer: string; name: string }) => `${p.randomizer}/${p.name}`

  async function lookUp() {
    const ns = namespace.trim()
    if (!ns) return
    setBusy('look')
    setError(null)
    setResults(null)
    try {
      const rows = await api.GET('/api/preset-library/sahasrahbot/{namespace}', { params: { path: { namespace: ns } } }).then(unwrap)
      setFound(rows)
      setLooked(ns)
      setPicked(new Set(rows.filter((r) => !r.exists).slice(0, 25).map(key)))
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(null)
    }
  }

  async function run() {
    if (!looked || picked.size === 0) {
      setError('Look up a namespace and tick at least one preset.')
      return
    }
    setBusy('import')
    setError(null)
    try {
      const chosen = (found ?? []).filter((r) => picked.has(key(r))).map((r) => ({ randomizer: r.randomizer, name: r.name }))
      const done = await api
        .POST('/api/preset-library/sahasrahbot/{namespace}/import', {
          params: { path: { namespace: looked } },
          body: { presets: chosen, space_id: spaceId },
        })
        .then(unwrap)
      setResults(done)
      setFound(null)
      setPicked(new Set())
      onImported()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(null)
    }
  }

  const toggle = (k: string) => {
    const next = new Set(picked)
    if (next.has(k)) next.delete(k)
    else next.add(k)
    setPicked(next)
  }
  const imported = results?.filter((r) => r.preset_id != null) ?? []
  const problems = results?.filter((r) => r.problem) ?? []

  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Import from SahasrahBot"
      description="Copies presets from a SahasrahBot namespace into your space. ALTTPR, Super Metroid and SMZ3 presets come across."
      primaryLabel={picked.size ? `Import ${picked.size}` : 'Import'}
      cancelLabel={results ? 'Close' : 'Cancel'}
      busy={busy === 'import'}
      busyLabel="Importing…"
      onPrimary={() => void run()}
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        {results && (
          <Banner tone={problems.length ? 'warning' : 'info'}>
            {imported.length ? `Imported ${imported.length} ${imported.length === 1 ? 'preset' : 'presets'}.` : 'Nothing imported.'}
            {problems.length > 0 && (
              <ul className="preset-problems">
                {problems.map((p) => (
                  <li key={key(p)}>
                    <span className="preset-ref">{p.name}</span>: {p.problem}
                  </li>
                ))}
              </ul>
            )}
          </Banner>
        )}
        <SpacePicker spaces={spaces} value={spaceId} onChange={setSpaceId} />
        <form
          className="preset-lookup"
          onSubmit={(e) => {
            e.preventDefault()
            void lookUp()
          }}
        >
          <label className="field">
            <span className="field__label">SahasrahBot namespace</span>
            <input className="input mono" value={namespace} maxLength={64} placeholder="e.g. tom" onChange={(e) => setNamespace(e.target.value)} />
            <span className="field__hint">The part before the slash in your SahasrahBot preset names.</span>
          </label>
          <Button type="submit" variant="outline" busy={busy === 'look'} disabled={!namespace.trim()}>
            Look up
          </Button>
        </form>
        {busy === 'look' && <Spinner label="Asking SahasrahBot" />}
        {found && found.length === 0 && <p className="preset-muted">{looked} has no ALTTPR, ALTTPR mystery, Super Metroid or SMZ3 presets on SahasrahBot.</p>}
        {found && found.length > 0 && (
          <fieldset className="field preset-fieldset">
            <legend className="field__label">
              Presets in {looked} ({picked.size} of {found.length} ticked, 25 at a time)
            </legend>
            {found.map((r) => (
              <label key={key(r)} className="t-check">
                <input type="checkbox" checked={picked.has(key(r))} disabled={r.exists} onChange={() => toggle(key(r))} />
                <span>
                  {r.name}
                  <span className="t-row__sub"> · {GAME_NAMES[r.randomizer] ?? r.randomizer}{r.exists ? ' · already in your space' : ''}</span>
                </span>
              </label>
            ))}
          </fieldset>
        )}
      </div>
    </FormDialog>
  )
}
