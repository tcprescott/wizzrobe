import { useEffect, useId, useState } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, Chip, ConfirmDialog, EmptyState, IconButton, RowMenu, Spinner, type MenuItem } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { message } from '../../me/data'
import { Detail, DetailEmpty, DetailSection, SideListCard, SideListLayout, SideListRow } from '../kit'
import { useAdminQuery, type BoardRow } from './data'
import { newRuleId, type PresetRef } from './seedRules'
import './seed-rules.css'

type Question = Schemas['MatchFormQuestionOut']
type Kind = Question['kind']
type Note = { tone: 'info' | 'error'; text: string }

const KIND_LABEL: Record<Kind, string> = { yes_no: 'Yes / No', choice_one: 'Pick one', choice_many: 'Pick several', number: 'Number' }
const WHO_LABEL: Record<Question['who'], string> = {
  each_player: 'Each player',
  higher_seed: 'The higher seed',
  previous_loser: 'Loser of the previous game',
}
const SEES_LABEL: Record<Question['visibility'], string> = { right_away: 'Right away', at_roll: 'When the seed rolls', never: 'Never' }
const MAX_QUESTIONS = 12

/** What a question is, in a line: "Yes / No · each player · hidden". */
function summary(q: Question): string {
  const kind = q.kind === 'choice_one' || q.kind === 'choice_many' ? `${KIND_LABEL[q.kind]} of ${q.choices.length}` : KIND_LABEL[q.kind]
  const sees = q.visibility === 'never' ? 'hidden' : q.visibility === 'at_roll' ? 'hidden until the seed rolls' : 'opponent sees it'
  return `${kind} · ${WHO_LABEL[q.who].toLowerCase()} · ${sees}`
}

function blank(taken: Set<string>): Question {
  return {
    key: newRuleId(taken),
    kind: 'yes_no',
    prompt: '',
    help: null,
    choices: [
      { value: 'yes', label: 'Yes' },
      { value: 'no', label: 'No' },
    ],
    config: {},
    who: 'each_player',
    ask_on: 'every_match',
    visibility: 'right_away',
    used_by: [],
  }
}

/** Sends only what the server reads, so comparing to the saved form ignores display fields. */
function tidy(q: Question): Schemas['MatchFormQuestionIn'] {
  const config: Record<string, number> = q.kind === 'number' ? { min: q.config.min ?? 0, max: q.config.max ?? 10 } : {}
  return {
    key: q.key,
    kind: q.kind,
    prompt: q.prompt,
    help: q.help || null,
    choices: q.kind === 'number' ? [] : q.choices.map((c) => ({ value: c.value ?? null, label: c.label, preset: c.preset ?? null })),
    config,
    who: q.who,
    ask_on: q.ask_on,
    visibility: q.visibility,
  }
}

