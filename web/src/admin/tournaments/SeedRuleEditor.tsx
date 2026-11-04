import { useEffect, useId, useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Button, IconButton, Menu, RowMenu, type MenuItem } from '../../components'
import { message } from '../../me/data'
import { Detail, DetailSection } from '../kit'
import { InstantField } from '../kit/fields'
import {
  CONDITION_LABEL,
  conditionsOf,
  parseTweak,
  tidy,
  tweakText,
  type ConditionKey,
  type Preview,
  type Rule,
  type RuleWhen,
  type SeedEditor,
} from './seedRules'

interface Props {
  rule: Rule
  index: number
  rules: Rule[]
  editor: SeedEditor
  tournamentId: number
  zone: string | null
  onChange: (rule: Rule) => void
  menu: MenuItem[]
}

const GAMES: (number | 'last')[] = [1, 2, 3, 4, 5, 'last']

/** The open rule: name, its conditions, what it rolls, tweaks, and a live preview. */
export function SeedRuleEditor({ rule, index, rules, editor, tournamentId, zone, onChange, menu }: Props) {
  const nameId = useId()
  const when = rule.when ?? {}
  const shown = conditionsOf(when)
  const missing = (Object.keys(CONDITION_LABEL) as ConditionKey[]).filter((k) => !shown.includes(k))
  const setWhen = (patch: Partial<RuleWhen>) => onChange({ ...rule, when: { ...when, ...patch } })
  const drop = (key: ConditionKey) => {
    const next: RuleWhen = { ...when }
    if (key === 'dates') {
      delete next.from
      delete next.to
    } else delete next[key]
    onChange({ ...rule, when: next })
  }
  const add = (key: ConditionKey) => {
    if (key === 'round') setWhen({ round: [] })
    if (key === 'game') setWhen({ game: [] })
    if (key === 'title') setWhen({ title: [] })
    if (key === 'on_stage') setWhen({ on_stage: true })
    if (key === 'dates') setWhen({ from: new Date().toISOString() })
  }

  return (
    <Detail
      title={`Rule ${index + 1}`}
      sub={rule.name || 'No name yet'}
      actions={<RowMenu label={`More for rule ${index + 1}`} items={menu} />}
    >
      <DetailSection>
        <label className="field" htmlFor={nameId}>
          <span className="field__label">Rule name</span>
          <input
            id={nameId}
            className="input"
            maxLength={80}
            value={rule.name ?? ''}
            placeholder={`Rule ${index + 1}`}
            onChange={(e) => onChange({ ...rule, name: e.target.value })}
          />
        </label>
      </DetailSection>

      <DetailSection title="When">
        {shown.length === 0 && <p className="sr-hint">Every match, until you add a condition.</p>}
        <div className="sr-conditions">
          {shown.map((key) => (
            <div key={key} className="sr-cond">
              <span className="sr-cond__label">{CONDITION_LABEL[key]}</span>
              <div className="sr-cond__body">
                <ConditionInput kind={key} when={when} editor={editor} zone={zone} setWhen={setWhen} />
              </div>
              <IconButton icon="close" label={`Remove ${CONDITION_LABEL[key]}`} onClick={() => drop(key)} />
            </div>
          ))}
        </div>
        {missing.length > 0 && (
          <Menu
            trigger={
              <Button variant="ghost" size="sm" icon="add">
                Add condition
              </Button>
            }
            items={missing.map((k) => ({ label: CONDITION_LABEL[k], onSelect: () => add(k) }))}
          />
        )}
      </DetailSection>

      <DetailSection title="Then">
        <Outcome rule={rule} editor={editor} onChange={onChange} />
      </DetailSection>

      <DetailSection title="Setting tweaks">
        <Tweaks rule={rule} onChange={onChange} />
      </DetailSection>

      <DetailSection title="Preview">
        <PreviewPanel rules={rules} openId={rule.id ?? ''} editor={editor} tournamentId={tournamentId} />
      </DetailSection>
    </Detail>
  )
}

