import { useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, EmptyState, Icon, RowMenu, type IconName, type MenuItem } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { DetailEmpty, SideListCard, SideListLayout, SideListRow, useAdminQuery } from '../kit'
import { audienceLabel, kindOf, mins, plural, questionLine, type Builder, type QuestionRow, type Section } from './data'
import { ConfirmDialog, PartDialog, QuestionDialog } from './dialogs'
import type { SurveyCtx } from './SurveyPage'

type Batch = Schemas['SuggestionBatchResponse']
type Editing =
  | { kind: 'part'; section: Section | null }
  | { kind: 'question'; section: Section; question: QuestionRow | null }
  | { kind: 'delete-part'; section: Section }
  | { kind: 'delete-question'; question: QuestionRow }

function TimeBox({ b }: { b: Builder }) {
  const scale = Math.max(b.budget_seconds, b.longest_seconds, 1)
  const pct = (s: number) => `${Math.min(100, (s / scale) * 100)}%`
  return (
    <div className="sv-timebox">
      <p className="sv-timebox__head">Most people: {mins(b.typical_seconds)}</p>
      <p className="sv-timebox__sub">
        Longest path {mins(b.longest_seconds)} ({b.longest_who}). Aim for under {mins(b.target_seconds)}.
      </p>
      <div
        className="sv-timebox__bar"
        role="img"
        aria-label={`Most people ${mins(b.typical_seconds)}, longest ${mins(b.longest_seconds)}, target ${mins(b.target_seconds)}`}
      >
        <span className="sv-timebox__long" style={{ width: pct(b.longest_seconds) }} />
        <span className="sv-timebox__typical" style={{ width: pct(b.typical_seconds) }} />
        <span className="sv-timebox__target" style={{ left: pct(b.target_seconds) }} />
      </div>
    </div>
  )
}

function Checks({ b }: { b: Builder }) {
  const checks = b.checks ?? []
  if (!checks.length) return <p className="sv-checks sv-checks--ok">{plural(b.checks_passed?.length ?? 0, 'check')} passed.</p>
  return (
    <details className="sv-checks">
      <summary>{plural(checks.length, 'thing')} to look at</summary>
      <ul>
        {checks.map((c, i) => (
          <li key={i}>
            {c.link && <strong>{c.link}</strong>} {c.text}
          </li>
        ))}
      </ul>
    </details>
  )
}

