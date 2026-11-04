import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../api/client'
import { Banner, Button, EmptyState, PageHeader, Spinner } from '../components'
import { readStored, writeStored } from '../lib/storage'
import { effectiveZone, formatDayTime } from '../lib/time'
import { bpsSourceCrc, crc32 } from './bps'
import { adjustRom, buildSeedRom, CHOICES, cleanOptions, parseSprite, RomError, stripHeader, type ChoiceKey, type DoorOptions, type SpriteChoice } from './door'
import { doorRom, fetchBytes, fetchOutside, readFile, saveBytes } from './files'
import { CopyLinkButton, RomLine } from './parts'
import { SpritePicker } from './SpritePicker'
import './patcher.css'

type Seed = Schemas['DoorSeedResponse']

const OPTIONS_KEY = 'wiz-door-patcher-options'

const LABELS: Record<ChoiceKey, { label: string; values: Record<string, string> }> = {
  heartColor: { label: 'Heart colour', values: { red: 'Red', blue: 'Blue', green: 'Green', yellow: 'Yellow', random: 'Random' } },
  heartBeep: { label: 'Low health beep', values: { off: 'Off', quarter: 'Quarter', half: 'Half', normal: 'Normal', double: 'Double' } },
  menuSpeed: {
    label: 'Menu speed',
    values: { half: 'Half', normal: 'Normal', double: 'Double', triple: 'Triple', quadruple: 'Quadruple', instant: 'Instant' },
  },
  owPalettes: {
    label: 'Overworld palettes',
    values: { default: 'Default', shuffled: 'Shuffled', legacy: 'Legacy', grayscale: 'Grayscale', blackout: 'Blackout', puke: 'Puke' },
  },
  uwPalettes: {
    label: 'Dungeon palettes',
    values: { default: 'Default', shuffled: 'Shuffled', legacy: 'Legacy', grayscale: 'Grayscale', blackout: 'Blackout', puke: 'Puke' },
  },
}
const SELECT_ORDER: ChoiceKey[] = ['heartColor', 'heartBeep', 'menuSpeed', 'owPalettes', 'uwPalettes']
const TOGGLES: { key: 'quickswap' | 'music' | 'reduceFlashing'; label: string }[] = [
  { key: 'quickswap', label: 'Item quickswap' },
  { key: 'music', label: 'Background music' },
  { key: 'reduceFlashing', label: 'Reduce flashing' },
]

function loadOptions(): DoorOptions {
  try {
    return cleanOptions(JSON.parse(readStored(OPTIONS_KEY) || 'null'))
  } catch {
    return cleanOptions(null)
  }
}

function spriteName(o: DoorOptions): string {
  if (!o.sprite || o.sprite.default) return 'Link'
  return o.sprite.random ? 'Random sprite' : o.sprite.name
}

function summary(o: DoorOptions): { main: string; sub: string } {
  const main = [spriteName(o), `${LABELS.heartColor.values[o.heartColor]!.toLowerCase()} hearts`, `${LABELS.heartBeep.values[o.heartBeep]!.toLowerCase()} beep`]
  const sub = [`Quickswap ${o.quickswap ? 'on' : 'off'}`, `music ${o.music ? 'on' : 'off'}`]
  if (o.menuSpeed !== 'normal') sub.push(`${LABELS.menuSpeed.values[o.menuSpeed]!.toLowerCase()} menu`)
  if (o.owPalettes !== 'default' || o.uwPalettes !== 'default') sub.push('palettes changed')
  if (o.reduceFlashing) sub.push('reduced flashing')
  return { main: main.join(' · '), sub: sub.join(', ') }
}

function message(e: unknown): string {
  if (e instanceof RomError || e instanceof Error) return e.message
  return 'Something went wrong building the ROM. Try again, or pick your ROM again.'
}

