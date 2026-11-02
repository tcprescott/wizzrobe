import { useState, type ReactNode } from 'react'
import { api, unwrap } from '../../api/client'
import { FormDialog } from '../../components'
import { SwitchRow } from '../kit'
import { AUDIENCES, KINDS, type QuestionRow, type Section, type SurveyFull } from './data'

function useRun() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function run(work: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await work()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

function Failure({ error }: { error: string | null }) {
  return error ? (
    <p className="form-error" role="alert">
      {error}
    </p>
  ) : null
}

/** New survey, from the starter questions by default. */
export function NewSurveyDialog({ onClose, onDone }: { onClose: () => void; onDone: (id: number) => void }) {
  const [title, setTitle] = useState('')
  const [template, setTemplate] = useState(true)
  const { busy, error, run } = useRun()
  const submit = () =>
    run(async () => {
      const s = await api.POST('/api/surveys', { body: { title, from_template: template, intro: '', allow_anonymous: true } }).then(unwrap)
      onDone(s.id)
    })
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New survey"
      primaryLabel="Create draft"
      busy={busy}
      busyLabel="Creating…"
      onPrimary={() => void submit()}
    >
      <form className="settings-stack" onSubmit={(e) => (e.preventDefault(), void submit())}>
        <label className="field">
          <span className="field__label">Title</span>
          <input className="input" maxLength={120} required value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <SwitchRow
          label="Start from the starter questions"
          hint="About the event, your tournaments, your shifts and crew. You can change any of it."
          checked={template}
          onChange={setTemplate}
        />
        <Failure error={error} />
      </form>
    </FormDialog>
  )
}

/** Title, intro, the after-send line and whether people may answer without their name. */
export function SurveyDetailsDialog({ survey, onClose, onDone }: { survey: SurveyFull; onClose: () => void; onDone: () => void }) {
  const [title, setTitle] = useState(survey.title)
  const [intro, setIntro] = useState(survey.intro ?? '')
  const [after, setAfter] = useState(survey.after_send ?? '')
  const [anon, setAnon] = useState(survey.allow_anonymous)
  const { busy, error, run } = useRun()
  const draft = survey.status === 'draft'
  const submit = () =>
    run(async () => {
      await api
        .PATCH('/api/surveys/{survey_id}', {
          params: { path: { survey_id: survey.id } },
          body: draft
            ? { title, intro, after_send: after, allow_anonymous: anon }
            : { title, intro, after_send: after },
        })
        .then(unwrap)
      onDone()
    })
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Survey details"
      primaryLabel="Save"
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void submit()}
    >
      <form className="settings-stack" onSubmit={(e) => (e.preventDefault(), void submit())}>
        <label className="field">
          <span className="field__label">Title</span>
          <input className="input" maxLength={120} required value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Intro</span>
          <textarea className="input" rows={3} maxLength={2000} value={intro} onChange={(e) => setIntro(e.target.value)} />
          <span className="field__hint">Shown above the parts.</span>
        </label>
        <label className="field">
          <span className="field__label">After sending</span>
          <textarea className="input" rows={2} maxLength={500} value={after} onChange={(e) => setAfter(e.target.value)} />
          <span className="field__hint">What people read once their answers are in. Blank shows a default line.</span>
        </label>
        <SwitchRow
          label="Let people answer without their name"
          hint={draft ? 'They tick a box before sending.' : "Fixed once it's sent."}
          checked={anon}
          disabled={!draft}
          onChange={setAnon}
        />
        <Failure error={error} />
      </form>
    </FormDialog>
  )
}

/** Add or edit a part (section): title, description and who gets it. */
export function PartDialog({
  surveyId,
  section,
  onClose,
  onDone,
}: {
  surveyId: number
  section: Section | null
  onClose: () => void
  onDone: (id: number) => void
}) {
  const [title, setTitle] = useState(section?.title ?? '')
  const [description, setDescription] = useState(section?.description ?? '')
  const [audience, setAudience] = useState(section?.audience ?? 'everyone')
  const { busy, error, run } = useRun()
  const submit = () =>
    run(async () => {
      const body = { title, description, audience: audience as Section['audience'] & string }
      const saved = section
        ? await api
            .PATCH('/api/surveys/sections/{section_id}', { params: { path: { section_id: section.id } }, body: body as never })
            .then(unwrap)
        : await api
            .POST('/api/surveys/{survey_id}/sections', { params: { path: { survey_id: surveyId } }, body: body as never })
            .then(unwrap)
      onDone(saved.id)
    })
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={section ? `Edit ${section.title}` : 'Add part'}
      primaryLabel={section ? 'Save' : 'Add part'}
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void submit()}
    >
      <form className="settings-stack" onSubmit={(e) => (e.preventDefault(), void submit())}>
        <label className="field">
          <span className="field__label">Title</span>
          <input className="input" maxLength={120} required value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Description</span>
          <textarea
            className="input"
            rows={2}
            maxLength={240}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field__label">Who gets it</span>
          <select className="input" value={audience} onChange={(e) => setAudience(e.target.value)}>
            {AUDIENCES.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
          <span className="field__hint">"Each…" parts repeat once per tournament or position.</span>
        </label>
        <Failure error={error} />
      </form>
    </FormDialog>
  )
}

