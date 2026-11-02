import { useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { useQuery } from '../../api/useQuery'
import { Button, Chip, EmptyState, Icon, PageHeader, RowMenu, Spinner, type MenuItem } from '../../components'
import { useCommunity } from '../../community/CommunityProvider'
import { usePhone } from '../../lib/usePhone'
import {
  Detail,
  DetailEmpty,
  DetailLine,
  DetailSection,
  LoadError,
  SideListCard,
  SideListLayout,
  SideListRow,
  useAdmin,
} from '../kit'
import { ConfirmEventDialog, EventFormDialog, type Confirm } from './EventDialogs'
import { count, datesLabel, STATUS, type EventRow } from './data'

function EventDetail({ event, onAction }: { event: EventRow; onAction: (action: string) => void }) {
  const { community } = useCommunity()
  const { data: summary, error } = useQuery(`event-summary-${event.slug}`, () =>
    api.GET('/api/events/{slug}/summary', { params: { path: { slug: event.slug } } }).then(unwrap),
  )
  const status = STATUS[event.status]
  const settingsHref = `/t/${community.slug}/e/${event.slug}/admin/event-settings`

  const main =
    event.status === 'draft' ? (
      <Button variant="primary" icon="play_circle" onClick={() => onAction('go-live')}>
        Go live…
      </Button>
    ) : event.status === 'live' ? (
      <Button variant="outline" icon="inventory_2" onClick={() => onAction('archive')}>
        Archive…
      </Button>
    ) : (
      <Button variant="outline" icon="unarchive" onClick={() => onAction('unarchive')}>
        Unarchive…
      </Button>
    )

  const menu: MenuItem[] = [
    { label: 'Rename…', icon: 'edit', onSelect: () => onAction('rename') },
    { label: 'New event from this…', icon: 'content_copy', onSelect: () => onAction('copy') },
  ]
  if (event.status === 'live' && !event.is_default)
    menu.push({ label: 'Make default', icon: 'star', onSelect: () => onAction('make-default') })
  if (event.status === 'draft' && summary && summary.matches === 0)
    menu.push({ label: 'Delete…', icon: 'delete', destructive: true, onSelect: () => onAction('delete') })

  const open = summary
    ? [
        summary.open_matches ? `${count(summary.open_matches, 'match', 'matches')} not finished` : '',
        summary.future_shifts ? `${count(summary.future_shifts, 'shift', 'shifts')} still to come` : '',
        summary.open_race_rooms ? `${count(summary.open_race_rooms, 'race room', 'race rooms')} open` : '',
      ].filter(Boolean)
    : []

  return (
    <Detail
      title={event.name}
      sub={
        <>
          <Chip tone={status.tone}>{status.label}</Chip>
          {event.is_default && <Chip tone="neutral">Default</Chip>}
        </>
      }
      actions={
        <>
          <RowMenu label={`More actions for ${event.name}`} items={menu} />
          {main}
        </>
      }
    >
      <DetailSection>
        <DetailLine icon="date_range" sub="Dates">
          {datesLabel(event.starts_on, event.ends_on) || 'No dates yet'}
        </DetailLine>
        <DetailLine icon="trophy" sub="Size">
          {summary
            ? `${count(summary.tournaments, 'tournament', 'tournaments')} · ${count(summary.matches, 'match', 'matches')}`
            : error
              ? '—'
              : 'Counting…'}
        </DetailLine>
        <DetailLine icon="link" sub="Link">
          /t/{community.slug}/e/{event.slug}
        </DetailLine>
        {event.status === 'live' && summary && (
          <DetailLine icon="inventory_2" sub="Before archiving">
            {open.length ? open.join(' · ') : 'Nothing still open'}
          </DetailLine>
        )}
        {event.status === 'draft' && (
          <DetailLine icon="visibility" sub="Who can see it">
            Staff, and anyone holding a role in it, until it goes live
          </DetailLine>
        )}
      </DetailSection>
      <DetailSection>
        <Link to={settingsHref} className="settings-link">
          <Icon name="settings" size={18} />
          Event settings
        </Link>
      </DetailSection>
    </Detail>
  )
}

/**
 * Admin → Events, layout E2: the community's events as a side list, beside the
 * open event with its size, what archiving would leave open, and its actions.
 */
export function EventsScreen() {
  const { event: slug } = useParams()
  const navigate = useNavigate()
  const phone = usePhone()
  const { adminBase } = useAdmin()
  const base = `${adminBase}/events`
  const { data, error, reload } = useQuery('admin-events', () => api.GET('/api/events').then(unwrap))
  const [form, setForm] = useState<Parameters<typeof EventFormDialog>[0]['mode'] | null>(null)
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data) return <Spinner label="Loading events" />
  const events = data
  const event = events.find((e) => e.slug === slug)
  if (!slug && !phone && events[0]) return <Navigate to={`${base}/${events[0].slug}`} replace />
  const defaultEvent = events.find((e) => e.is_default)

  async function act(target: EventRow, action: string) {
    setNote(null)
    setFailure(null)
    if (action === 'rename') setForm({ kind: 'rename', event: target })
    else if (action === 'copy') setForm({ kind: 'copy', from: target })
    else if (action === 'go-live' || action === 'unarchive' || action === 'delete') setConfirm({ kind: action, event: target })
    else if (action === 'archive') {
      const summary = await api
        .GET('/api/events/{slug}/summary', { params: { path: { slug: target.slug } } })
        .then(unwrap)
        .catch(() => undefined)
      setConfirm({ kind: 'archive', event: target, summary })
    } else if (action === 'make-default') {
      try {
        await api.POST('/api/events/{slug}/make-default', { params: { path: { slug: target.slug } } }).then(unwrap)
        setNote(`Bare links now open ${target.name}.`)
        reload()
      } catch (err) {
        setFailure(err instanceof Error ? err.message : String(err))
      }
    }
  }

  const list = (
    <SideListCard label="Events">
      {events.map((e) => {
        const status = STATUS[e.status]
        return (
          <SideListRow
            key={e.slug}
            to={`${base}/${e.slug}`}
            leading={<Icon name="calendar_month" />}
            title={e.name}
            sub={
              <>
                <Chip tone={status.tone}>{e.is_default ? `${status.label} · default` : status.label}</Chip>{' '}
                {datesLabel(e.starts_on, e.ends_on)}
              </>
            }
          />
        )
      })}
    </SideListCard>
  )

  const detail = !slug ? (
    <DetailEmpty>Pick an event.</DetailEmpty>
  ) : event ? (
    <EventDetail key={event.slug} event={event} onAction={(a) => void act(event, a)} />
  ) : (
    <EmptyState icon="calendar_month" title="That event isn't here">
      It may have been deleted, or renamed.
    </EmptyState>
  )

  return (
    <>
      {phone && slug && <h1 className="visually-hidden">Events</h1>}
      {(!phone || !slug) && (
        <PageHeader
          title="Events"
          lede="Each SGL weekend or season is one event."
          actions={
            <Button icon="add" onClick={() => setForm({ kind: 'create' })}>
              New event
            </Button>
          }
        />
      )}
      {note && (
        <p className="settings-saved" role="status">
          {note}
        </p>
      )}
      {failure && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
      <SideListLayout list={list} detail={detail} open={Boolean(slug)} backTo={base} backLabel="Events" />
      {form && (
        <EventFormDialog
          mode={form}
          defaultName={defaultEvent?.name}
          onClose={() => setForm(null)}
          onDone={(saved, message) => {
            setForm(null)
            setNote(message)
            reload()
            navigate(`${base}/${saved.slug}`, { replace: form.kind === 'rename' })
          }}
        />
      )}
      {confirm && (
        <ConfirmEventDialog
          confirm={confirm}
          events={events}
          onClose={() => setConfirm(null)}
          onDone={(message, gone) => {
            setConfirm(null)
            setNote(message)
            reload()
            if (gone) navigate(base, { replace: true })
          }}
        />
      )}
    </>
  )
}
