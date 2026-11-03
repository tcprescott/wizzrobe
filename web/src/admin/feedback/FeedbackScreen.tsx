import { useMemo, useState } from 'react'
import { Link, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { useQuery } from '../../api/useQuery'
import { Banner, Button, Chip, EmptyState, Icon, IconButton, PageHeader, RowMenu, Spinner, type IconName } from '../../components'
import { useCommunity } from '../../community/CommunityProvider'
import { formatDayTime } from '../../lib/time'
import { usePhone } from '../../lib/usePhone'
import { useViewerZone } from '../comms/useViewerZone'
import { useAdmin } from '../kit/useAdminNav'
import '../comms/comms.css'
import './feedback.css'

type Feedback = Schemas['FeedbackResponse']
type Status = 'new' | 'reviewed' | 'all'

const CATEGORIES: { key: string; label: string; icon: IconName }[] = [
  { key: 'bug', label: 'Bug', icon: 'bug_report' },
  { key: 'suggestion', label: 'Suggestion', icon: 'lightbulb' },
  { key: 'praise', label: 'Praise', icon: 'favorite' },
  { key: 'other', label: 'Other', icon: 'chat_bubble' },
]
const categoryOf = (key: string) => CATEGORIES.find((c) => c.key === key) ?? CATEGORIES[3]!

/** A page path the sender gave, as a link only when it stays on this site. */
export function safePath(url: string | null | undefined): string | null {
  if (!url || !url.startsWith('/') || url.startsWith('//') || url.includes('\\')) return null
  return url
}

function useFeedback() {
  const { community } = useCommunity()
  return useQuery(`admin/feedback@${community.slug}`, () =>
    api.GET('/api/feedback', { params: { query: { limit: 500 } } }).then(unwrap),
  )
}

/** Admin › Feedback, layout B: an inbox. The list beside the open message on desktop; each its own screen on a phone. */
export function FeedbackScreen() {
  return (
    <Routes>
      <Route index element={<Inbox />} />
      <Route path=":id" element={<Inbox />} />
    </Routes>
  )
}

function Inbox() {
  const { id } = useParams()
  const navigate = useNavigate()
  const phone = usePhone()
  const zone = useViewerZone()
  const { adminBase } = useAdmin()
  const base = `${adminBase}/feedback`
  const query = useFeedback()
  const [status, setStatus] = useState<Status>('new')
  const [category, setCategory] = useState('any')
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  const all = query.data ?? []
  const newCount = all.filter((f) => f.status !== 'reviewed').length
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return all.filter(
      (f) =>
        (status === 'all' || (status === 'reviewed') === (f.status === 'reviewed')) &&
        (category === 'any' || f.category === category) &&
        (!needle || `${f.message} ${f.submitted_by_name ?? ''} ${f.page_url ?? ''}`.toLowerCase().includes(needle)),
    )
  }, [all, status, category, search])

  const openId = id ? Number(id) : phone ? null : (rows[0]?.id ?? null)
  const open = all.find((f) => f.id === openId) ?? null
  const stale = id !== undefined && query.data !== undefined && !open

  async function setReviewed(item: Feedback, reviewed: boolean) {
    setBusy(true)
    setError(null)
    try {
      await api
        .POST('/api/feedback/{feedback_id}/reviewed', { params: { path: { feedback_id: item.id } }, body: { reviewed } })
        .then(unwrap)
      setSaved(reviewed ? 'Marked as reviewed.' : 'Back in the New queue.')
      if (reviewed && status === 'new') {
        const at = rows.findIndex((r) => r.id === item.id)
        const next = rows[at + 1] ?? rows[at - 1]
        navigate(next && next.id !== item.id ? `${base}/${next.id}` : base, { replace: true })
      }
      query.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const copy = (text: string, what: string) =>
    void navigator.clipboard?.writeText(text).then(() => setSaved(`${what} copied.`))

  const detail = open && (
    <Detail
      item={open}
      zone={zone}
      busy={busy}
      onReviewed={(r) => void setReviewed(open, r)}
      onCopy={copy}
    />
  )

  if (phone && id !== undefined) {
    return (
      <>
        <Link className="admin-back" to={base}>
          <Icon name="chevron_left" />
          Feedback
        </Link>
        <PageHeader title={open ? `From ${open.submitted_by_name ?? 'someone'}` : 'Feedback'} />
        {error && <Banner tone="error">{error}</Banner>}
        {saved && (
          <p className="me-saved" role="status">
            {saved}
          </p>
        )}
        {query.loading && !query.data ? (
          <Spinner label="Loading feedback" />
        ) : stale ? (
          <Banner tone="warning">That feedback isn't here any more.</Banner>
        ) : (
          detail
        )}
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="Feedback"
        lede="What members sent with Send Feedback. Mark each one reviewed once it's handled."
        actions={<IconButton icon="refresh" label="Refresh" onClick={query.reload} />}
      />
      <div className="feedback-filters">
        <div className="segmented" role="radiogroup" aria-label="Which feedback">
          {(
            [
              ['new', `New · ${newCount}`],
              ['reviewed', 'Reviewed'],
              ['all', 'All'],
            ] as const
          ).map(([value, label]) => (
            <span key={value}>
              <input
                type="radio"
                id={`fb-${value}`}
                name="fb-status"
                checked={status === value}
                onChange={() => setStatus(value)}
              />
              <label htmlFor={`fb-${value}`}>{label}</label>
            </span>
          ))}
        </div>
        <label className="feedback-select">
          <span className="visually-hidden">Category</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="any">Any category</option>
            {CATEGORIES.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label className="notify-search">
          <Icon name="search" size={20} />
          <span className="visually-hidden">Search feedback</span>
          <input type="search" placeholder="Search" value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      {saved && (
        <p className="me-saved" role="status">
          {saved}
        </p>
      )}
      {stale && <Banner tone="warning">That feedback isn't here any more.</Banner>}
      {query.loading && !query.data ? (
        <Spinner label="Loading feedback" />
      ) : query.error && !query.data ? (
        <Banner tone="error" action={<Button variant="outline" size="sm" onClick={query.reload}>Try again</Button>}>{query.error.message}</Banner>
      ) : rows.length === 0 ? (
        <EmptyState icon="feedback" title={status === 'new' ? 'Nothing new.' : 'Nothing here.'}>
          New feedback shows up in this list.
        </EmptyState>
      ) : (
        <div className={phone ? undefined : 'feedback-split'}>
          <ul className="feedback-list" aria-label="Feedback">
            {rows.map((f) => {
              const cat = categoryOf(f.category)
              return (
                <li key={f.id}>
                  <Link
                    to={`${base}/${f.id}`}
                    className={f.id === openId ? 'feedback-item feedback-item--open' : 'feedback-item'}
                    aria-current={f.id === openId ? 'true' : undefined}
                  >
                    <span className="feedback-item__head">
                      <b>{f.submitted_by_name ?? 'Someone'}</b>
                      <span className="feedback-cat">
                        <Icon name={cat.icon} size={16} />
                        {cat.label}
                      </span>
                      <span className="feedback-item__when">{formatDayTime(f.created_at, zone)}</span>
                    </span>
                    <span className="feedback-item__msg">{f.message}</span>
                  </Link>
                </li>
              )
            })}
          </ul>
          {!phone && detail}
        </div>
      )}
    </>
  )
}

function Detail({
  item,
  zone,
  busy,
  onReviewed,
  onCopy,
}: {
  item: Feedback
  zone: string
  busy: boolean
  onReviewed: (reviewed: boolean) => void
  onCopy: (text: string, what: string) => void
}) {
  const cat = categoryOf(item.category)
  const href = safePath(item.page_url)
  const reviewed = item.status === 'reviewed'
  const who = item.submitted_by_name ?? 'Someone'
  return (
    <section className="feedback-detail" aria-label={`Feedback from ${who}`}>
      <header className="feedback-detail__head">
        <b>{who}</b>
        <span className="feedback-cat">
          <Icon name={cat.icon} size={16} />
          {cat.label}
        </span>
        {reviewed ? <Chip tone="ok">Reviewed</Chip> : <Chip tone="pending">New</Chip>}
        <span className="dialog__spacer" />
        <RowMenu
          label={`More for feedback from ${who}`}
          items={[
            { label: 'Copy message', icon: 'content_copy', onSelect: () => onCopy(item.message, 'Message') },
            ...(item.page_url
              ? [{ label: 'Copy page path', icon: 'link' as const, onSelect: () => onCopy(item.page_url ?? '', 'Page path') }]
              : []),
          ]}
        />
      </header>
      <p className="feedback-detail__msg">{item.message}</p>
      <dl className="feedback-detail__facts">
        <dt>Sent</dt>
        <dd>{formatDayTime(item.created_at, zone)}</dd>
        {item.page_url && (
          <>
            <dt>Page</dt>
            <dd>
              {href ? (
                <a href={href} target="_blank" rel="noopener noreferrer" className="feedback-page">
                  <Icon name="open_in_new" size={16} />
                  {item.page_url}
                </a>
              ) : (
                <span className="feedback-page feedback-page--plain">{item.page_url}</span>
              )}
            </dd>
          </>
        )}
      </dl>
      <div className="feedback-detail__foot">
        {reviewed ? (
          <Button variant="outline" icon="undo" onClick={() => onReviewed(false)} busy={busy} busyLabel="Saving…">
            Reopen
          </Button>
        ) : (
          <Button icon="check" onClick={() => onReviewed(true)} busy={busy} busyLabel="Saving…">
            Mark reviewed
          </Button>
        )}
      </div>
    </section>
  )
}