function ConditionInput({
  kind,
  when,
  editor,
  zone,
  setWhen,
}: {
  kind: ConditionKey
  when: RuleWhen
  editor: SeedEditor
  zone: string | null
  setWhen: (patch: Partial<RuleWhen>) => void
}) {
  if (kind === 'round') {
    const picked = when.round ?? []
    const options = [...editor.rounds]
    for (const key of picked) if (!options.some((o) => o.key === key)) options.push({ key, label: key })
    if (options.length === 0) {
      return (
        <ListText
          label="Round keys, comma separated"
          value={picked}
          placeholder="round:1, round:2"
          onChange={(round) => setWhen({ round })}
        />
      )
    }
    return (
      <div className="filter-chips" role="group" aria-label="Rounds">
        {options.map((o) => (
          <button
            key={o.key}
            type="button"
            className="filter-chip"
            aria-pressed={picked.includes(o.key)}
            onClick={() => setWhen({ round: picked.includes(o.key) ? picked.filter((k) => k !== o.key) : [...picked, o.key] })}
          >
            {o.label}
          </button>
        ))}
      </div>
    )
  }
  if (kind === 'game') {
    const picked = when.game ?? []
    return (
      <div className="filter-chips" role="group" aria-label="Games in the set">
        {GAMES.map((g) => (
          <button
            key={String(g)}
            type="button"
            className="filter-chip"
            aria-pressed={picked.includes(g)}
            onClick={() => setWhen({ game: picked.includes(g) ? picked.filter((x) => x !== g) : [...picked, g] })}
          >
            {g === 'last' ? 'Decider' : `Game ${g}`}
          </button>
        ))}
      </div>
    )
  }
  if (kind === 'title') {
    return (
      <ListText
        label="Words in the title, comma separated"
        value={when.title ?? []}
        placeholder="final, top 8"
        onChange={(title) => setWhen({ title })}
      />
    )
  }
  if (kind === 'on_stage') {
    return (
      <select
        className="input"
        aria-label="Stage"
        value={when.on_stage ? 'yes' : 'no'}
        onChange={(e) => setWhen({ on_stage: e.target.value === 'yes' })}
      >
        <option value="yes">On a stage</option>
        <option value="no">Not on a stage</option>
      </select>
    )
  }
  return (
    <div className="t-form__grid">
      <InstantField label="From" value={when.from ?? null} zone={zone} onChange={(from) => setWhen({ from })} />
      <InstantField label="Before" value={when.to ?? null} zone={zone} onChange={(to) => setWhen({ to })} />
    </div>
  )
}

/** A comma separated list, kept as typed until it changes the values. */
function ListText({
  label,
  value,
  placeholder,
  onChange,
}: {
  label: string
  value: string[]
  placeholder: string
  onChange: (v: string[]) => void
}) {
  const [text, setText] = useState(value.join(', '))
  return (
    <input
      className="input"
      aria-label={label}
      placeholder={placeholder}
      value={text}
      onChange={(e) => {
        setText(e.target.value)
        onChange(
          e.target.value
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean),
        )
      }}
    />
  )
}