const lines = (text: string) =>
  text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)

/** Add or edit a question. Who sees it (conditions) is kept as it is. */
export function QuestionDialog({
  sectionId,
  question,
  onClose,
  onDone,
}: {
  sectionId: number
  question: QuestionRow | null
  onClose: () => void
  onDone: () => void
}) {
  const [prompt, setPrompt] = useState(question?.prompt ?? '')
  const [kind, setKind] = useState(question?.kind ?? 'rating')
  const [options, setOptions] = useState((question?.options ?? []).join('\n'))
  const [required, setRequired] = useState(question?.required ?? false)
  const [other, setOther] = useState(question?.other_option ?? false)
  const [none, setNone] = useState(question?.none_option ?? '')
  const [min, setMin] = useState(question?.min_picks?.toString() ?? '')
  const [max, setMax] = useState(question?.max_picks?.toString() ?? '')
  const [long, setLong] = useState(question?.long_answer ?? false)
  const { busy, error, run } = useRun()
  const choice = kind === 'choice_one' || kind === 'choice_many'
  const many = kind === 'choice_many'
  const listed = choice || kind === 'agree_scale'

  const submit = () =>
    run(async () => {
      const body = {
        prompt,
        kind,
        required,
        options: listed ? lines(options) : null,
        other_option: choice && other,
        none_option: many && none.trim() ? none.trim() : question ? '' : null,
        min_picks: many && min ? Number(min) : question ? 0 : null,
        max_picks: many && max ? Number(max) : question ? 0 : null,
        long_answer: kind === 'comment' && long,
      }
      if (question)
        await api
          .PATCH('/api/surveys/questions/{question_id}', {
            params: { path: { question_id: question.id } },
            body: body as never,
          })
          .then(unwrap)
      else
        await api
          .POST('/api/surveys/sections/{section_id}/questions', {
            params: { path: { section_id: sectionId } },
            body: body as never,
          })
          .then(unwrap)
      onDone()
    })

  let extra: ReactNode = null
  if (listed)
    extra = (
      <label className="field">
        <span className="field__label">{kind === 'agree_scale' ? 'Statements' : 'Choices'}</span>
        <textarea className="input" rows={5} value={options} onChange={(e) => setOptions(e.target.value)} />
        <span className="field__hint">One per line, 2 to 10.</span>
      </label>
    )
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={question ? 'Edit question' : 'Add question'}
      primaryLabel={question ? 'Save' : 'Add question'}
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void submit()}
    >
      <form className="settings-stack" onSubmit={(e) => (e.preventDefault(), void submit())}>
        <label className="field">
          <span className="field__label">Question</span>
          <textarea className="input" rows={2} maxLength={280} required value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Kind</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        {extra}
        {choice && <SwitchRow label='Add "Other" with a write-in' checked={other} onChange={setOther} />}
        {many && (
          <>
            <label className="field">
              <span className="field__label">"None" choice</span>
              <input className="input" maxLength={60} value={none} placeholder="None of these" onChange={(e) => setNone(e.target.value)} />
              <span className="field__hint">Listed last, and can't be picked with anything else. Leave blank for none.</span>
            </label>
            <div className="field-grid">
              <label className="field">
                <span className="field__label">Fewest picks</span>
                <input className="input" type="number" min={1} value={min} onChange={(e) => setMin(e.target.value)} />
              </label>
              <label className="field">
                <span className="field__label">Most picks</span>
                <input className="input" type="number" min={1} value={max} onChange={(e) => setMax(e.target.value)} />
              </label>
            </div>
          </>
        )}
        {kind === 'comment' && <SwitchRow label="Long answer" hint="A taller box, up to 3000 characters." checked={long} onChange={setLong} />}
        <SwitchRow label="Required" checked={required} onChange={setRequired} />
        {question?.conditions?.length ? (
          <p className="settings-note">Who sees it stays as set. Change that on the website for now.</p>
        ) : null}
        <Failure error={error} />
      </form>
    </FormDialog>
  )
}

/** "Are you sure" for anything that DMs people or can't be undone. */
export function ConfirmDialog({
  title,
  children,
  label,
  busyLabel,
  destructive,
  action,
  onClose,
  extra,
}: {
  title: string
  children: ReactNode
  label: string
  busyLabel: string
  destructive?: boolean
  action: () => Promise<void>
  onClose: () => void
  extra?: ReactNode
}) {
  const { busy, error, run } = useRun()
  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(o) => !o && onClose()}
      title={title}
      primaryLabel={label}
      busy={busy}
      busyLabel={busyLabel}
      destructive={destructive}
      onPrimary={() => void run(action)}
    >
      <div className="settings-stack">
        <p>{children}</p>
        {extra}
        <Failure error={error} />
      </div>
    </FormDialog>
  )
}