async function chooseSprite(choice: SpriteChoice | null): Promise<SpriteChoice | null> {
  if (!choice || choice.default) return null
  if (!choice.random) return choice
  const list = await api.GET('/api/door-seeds/sprites').then(unwrap).catch(() => [])
  const usable = list.filter((s) => s.file)
  const picked = usable[Math.floor(Math.random() * usable.length)]
  return picked ? { name: picked.name, file: picked.file ?? undefined, author: picked.author ?? undefined } : null
}

/** /t/<slug>/door/<token>: one Door Randomizer seed, built into a ROM on this device. */
export function DoorSeedScreen() {
  const { token = '' } = useParams()
  const [seed, setSeed] = useState<Seed | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [hasRom, setHasRom] = useState<boolean | null>(null)
  const [options, setOptions] = useState<DoorOptions>(loadOptions)
  const [open, setOpen] = useState(false)
  const [picker, setPicker] = useState(false)
  const [busy, setBusy] = useState<'rom' | 'download' | 'spoiler' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let live = true
    api
      .GET('/api/door-seeds/{token}', { params: { path: { token } } })
      .then(unwrap)
      .then((s) => live && setSeed(s))
      .catch((e: unknown) => live && setLoadError(message(e)))
    void doorRom.get().then((r) => live && setHasRom(Boolean(r)))
    return () => {
      live = false
    }
  }, [token])

  const update = useCallback((next: Partial<DoorOptions>) => {
    setOptions((o) => {
      const merged = { ...o, ...next }
      writeStored(OPTIONS_KEY, JSON.stringify(merged))
      return merged
    })
  }, [])

  if (loadError) {
    return (
      <EmptyState icon="link_off" title="This seed isn't here">
        {loadError} Check you have the whole link.
      </EmptyState>
    )
  }
  if (!seed) return <Spinner label="Loading the seed" />

  async function pickRom(file: File) {
    setBusy('rom')
    setError(null)
    setDone(null)
    try {
      const bytes = stripHeader(await readFile(file))
      const base = await fetchBytes(`/api/door-seeds/base/${encodeURIComponent(seed!.version)}`, 'base patch')
      if (crc32(bytes) !== bpsSourceCrc(base)) {
        throw new RomError('version', 'That ROM is not the Japanese 1.0 release. Door Randomizer needs that one.')
      }
      await doorRom.put(bytes.slice())
      setHasRom(true)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
      if (input.current) input.current.value = ''
    }
  }

  async function download() {
    setBusy('download')
    setError(null)
    setDone(null)
    try {
      const rom = await doorRom.get()
      if (!rom) {
        setHasRom(false)
        throw new Error('Pick your ROM first.')
      }
      const sprite = await chooseSprite(options.sprite)
      const [writes, base, spriteBytes] = await Promise.all([
        fetchBytes(`/api/door-seeds/${encodeURIComponent(seed!.token)}/patch`, 'seed').then(
          (b) => JSON.parse(new TextDecoder().decode(b)) as { patch: Record<string, number[]> },
        ),
        fetchBytes(`/api/door-seeds/base/${encodeURIComponent(seed!.version)}`, 'base patch'),
        sprite?.file ? fetchOutside(sprite.file, 'sprite') : Promise.resolve(null),
      ])
      const built = buildSeedRom(rom, base, writes.patch)
      adjustRom(built, options, spriteBytes ? parseSprite(spriteBytes) : null)
      saveBytes(built, seed!.filename)
      setDone(sprite && options.sprite?.random ? `Saved, with ${sprite.name} as your sprite.` : 'Saved. Check your downloads.')
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
    }
  }

  async function spoiler() {
    setBusy('spoiler')
    setError(null)
    try {
      saveBytes(await fetchBytes(`/api/door-seeds/${encodeURIComponent(seed!.token)}/spoiler`, 'spoiler log'), `DR_${seed!.token}_spoiler.json`)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
    }
  }

  const zone = effectiveZone(null)
  const s = summary(options)
  return (
    <div className="patcher">
      <PageHeader title="Door seed" lede={`Door Randomizer ${seed.version} · rolled ${formatDayTime(seed.created_at, zone)}`} />
      <section className="panel patcher-card" aria-label="Your seed">
        {seed.seed_code && seed.seed_code.length > 0 && (
          <div>
            <h2 className="patcher-label">Code</h2>
            <ul className="patcher-code">
              {seed.seed_code.map((item, i) => (
                <li key={`${item}-${i}`}>{item}</li>
              ))}
            </ul>
          </div>
        )}
        <input
          ref={input}
          type="file"
          accept=".sfc,.smc,application/octet-stream"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) void pickRom(file)
          }}
        />
        {hasRom === null ? (
          <Spinner label="Checking for your ROM" />
        ) : hasRom ? (
          <Button className="patcher-primary" icon="download" busy={busy === 'download'} busyLabel="Building your ROM…" onClick={() => void download()}>
            Download ROM
          </Button>
        ) : (
          <div className="patcher-stack">
            <Button className="patcher-primary" icon="upload_file" busy={busy === 'rom'} busyLabel="Checking your ROM…" onClick={() => input.current?.click()}>
              Choose your ROM…
            </Button>
            <p className="patcher-hint">Pick your Japanese 1.0 Link to the Past ROM. It stays on this device, and you won't be asked again.</p>
          </div>
        )}
        {error && <Banner tone="error">{error}</Banner>}
        {done && <Banner tone="info">{done}</Banner>}
        {hasRom && (
          <RomLine name="Zelda no Densetsu (J) 1.0" detail="Saved on this device" onChange={() => input.current?.click()} busy={busy === 'rom'} />
        )}
        <div className="patcher-summary">
          <div>
            <p className="patcher-summary__main">{s.main}</p>
            <p className="patcher-hint">{s.sub}</p>
          </div>
          <Button variant="outline" icon="tune" aria-expanded={open} aria-controls="door-customize" onClick={() => setOpen(!open)}>
            Customize
          </Button>
        </div>
        {open && (
          <div id="door-customize" className="patcher-customize">
            <h2 className="patcher-label">Sprite</h2>
            <div className="patcher-sprite">
              {options.sprite?.preview ? <img src={options.sprite.preview} alt="" width={32} height={48} /> : <span className="patcher-sprite__blank" aria-hidden="true" />}
              <div>
                <p className="patcher-summary__main">{spriteName(options)}</p>
                <p className="patcher-hint">{options.sprite?.author ? `By ${options.sprite.author}` : options.sprite?.random ? 'A new one each download' : 'The default'}</p>
              </div>
              <Button variant="outline" onClick={() => setPicker(true)}>
                Choose sprite…
              </Button>
            </div>
            <div className="patcher-fields">
              {SELECT_ORDER.map((key) => (
                <label key={key} className="field">
                  <span className="field__label">{LABELS[key].label}</span>
                  <select className="input" value={options[key]} onChange={(e) => update({ [key]: e.target.value } as Partial<DoorOptions>)}>
                    {CHOICES[key].map((v) => (
                      <option key={v} value={v}>
                        {LABELS[key].values[v]}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            {TOGGLES.map((t) => (
              <label key={t.key} className="patcher-switch">
                <span>{t.label}</span>
                <input type="checkbox" role="switch" className="switch" checked={options[t.key]} onChange={(e) => update({ [t.key]: e.target.checked })} />
              </label>
            ))}
            <p className="patcher-hint">Saved on this device for every door seed.</p>
          </div>
        )}
        <div className="patcher-links">
          <CopyLinkButton />
          {seed.spoiler && (
            <Button variant="ghost" icon={seed.spoiler_staff_only ? 'lock' : 'description'} busy={busy === 'spoiler'} onClick={() => void spoiler()}>
              {seed.spoiler_staff_only ? 'Spoiler log (staff)' : 'Spoiler log'}
            </Button>
          )}
        </div>
      </section>
      <SpritePicker
        open={picker}
        onOpenChange={setPicker}
        current={options.sprite}
        onPick={(sprite) => {
          update({ sprite })
          setPicker(false)
        }}
      />
    </div>
  )
}
