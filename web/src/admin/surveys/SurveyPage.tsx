import { useState } from 'react'
import { Link, Navigate, NavLink, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { ActionsMenu, Button, Chip, EmptyState, Icon, Spinner, type MenuItem } from '../../components'
import { LoadError, useAdmin, useAdminQuery } from '../kit'
import { BuildTab } from './BuildTab'
import { CommentsTab } from './CommentsTab'
import { statusOf, plural, type SurveyFull, type SurveyRow } from './data'
import { ConfirmDialog, SurveyDetailsDialog } from './dialogs'
import { PreviewTab } from './PreviewTab'
import { ResultsTab } from './ResultsTab'
import { SendTab } from './SendTab'

export interface SurveyCtx {
  survey: SurveyFull
  row: SurveyRow | undefined
  base: string
  reload: () => void
}

function statusLine(survey: SurveyFull, row: SurveyRow | undefined): string {
  const parts = plural(survey.sections?.length ?? 0, 'part')
  if (survey.status === 'draft') return `${parts} · nothing sent yet`
  return [parts, row ? `${row.answered} of ${row.recipients} answered` : ''].filter(Boolean).join(' · ')
}

/** One survey at full width: its tabs, its main action and the Actions menu. */
export function SurveyPage() {
  const { survey: param } = useParams()
  const id = Number(param)
  const { adminBase } = useAdmin()
  const navigate = useNavigate()
  const list = `${adminBase}/surveys`
  const base = `${list}/${id}`
  const path = { survey_id: id }
  const { data, error, reload } = useAdminQuery(`surveys/${id}`, () =>
    api.GET('/api/surveys/{survey_id}', { params: { path } }).then(unwrap),
  )
  const rows = useAdminQuery('surveys', () => api.GET('/api/surveys').then(unwrap))
  const [dialog, setDialog] = useState<'details' | 'delete' | 'close' | null>(null)
  const [note, setNote] = useState<string | null>(null)

  if (error && !data)
    return error.message.includes('404') || error.message.toLowerCase().includes('not found') ? (
      <EmptyState icon="rate_review" title="That survey isn't here">
        It may have been deleted. <Link to={list}>Back to surveys</Link>
      </EmptyState>
    ) : (
      <LoadError error={error} onRetry={reload} />
    )
  if (!data) return <Spinner label="Loading the survey" />
  const survey = data
  const row = rows.data?.find((r) => r.id === id)
  const refresh = () => {
    reload()
    rows.reload()
  }
  const ctx: SurveyCtx = { survey, row, base, reload: refresh }
  const st = statusOf(survey.status)

  const menu: MenuItem[] = [
    { label: 'Preview', icon: 'visibility', onSelect: () => navigate(`${base}/preview`) },
    { label: 'Survey details…', icon: 'edit', onSelect: () => setDialog('details') },
    {
      label: 'Duplicate',
      icon: 'content_copy',
      onSelect: () =>
        void api
          .POST('/api/surveys/{survey_id}/duplicate', { params: { path } })
          .then(unwrap)
          .then((copy) => {
            rows.reload()
            navigate(`${list}/${copy.id}/build`)
          })
          .catch((err: Error) => setNote(err.message)),
    },
  ]
  if (survey.status === 'open') menu.push({ label: 'Close survey…', icon: 'lock', destructive: true, onSelect: () => setDialog('close') })
  if (survey.status === 'draft') menu.push({ label: 'Delete survey…', icon: 'delete', destructive: true, onSelect: () => setDialog('delete') })

  const main =
    survey.status === 'draft' ? (
      <Button variant="primary" icon="send" onClick={() => navigate(`${base}/send`)}>
        Send…
      </Button>
    ) : survey.status === 'open' ? (
      <Button variant="outline" icon="notifications" onClick={() => navigate(`${base}/send`)}>
        Remind…
      </Button>
    ) : null

  const tabs = [
    { slug: 'build', label: 'Build' },
    { slug: 'send', label: 'Send' },
    { slug: 'results', label: 'Results' },
    { slug: 'comments', label: 'Comments' },
  ]

  return (
    <div className="sv-admin">
      <Link to={list} className="admin-back sv-admin-back">
        <Icon name="chevron_left" />
        Surveys
      </Link>
      <header className="page-header">
        <div className="page-header__text">
          <h1>{survey.title}</h1>
          <p className="page-header__lede">
            <Chip tone={st.tone}>{st.label}</Chip> {statusLine(survey, row)}
          </p>
        </div>
        <div className="page-header__actions">
          <ActionsMenu items={menu} />
          {main}
        </div>
      </header>
      {note && (
        <p className="form-error" role="alert">
          {note}
        </p>
      )}
      <nav className="detail-tabs" aria-label={`${survey.title} sections`}>
        {tabs.map((t) => (
          <NavLink key={t.slug} to={`${base}/${t.slug}`}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to={survey.status === 'draft' ? 'build' : 'results'} replace />} />
        <Route path="build" element={<BuildTab {...ctx} />} />
        <Route path="build/:section" element={<BuildTab {...ctx} />} />
        <Route path="send" element={<SendTab {...ctx} />} />
        <Route path="results" element={<ResultsTab {...ctx} />} />
        <Route path="comments" element={<CommentsTab {...ctx} />} />
        <Route path="comments/:question" element={<CommentsTab {...ctx} />} />
        <Route path="preview" element={<PreviewTab {...ctx} />} />
        <Route path="preview/:part" element={<PreviewTab {...ctx} />} />
        <Route path="*" element={<Navigate to="build" replace />} />
      </Routes>
      {dialog === 'details' && (
        <SurveyDetailsDialog
          survey={survey}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null)
            refresh()
          }}
        />
      )}
      {dialog === 'delete' && (
        <ConfirmDialog
          title={`Delete ${survey.title}?`}
          label="Delete survey"
          busyLabel="Deleting…"
          destructive
          onClose={() => setDialog(null)}
          action={async () => {
            await api.DELETE('/api/surveys/{survey_id}', { params: { path } }).then(unwrap)
            rows.reload()
            navigate(list, { replace: true })
          }}
        >
          The draft and all its parts and questions go for good. Nothing was sent, so nobody is told.
        </ConfirmDialog>
      )}
      {dialog === 'close' && (
        <ConfirmDialog
          title={`Close ${survey.title}?`}
          label="Close survey"
          busyLabel="Closing…"
          destructive
          onClose={() => setDialog(null)}
          action={async () => {
            await api.POST('/api/surveys/{survey_id}/close', { params: { path } }).then(unwrap)
            setDialog(null)
            refresh()
          }}
        >
          It stops taking answers now. Anything people saved but didn't send stays unsent.
        </ConfirmDialog>
      )}
    </div>
  )
}