function Outcome({ rule, editor, onChange }: { rule: Rule; editor: SeedEditor; onChange: (r: Rule) => void }) {
  const groupId = useId()
  const weighted = rule.then?.weighted != null
  const presets = editor.presets
  const firstPreset = presets[0]
  if (!firstPreset) {
    return <p className="sr-hint">This community has no presets for this randomizer yet. Add one under Seed presets.</p>
  }
  const entries = rule.then?.weighted ?? []
  const total = entries.reduce((n, e) => n + (e.weight || 0), 0)
  const setEntries = (next: typeof entries) => onChange({ ...rule, then: { weighted: next } })
  const unused = presets.find((p) => !entries.some((e) => e.preset === p.id)) ?? firstPreset

  return (
    <>
      <div className="segmented" role="radiogroup" aria-label="What it rolls">
        {(
          [
            [false, 'One preset'],
            [true, 'Weighted'],
          ] as const
        ).map(([value, label]) => (
          <span key={label}>
            <input
              type="radio"
              id={`${groupId}-${value}`}
              name={groupId}
              checked={weighted === value}
              onChange={() => {
                if (value) {
                  const first = rule.then?.preset ?? firstPreset.id
                  const second = presets.find((p) => p.id !== first)?.id ?? first
                  onChange({ ...rule, then: { weighted: [{ preset: first, weight: 50 }, { preset: second, weight: 50 }] } })
                } else onChange({ ...rule, then: { preset: entries[0]?.preset ?? firstPreset.id } })
              }}
            />
            <label htmlFor={`${groupId}-${value}`}>{label}</label>
          </span>
        ))}
      </div>
      {!weighted ? (
        <label className="field sr-gap">
          <span className="field__label">Preset</span>
          <select
            className="input"
            value={rule.then?.preset ?? ''}
            onChange={(e) => onChange({ ...rule, then: { preset: e.target.value ? Number(e.target.value) : null } })}
          >
            <option value="">Pick a preset</option>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <div className="sr-weights sr-gap">
          {entries.map((e, i) => {
            const pct = total > 0 ? Math.round((100 * (e.weight || 0)) / total) : 0
            return (
              <div key={i} className="sr-weight">
                <select
                  className="input"
                  aria-label={`Preset ${i + 1}`}
                  value={e.preset}
                  onChange={(ev) => setEntries(entries.map((x, j) => (j === i ? { ...x, preset: Number(ev.target.value) } : x)))}
                >
                  {presets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                <input
                  className="input sr-weight__n"
                  type="number"
                  min={0}
                  max={10000}
                  aria-label={`Weight for preset ${i + 1}`}
                  value={e.weight}
                  onChange={(ev) => setEntries(entries.map((x, j) => (j === i ? { ...x, weight: Math.max(0, Number(ev.target.value) || 0) } : x)))}
                />
                <span className="sr-bar" aria-hidden="true">
                  <span className="sr-bar__fill" style={{ width: `${pct}%` }} />
                </span>
                <span className="sr-weight__pct">{pct}%</span>
                <IconButton
                  icon="close"
                  label={`Remove preset ${i + 1}`}
                  disabled={entries.length <= 2}
                  onClick={() => setEntries(entries.filter((_, j) => j !== i))}
                />
              </div>
            )
          })}
          {entries.length < 16 && (
            <Button variant="ghost" size="sm" icon="add" onClick={() => setEntries([...entries, { preset: unused.id, weight: 10 }])}>
              Add preset
            </Button>
          )}
        </div>
      )}
    </>
  )
}

function Tweaks({ rule, onChange }: { rule: Rule; onChange: (r: Rule) => void }) {
  const [rows, setRows] = useState(() => Object.entries(rule.tweaks ?? {}).map(([key, v]) => ({ key, value: tweakText(v) })))
  const commit = (next: typeof rows) => {
    setRows(next)
    const tweaks: Record<string, unknown> = {}
    for (const r of next) if (r.key.trim()) tweaks[r.key.trim()] = parseTweak(r.value)
    onChange({ ...rule, tweaks })
  }
  return (
    <>
      {rows.length === 0 && <p className="sr-hint">Set a setting on top of the preset, such as item.swords to assured.</p>}
      {rows.map((r, i) => (
        <div key={i} className="sr-tweak">
          <input
            className="input"
            aria-label={`Setting ${i + 1}`}
            placeholder="item.swords"
            value={r.key}
            onChange={(e) => commit(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))}
          />
          <input
            className="input"
            aria-label={`Value for setting ${i + 1}`}
            placeholder="assured"
            value={r.value}
            onChange={(e) => commit(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
          />
          <IconButton icon="close" label={`Remove setting ${i + 1}`} onClick={() => commit(rows.filter((_, j) => j !== i))} />
        </div>
      ))}
      {rows.length < 24 && (
        <Button variant="ghost" size="sm" icon="add" onClick={() => setRows([...rows, { key: '', value: '' }])}>
          Add tweak
        </Button>
      )}
    </>
  )
}

type Facts = { round: string; game: string; best_of: string; title: string; on_stage: boolean }

const BLANK: Facts = { round: '', game: '', best_of: '', title: '', on_stage: false }

/** Tries the unsaved rules on a match without rolling, and says which rule fires. */
function PreviewPanel({
  rules,
  openId,
  editor,
  tournamentId,
}: {
  rules: Rule[]
  openId: string
  editor: SeedEditor
  tournamentId: number
}) {
  const [pick, setPick] = useState<string>(editor.matches[0] ? String(editor.matches[0].id) : 'custom')
  const [facts, setFacts] = useState<Facts>(BLANK)
  const [result, setResult] = useState<Preview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const body = JSON.stringify({ rules: rules.map(tidy), pick, facts })

  useEffect(() => {
    let live = true
    const t = window.setTimeout(() => {
      const req =
        pick === 'custom'
          ? {
              rules: rules.map(tidy),
              facts: {
                round: facts.round || null,
                game: facts.game ? Number(facts.game) : null,
                best_of: facts.best_of ? Number(facts.best_of) : null,
                title: facts.title || null,
                on_stage: facts.on_stage,
              },
            }
          : { rules: rules.map(tidy), match_id: Number(pick) }
      api
        .POST('/api/admin/tournaments/{tournament_id}/seed-rules/preview', {
          params: { path: { tournament_id: tournamentId } },
          body: req,
        })
        .then(unwrap)
        .then((r) => {
          if (!live) return
          setResult(r)
          setError(null)
        })
        .catch((err) => live && setError(message(err)))
    }, 350)
    return () => {
      live = false
      window.clearTimeout(t)
    }
  }, [body, tournamentId])

  const set = (patch: Partial<Facts>) => setFacts({ ...facts, ...patch })
  return (
    <div className="sr-preview">
      <label className="field">
        <span className="field__label">Try it on</span>
        <select className="input" value={pick} onChange={(e) => setPick(e.target.value)}>
          {editor.matches.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
          <option value="custom">A made-up match</option>
        </select>
      </label>
      {pick === 'custom' && (
        <div className="t-form__grid">
          <label className="field">
            <span className="field__label">Round</span>
            <select className="input" value={facts.round} onChange={(e) => set({ round: e.target.value })}>
              <option value="">No bracket round</option>
              {editor.rounds.map((r) => (
                <option key={r.key} value={r.key}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Game in set</span>
            <select className="input" value={facts.game} onChange={(e) => set({ game: e.target.value })}>
              <option value="">Not in a set</option>
              {[1, 2, 3, 4, 5].map((g) => (
                <option key={g} value={g}>
                  Game {g}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Best of</span>
            <select className="input" value={facts.best_of} onChange={(e) => set({ best_of: e.target.value })}>
              <option value="">One game</option>
              <option value="3">Best of 3</option>
              <option value="5">Best of 5</option>
            </select>
          </label>
          <label className="field">
            <span className="field__label">Title</span>
            <input className="input" value={facts.title} onChange={(e) => set({ title: e.target.value })} />
          </label>
          <label className="t-check">
            <input type="checkbox" checked={facts.on_stage} onChange={(e) => set({ on_stage: e.target.checked })} />
            On a stage
          </label>
        </div>
      )}
      <div className="sr-preview__out" role="status" aria-live="polite">
        {error ? (
          <span className="sr-error">{error}</span>
        ) : result ? (
          <PreviewText result={result} openId={openId} />
        ) : (
          'Working it out…'
        )}
      </div>
    </div>
  )
}

function PreviewText({ result, openId }: { result: Preview; openId: string }) {
  const rolls =
    result.outcomes.length > 1
      ? `Rolls ${result.outcomes.map((o) => `${o.name} ${o.percent}%`).join(', ')}`
      : result.outcomes[0]
        ? `Rolls ${result.outcomes[0].name}`
        : 'Rolls nothing: the tournament has no preset'
  const tweaks = Object.entries(result.tweaks ?? {})
  return (
    <>
      <strong>
        {result.source === 'default'
          ? 'No rule fits.'
          : result.rule_id === openId
            ? `This rule fires.`
            : `Rule ${result.rule_number}, ${result.rule_name}, fires first.`}
      </strong>{' '}
      {rolls}
      {tweaks.length > 0 && (
        <>
          , then sets{' '}
          {tweaks.map(([k, v], i) => (
            <span key={k}>
              {i > 0 && ', '}
              <code>
                {k}: {tweakText(v)}
              </code>
            </span>
          ))}
        </>
      )}
      .
    </>
  )
}