/** Questions players answer before a match; seed rules read the answers. */
export function MatchFormTab({ row, base }: { row: BoardRow; base: string }) {
  const form = useAdminQuery(`tournaments/${row.id}/match-form`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/match-form', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const rules = useAdminQuery(`tournaments/${row.id}/seed-rules`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/seed-rules', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const openKey = useParams()['*'] || null
  const phone = usePhone()
  const navigate = useNavigate()
  const [draft, setDraft] = useState<Question[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<Note | null>(null)
  const [deleting, setDeleting] = useState<Question | null>(null)

  useEffect(() => {
    if (form.data && draft === null) setDraft(form.data.questions)
  }, [form.data, draft])

  if (form.error && !form.data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={form.reload}>Try again</Button>}>
        {form.error.message}
      </Banner>
    )
  }
  if (!form.data || draft === null) return <Spinner label="Loading the match form" />
  const saved = form.data.questions
  const questions = draft
  const dirty = JSON.stringify(questions.map(tidy)) !== JSON.stringify(saved.map(tidy))
  const index = questions.findIndex((q) => q.key === openKey)
  const open = index >= 0 ? questions[index] : null
  if (!phone && openKey == null && questions[0]) return <Navigate to={`${base}/${questions[0].key}`} replace />

  const update = (q: Question) => setDraft(questions.map((x) => (x.key === q.key ? q : x)))
  const move = (i: number, by: number) => {
    const next = [...questions]
    const [q] = next.splice(i, 1)
    if (q) next.splice(i + by, 0, q)
    setDraft(next)
  }

  function add() {
    const q = blank(new Set(questions.map((x) => x.key)))
    setDraft([...questions, q])
    navigate(`${base}/${q.key}`)
  }

  async function save() {
    setBusy(true)
    setNote(null)
    try {
      const out = await api
        .PUT('/api/admin/tournaments/{tournament_id}/match-form', {
          params: { path: { tournament_id: row.id } },
          body: { questions: questions.map(tidy) },
        })
        .then(unwrap)
      setDraft(out.questions)
      setNote({ tone: 'info', text: 'Saved. Players see these questions on their next match.' })
      form.reload()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  const menu = (i: number, q: Question): MenuItem[] => [
    { label: 'Move up', icon: 'arrow_upward', disabled: i === 0, onSelect: () => move(i, -1) },
    { label: 'Move down', icon: 'arrow_downward', disabled: i === questions.length - 1, onSelect: () => move(i, 1) },
    {
      label: 'Delete question…',
      icon: 'delete',
      destructive: true,
      disabled: q.used_by.length > 0,
      onSelect: () => setDeleting(q),
    },
  ]

  const list = (
    <div className="side-list__col">
      <SideListCard label="Match form questions">
        {questions.map((q, i) => (
          <SideListRow
            key={q.key}
            to={`${base}/${q.key}`}
            leading={<span className="sr-num">{i + 1}</span>}
            title={q.prompt || `Question ${i + 1}`}
            sub={
              <span className="sr-summary">
                <span>{summary(q)}</span>
                <span>
                  {q.used_by.length ? (
                    <Chip tone="neutral">Used by rule {q.used_by.join(', ')}</Chip>
                  ) : (
                    <Chip tone="pending">Not used by a rule</Chip>
                  )}
                </span>
              </span>
            }
          />
        ))}
      </SideListCard>
    </div>
  )

  const detail = open ? (
    <QuestionEditor
      key={open.key}
      question={open}
      index={index}
      presets={rules.data?.presets ?? []}
      onChange={update}
      menu={menu(index, open)}
    />
  ) : openKey ? (
    <EmptyState icon="info" title="That question isn't here">
      It may have been deleted. Pick another from the list.
    </EmptyState>
  ) : (
    <DetailEmpty>
      <EmptyState icon="checklist" title="No questions yet">
        Ask players something before each match, such as whether they want Hard mode, and let a seed rule read the answers.
      </EmptyState>
    </DetailEmpty>
  )

  return (
    <div className="sr-tab">
      {!(phone && open) && (
        <div className="sr-toolbar">
          <p className="sr-lede">Questions players answer before a match. Seed rules read the answers.</p>
          <div className="sr-toolbar__actions">
            <Button icon="add" onClick={add} disabled={questions.length >= MAX_QUESTIONS}>
              Add question
            </Button>
          </div>
        </div>
      )}
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <SideListLayout list={list} detail={detail} open={openKey != null} backTo={base} backLabel="Match form" />
      {dirty && (
        <div className="sr-savebar" role="region" aria-label="Unsaved match form">
          <span>Unsaved changes to the match form.</span>
          <span className="dialog__spacer" />
          <Button variant="ghost" disabled={busy} onClick={() => setDraft(saved)}>
            Undo changes
          </Button>
          <Button onClick={() => void save()} busy={busy} busyLabel="Saving…">
            Save form
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.prompt || 'this question'}”?`}
        action="Delete question"
        onConfirm={async () => {
          if (deleting) {
            setDraft(questions.filter((q) => q.key !== deleting.key))
            navigate(base)
          }
          return true
        }}
      >
        Its answers go with it once you save.
      </ConfirmDialog>
    </div>
  )
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: [T, string][]
  onChange: (v: T) => void
}) {
  const id = useId()
  return (
    <div className="field">
      <span className="field__label" id={`${id}-l`}>
        {label}
      </span>
      <div className="segmented" role="radiogroup" aria-labelledby={`${id}-l`}>
        {options.map(([v, text]) => (
          <span key={v}>
            <input type="radio" id={`${id}-${v}`} name={id} checked={value === v} onChange={() => onChange(v)} />
            <label htmlFor={`${id}-${v}`}>{text}</label>
          </span>
        ))}
      </div>
    </div>
  )
}

function QuestionEditor({
  question: q,
  index,
  presets,
  onChange,
  menu,
}: {
  question: Question
  index: number
  presets: PresetRef[]
  onChange: (q: Question) => void
  menu: MenuItem[]
}) {
  const promptId = useId()
  const helpId = useId()
  const set = (patch: Partial<Question>) => onChange({ ...q, ...patch })
  const setKind = (kind: Kind) => {
    if (kind === 'yes_no') set({ kind, choices: [{ value: 'yes', label: 'Yes' }, { value: 'no', label: 'No' }] })
    else if (kind === 'number') set({ kind, config: { min: q.config.min ?? 0, max: q.config.max ?? 10 } })
    else if (q.kind === 'yes_no' || q.kind === 'number') set({ kind, choices: [{ label: '' }, { label: '' }] })
    else set({ kind })
  }
  const setChoice = (i: number, patch: Partial<Question['choices'][number]>) =>
    set({ choices: q.choices.map((c, j) => (j === i ? { ...c, ...patch } : c)) })

  return (
    <Detail
      title={`Question ${index + 1}`}
      sub={q.prompt || 'No question yet'}
      actions={<RowMenu label={`More for question ${index + 1}`} items={menu} />}
    >
      <DetailSection>
        <label className="field" htmlFor={promptId}>
          <span className="field__label">Question</span>
          <input id={promptId} className="input" maxLength={200} value={q.prompt} placeholder="Play Hard mode?" onChange={(e) => set({ prompt: e.target.value })} />
        </label>
        <label className="field sr-gap" htmlFor={helpId}>
          <span className="field__label">Help text</span>
          <input
            id={helpId}
            className="input"
            maxLength={500}
            value={q.help ?? ''}
            placeholder="Only if you both say yes."
            onChange={(e) => set({ help: e.target.value })}
          />
        </label>
      </DetailSection>

      <DetailSection>
        <Segmented<Kind> label="Answer type" value={q.kind} options={Object.entries(KIND_LABEL) as [Kind, string][]} onChange={setKind} />
        {q.kind === 'number' ? (
          <div className="t-form__grid sr-gap">
            {(['min', 'max'] as const).map((k) => (
              <label key={k} className="field">
                <span className="field__label">{k === 'min' ? 'Lowest' : 'Highest'}</span>
                <input
                  className="input"
                  type="number"
                  value={q.config[k] ?? (k === 'min' ? 0 : 10)}
                  onChange={(e) => set({ config: { ...q.config, [k]: Math.trunc(Number(e.target.value) || 0) } })}
                />
              </label>
            ))}
          </div>
        ) : (
          <div className="mf-choices sr-gap">
            <span className="field__label">Choices</span>
            {q.choices.map((c, i) => (
              <div key={i} className="mf-choice">
                <input
                  className="input"
                  aria-label={`Choice ${i + 1}`}
                  maxLength={80}
                  value={c.label}
                  readOnly={q.kind === 'yes_no'}
                  onChange={(e) => setChoice(i, { label: e.target.value })}
                />
                <select
                  className="input"
                  aria-label={`Preset for choice ${i + 1}`}
                  value={c.preset ?? ''}
                  onChange={(e) => setChoice(i, { preset: e.target.value ? Number(e.target.value) : null })}
                >
                  <option value="">No preset</option>
                  {presets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                {q.kind !== 'yes_no' && (
                  <IconButton
                    icon="close"
                    label={`Remove choice ${i + 1}`}
                    disabled={q.choices.length <= 2}
                    onClick={() => set({ choices: q.choices.filter((_, j) => j !== i) })}
                  />
                )}
              </div>
            ))}
            {q.kind !== 'yes_no' && q.choices.length < 16 && (
              <Button variant="ghost" size="sm" icon="add" onClick={() => set({ choices: [...q.choices, { label: '' }] })}>
                Add choice
              </Button>
            )}
            <p className="sr-hint">Link a choice to a preset so a rule can roll “the players' answer”.</p>
          </div>
        )}
      </DetailSection>

      <DetailSection>
        <label className="field">
          <span className="field__label">Who answers</span>
          <select className="input" value={q.who} onChange={(e) => set({ who: e.target.value as Question['who'] })}>
            {Object.entries(WHO_LABEL).map(([v, text]) => (
              <option key={v} value={v}>
                {text}
              </option>
            ))}
          </select>
        </label>
        {q.who === 'higher_seed' && <p className="sr-hint">Needs seeds from this tournament's own bracket. Without them, nobody is asked.</p>}
        <label className="field sr-gap">
          <span className="field__label">Ask on</span>
          <select className="input" value={q.ask_on} onChange={(e) => set({ ask_on: e.target.value as Question['ask_on'] })}>
            <option value="every_match">Every match</option>
            <option value="when_used">Only matches where a rule reading it could fit</option>
          </select>
        </label>
        <div className="sr-gap">
          <Segmented<Question['visibility']>
            label="Opponent sees the answer"
            value={q.visibility}
            options={Object.entries(SEES_LABEL) as [Question['visibility'], string][]}
            onChange={(visibility) => set({ visibility })}
          />
        </div>
        <p className="sr-hint">Answers lock when the seed rolls. If nobody answers, the rule doesn't fire and the next one decides.</p>
      </DetailSection>
    </Detail>
  )
}
