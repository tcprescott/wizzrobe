import { useState } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { Button, Chip, CountBadge, EmptyState, RowMenu, Spinner, type MenuItem } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { LoadError, SideListCard, SideListLayout, SideListRow, useAdminQuery } from '../kit'
import type { SurveyCtx } from './SurveyPage'

type Inbox = Schemas['SurveyCommentsResponse']
type Comment = Schemas['SurveyCommentResponse']

function CommentCard({
  c,
  tags,
  onMark,
}: {
  c: Comment
  tags: Inbox['tags']
  onMark: (body: { read?: boolean; tags?: number[] }) => void
}) {
  const own = c.tags ?? []
  const items: MenuItem[] = (tags ?? []).map((t) => ({
    label: own.includes(t.id) ? `Remove “${t.name}”` : `Tag “${t.name}”`,
    icon: 'label',
    onSelect: () => onMark({ tags: own.includes(t.id) ? own.filter((x) => x !== t.id) : [...own, t.id] }),
  }))
  return (
    <li className={`sv-comment-card${c.read ? '' : ' sv-comment-card--unread'}`}>
      <div className="sv-comment-card__head">
        <span className="sv-comment-card__who">
          {c.who}
          {c.group ? ` · ${c.group}` : ''}
          {c.scope_label ? ` · ${c.scope_label}` : ''}
        </span>
        {c.write_in && <Chip tone="info">Other</Chip>}
        {!c.read && <Chip tone="pending">New</Chip>}
      </div>
      <p className="sv-comment-card__text">{c.text}</p>
      <div className="sv-comment-card__foot">
        {own.map((id) => {
          const t = tags?.find((x) => x.id === id)
          return t ? <Chip key={id}>{t.name}</Chip> : null
        })}
        <span className="dialog__spacer" />
        <Button size="sm" variant="ghost" icon={c.read ? 'mark_email_unread' : 'mark_email_read'} onClick={() => onMark({ read: !c.read })}>
          {c.read ? 'Mark unread' : 'Mark read'}
        </Button>
        {items.length > 0 && <RowMenu label={`Tags for ${c.who}'s comment`} items={items} />}
      </div>
    </li>
  )
}

/** Comments: the questions with comments beside the comments on the one picked. */
export function CommentsTab({ survey, base }: SurveyCtx) {
  const { question } = useParams()
  const phone = usePhone()
  const path = { survey_id: survey.id }
  const inbox = useAdminQuery(`surveys/${survey.id}/comments`, () =>
    api.GET('/api/surveys/{survey_id}/comments', { params: { path } }).then(unwrap),
  )
  const [data, setData] = useState<Inbox | null>(null)
  const [error, setError] = useState<string | null>(null)
  const commentsBase = `${base}/comments`
  if (!question && !phone) return <Navigate to={`${commentsBase}/all`} replace />
  if (inbox.error && !inbox.data) return <LoadError error={inbox.error} onRetry={inbox.reload} />
  const current = data ?? inbox.data
  if (!current) return <Spinner label="Loading comments" />
  const comments = current.comments ?? []
  if (!comments.length)
    return (
      <EmptyState icon="chat" title="No comments yet">
        Comments and "Other" write-ins land here as people answer.
      </EmptyState>
    )

  const questions: { id: number; prompt: string; section: string; n: number; unread: number }[] = []
  for (const c of comments) {
    const found = questions.find((q) => q.id === c.question_id)
    if (found) {
      found.n++
      if (!c.read) found.unread++
    } else questions.push({ id: c.question_id, prompt: c.prompt, section: c.section_title, n: 1, unread: c.read ? 0 : 1 })
  }
  const picked = question === 'all' || !question ? null : Number(question)
  const shown = picked === null ? comments : comments.filter((c) => c.question_id === picked)
  const pickedQ = questions.find((q) => q.id === picked)

  async function mark(c: Comment, body: { read?: boolean; tags?: number[] }) {
    setError(null)
    try {
      setData(
        await api
          .PATCH('/api/surveys/{survey_id}/comments/{answer_id}', { params: { path: { ...path, answer_id: c.answer_id } }, body })
          .then(unwrap),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const list = (
    <SideListCard label="Questions with comments">
      <SideListRow
        to={`${commentsBase}/all`}
        title="All comments"
        sub={`${comments.length} · ${current.unread} new`}
        end={<CountBadge count={current.unread} label="new" />}
      />
      {questions.map((q) => (
        <SideListRow
          key={q.id}
          to={`${commentsBase}/${q.id}`}
          title={q.prompt}
          sub={`${q.section} · ${q.n}`}
          end={<CountBadge count={q.unread} label="new" />}
        />
      ))}
    </SideListCard>
  )
  const detail = (
    <section className="sv-card" aria-labelledby="sv-comments-title">
      <h2 id="sv-comments-title" className="sv-card__title">
        {pickedQ ? pickedQ.prompt : 'All comments'}
      </h2>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <ul className="sv-comments">
        {shown.map((c) => (
          <CommentCard key={c.answer_id} c={c} tags={current.tags} onMark={(body) => void mark(c, body)} />
        ))}
      </ul>
    </section>
  )
  return <SideListLayout list={list} detail={detail} open={Boolean(question)} backTo={commentsBase} backLabel="Questions" />
}