function Suggestions({ batch, onChange }: { batch: Batch; onChange: () => void }) {
  const [busy, setBusy] = useState<number | 'dismiss' | null>(null)
  const [error, setError] = useState<string | null>(null)
  async function act(which: number | 'dismiss') {
    setBusy(which)
    setError(null)
    try {
      if (which === 'dismiss')
        await api.POST('/api/surveys/suggestions/{batch_id}/dismiss', { params: { path: { batch_id: batch.id } } }).then(unwrap)
      else
        await api
          .POST('/api/surveys/suggestions/{batch_id}/keep', { params: { path: { batch_id: batch.id } }, body: { index: which } })
          .then(unwrap)
      onChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }
  return (
    <div className="sv-suggest" aria-label="Suggested questions">
      {batch.drafts.map((d) => (
        <div key={d.index} className="sv-qrow sv-qrow--suggested">
          <Icon name="wand_stars" />
          <div className="sv-qrow__text">
            <span className="sv-qrow__title">{d.prompt}</span>
            <span className="sv-qrow__sub">Suggested · {kindOf(d.kind).label}</span>
          </div>
          <Button size="sm" variant="outline" busy={busy === d.index} onClick={() => void act(d.index)}>
            Keep
          </Button>
        </div>
      ))}
      <div className="sv-suggest__foot">
        <Button size="sm" variant="ghost" busy={busy === 'dismiss'} onClick={() => void act('dismiss')}>
          Dismiss the rest
        </Button>
        {batch.model && <span className="sv-hint">From {batch.model}</span>}
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

/** Build: the time box and parts in a side list beside the open part's questions. */
export function BuildTab({ survey, base, reload }: SurveyCtx) {
  const { section: param } = useParams()
  const phone = usePhone()
  const draft = survey.status === 'draft'
  const builder = useAdminQuery(`surveys/${survey.id}/builder`, () =>
    api.GET('/api/surveys/{survey_id}/builder', { params: { path: { survey_id: survey.id } } }).then(unwrap),
  )
  const [editing, setEditing] = useState<Editing | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const sections = survey.sections ?? []
  const section = sections.find((s) => String(s.id) === param) ?? null
  const buildBase = `${base}/build`
  const first = sections[0]
  if (!param && !phone && first) return <Navigate to={`${buildBase}/${first.id}`} replace />

  const refresh = () => {
    reload()
    builder.reload()
  }

  async function act(work: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await work()
      refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  const moveSection = (s: Section, step: number) =>
    act(() => api.POST('/api/surveys/sections/{section_id}/move', { params: { path: { section_id: s.id } }, body: { step } }).then(unwrap))
  const moveQuestion = (q: QuestionRow, step: number) =>
    act(() =>
      api.POST('/api/surveys/questions/{question_id}/move', { params: { path: { question_id: q.id } }, body: { step } }).then(unwrap),
    )
  const suggest = (s: Section) =>
    act(() => api.POST('/api/surveys/sections/{section_id}/suggestions', { params: { path: { section_id: s.id } } }).then(unwrap))

  const b = builder.data
  const batch = section ? b?.suggestions?.find((x) => x.section_id === section.id) : undefined

  const list = (
    <div className="side-list__col">
      {b && <TimeBox b={b} />}
      <SideListCard label="Parts">
        {sections.map((s) => (
          <SideListRow
            key={s.id}
            to={`${buildBase}/${s.id}`}
            leading={<Icon name="table_rows" />}
            title={s.title}
            sub={`${audienceLabel(s.audience)} · ${plural(s.questions?.length ?? 0, 'question')}`}
          />
        ))}
      </SideListCard>
      {draft && (
        <Button variant="outline" icon="add" onClick={() => setEditing({ kind: 'part', section: null })}>
          Add part
        </Button>
      )}
      {b && <Checks b={b} />}
    </div>
  )

  let detail = null
  if (section) {
    const idx = sections.indexOf(section)
    const partMenu: MenuItem[] = [
      { label: 'Edit part…', icon: 'edit', onSelect: () => setEditing({ kind: 'part', section }) },
      { label: 'Move up', icon: 'arrow_upward', disabled: idx === 0, onSelect: () => void moveSection(section, -1) },
      { label: 'Move down', icon: 'arrow_downward', disabled: idx === sections.length - 1, onSelect: () => void moveSection(section, 1) },
      { label: 'Delete part…', icon: 'delete', destructive: true, onSelect: () => setEditing({ kind: 'delete-part', section }) },
    ]
    const qs = section.questions ?? []
    detail = (
      <section className="sv-part" aria-labelledby="sv-build-part">
        <header className="sv-part__head">
          <div>
            <h2 id="sv-build-part">{section.title}</h2>
            {section.description && <p className="sv-part__desc">{section.description}</p>}
          </div>
          <span className="sv-part__count">
            {audienceLabel(section.audience)} · {plural(qs.length, 'question')}
          </span>
          {draft && <RowMenu label={`More actions for ${section.title}`} items={partMenu} />}
        </header>
        {qs.length === 0 && <p className="sv-empty">No questions in this part yet.</p>}
        <ul className="sv-qlist" aria-label={`Questions in ${section.title}`}>
          {qs.map((q, i) => (
            <li key={q.id} className="sv-qrow">
              <Icon name={kindOf(q.kind).icon as IconName} />
              <div className="sv-qrow__text">
                <span className="sv-qrow__title">{q.prompt}</span>
                <span className="sv-qrow__sub">{questionLine(q)}</span>
              </div>
              {draft && (
                <RowMenu
                  label={`More actions for ${q.prompt}`}
                  items={[
                    { label: 'Edit…', icon: 'edit', onSelect: () => setEditing({ kind: 'question', section, question: q }) },
                    { label: 'Move up', icon: 'arrow_upward', disabled: i === 0, onSelect: () => void moveQuestion(q, -1) },
                    { label: 'Move down', icon: 'arrow_downward', disabled: i === qs.length - 1, onSelect: () => void moveQuestion(q, 1) },
                    { label: 'Delete…', icon: 'delete', destructive: true, onSelect: () => setEditing({ kind: 'delete-question', question: q }) },
                  ]}
                />
              )}
            </li>
          ))}
        </ul>
        {batch && draft && <Suggestions batch={batch} onChange={refresh} />}
        {draft && (
          <footer className="sv-part__foot sv-part__foot--start">
            <Button variant="outline" icon="add" onClick={() => setEditing({ kind: 'question', section, question: null })}>
              Add question
            </Button>
            {b?.ai_provider && (
              <Button variant="ghost" icon="wand_stars" busy={busy} busyLabel="Asking…" onClick={() => void suggest(section)}>
                Suggest questions
              </Button>
            )}
          </footer>
        )}
      </section>
    )
  } else if (param) {
    detail = (
      <EmptyState icon="rate_review" title="That part isn't here">
        It may have been deleted.
      </EmptyState>
    )
  } else if (!sections.length) {
    detail = <DetailEmpty>Add a part to start.</DetailEmpty>
  }

  return (
    <>
      {!draft && (
        <Banner tone="info">
          {survey.status === 'open' ? "It's been sent, so" : "It's closed, so"} its parts and questions are fixed. Wording fixes stay on the website for now.
        </Banner>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <SideListLayout list={list} detail={detail} open={Boolean(param)} backTo={buildBase} backLabel="Parts" />
      {editing?.kind === 'part' && (
        <PartDialog
          surveyId={survey.id}
          section={editing.section}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null)
            refresh()
          }}
        />
      )}
      {editing?.kind === 'question' && (
        <QuestionDialog
          sectionId={editing.section.id}
          question={editing.question}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null)
            refresh()
          }}
        />
      )}
      {editing?.kind === 'delete-part' && (
        <ConfirmDialog
          title={`Delete ${editing.section.title}?`}
          label="Delete part"
          busyLabel="Deleting…"
          destructive
          onClose={() => setEditing(null)}
          action={async () => {
            await api.DELETE('/api/surveys/sections/{section_id}', { params: { path: { section_id: editing.section.id } } }).then(unwrap)
            setEditing(null)
            refresh()
          }}
        >
          Its {plural(editing.section.questions?.length ?? 0, 'question')} go with it.
        </ConfirmDialog>
      )}
      {editing?.kind === 'delete-question' && (
        <ConfirmDialog
          title="Delete this question?"
          label="Delete question"
          busyLabel="Deleting…"
          destructive
          onClose={() => setEditing(null)}
          action={async () => {
            await api.DELETE('/api/surveys/questions/{question_id}', { params: { path: { question_id: editing.question.id } } }).then(unwrap)
            setEditing(null)
            refresh()
          }}
        >
          “{editing.question.prompt}” goes for good.
        </ConfirmDialog>
      )}
    </>
  )
}
