import { useState } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { Chip, type Tone } from '../../../components'
import { formatDayTime } from '../../../lib/time'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded, Stats } from './Loading'
import { Pager } from './Pager'
import { SearchBox } from './Search'

type Message = Schemas['SentMessageEntry']
const LIMIT = 50

const STATUS: Record<string, { label: string; tone: Tone }> = {
  delivered: { label: 'Delivered', tone: 'ok' },
  discord_off: { label: 'Discord DMs off', tone: 'neutral' },
  blocked: { label: 'Blocked', tone: 'pending' },
  failed: { label: 'Failed', tone: 'cancelled' },
  pending: { label: 'Sending', tone: 'info' },
}

/** Every DM sent in the range and what happened to it. */
export function Messages({ bounds, zone }: ReportProps) {
  const [problems, setProblems] = useState(false)
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const query = useReport(['messages', bounds.start, bounds.end, problems, search, offset], () =>
    api
      .GET('/api/sent-messages', {
        params: {
          query: { start: bounds.start, end: bounds.end, problems_only: problems, search: search || undefined, limit: LIMIT, offset },
        },
      })
      .then(unwrap),
  )
  return (
    <>
      {query.data && (
        <Stats
          items={[
            { label: 'Sent', value: query.data.counts.sent.toLocaleString(), detail: `${query.data.counts.silent} silent` },
            { label: 'DMs off or blocked', value: query.data.counts.off_or_blocked },
            { label: 'Failed', value: query.data.counts.failed },
          ]}
        />
      )}
      <div className="report-filters">
        <div className="admin-pills" role="group" aria-label="Which messages">
          <button type="button" className="admin-pill" aria-pressed={!problems} onClick={() => {
              setProblems(false)
              setOffset(0)
            }}>
            All
          </button>
          <button type="button" className="admin-pill" aria-pressed={problems} onClick={() => {
              setProblems(true)
              setOffset(0)
            }}>
            Didn't arrive
          </button>
        </div>
        <SearchBox
          label="Search sent messages"
          value={search}
          onChange={(v) => {
            setSearch(v)
            setOffset(0)
          }}
        />
      </div>
      <Loaded query={query} label="sent messages">
        {(page) =>
          page.items.length === 0 ? (
            <p className="report-note">No messages match.</p>
          ) : (
            <>
              <ul className="report-log" aria-label="Sent messages">
                {page.items.map((m) => (
                  <MessageItem key={m.id} message={m} zone={zone} />
                ))}
              </ul>
              <Pager total={page.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )
        }
      </Loaded>
    </>
  )
}

function MessageItem({ message: m, zone }: { message: Message; zone: string }) {
  const status = STATUS[m.status] ?? { label: m.status, tone: 'neutral' as Tone }
  const copies = [
    m.push_devices ? `${m.push_devices} device${m.push_devices === 1 ? '' : 's'}` : null,
    m.email_sent == null ? null : m.email_sent ? 'email sent' : 'email failed',
    m.telegram_sent == null ? null : m.telegram_sent ? 'Telegram sent' : 'Telegram failed',
  ].filter(Boolean)
  return (
    <li>
      <details className="report-log__item">
        <summary>
          <span className="report-log__head">
            <b>{m.recipient_name ?? 'Someone'}</b>
            <span>{m.title ?? m.body.split('\n')[0]}</span>
            <span className="report-log__when">{formatDayTime(m.created_at, zone)}</span>
          </span>
          <span className="report-log__sub">
            <Chip tone={status.tone}>{status.label}</Chip>
            {m.silent && <Chip>Silent</Chip>}
            {copies.length > 0 && <span>{copies.join(' · ')}</span>}
          </span>
        </summary>
        <div className="report-log__body">
          <p className="report-message">{m.body}</p>
          {m.error && <p className="report-note">Why: {m.error}</p>}
          {m.link_url && (
            <p className="report-note">
              Button: {m.link_label ?? 'Open'} → {m.link_url}
            </p>
          )}
        </div>
      </details>
    </li>
  )
}
