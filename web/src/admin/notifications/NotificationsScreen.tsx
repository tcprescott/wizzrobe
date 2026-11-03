import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { useQuery } from '../../api/useQuery'
import {
  ActionsMenu,
  Banner,
  Button,
  Chip,
  Dialog,
  EmptyState,
  FormDialog,
  Icon,
  PageHeader,
  Panel,
  Spinner,
} from '../../components'
import { useCommunity } from '../../community/CommunityProvider'
import { usePhone } from '../../lib/usePhone'
import { useAdmin } from '../kit/useAdminNav'
import '../comms/comms.css'
import './notifications.css'

type Screen = Schemas['AdminNotificationsResponse']
type Message = Schemas['NotificationMessageResponse']
type Cell = Schemas['NotificationCellResponse']
type Mode = Cell['mode']
type StaffChoice = NonNullable<Cell['staff_choice']>

const MODES: { value: Mode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'silent', label: 'Silent' },
  { value: 'ping', label: 'Ping' },
]
const MODE_LABEL: Record<Mode, string> = { off: 'off', silent: 'silent', ping: 'ping' }
const STAFF_ORDER: StaffChoice[] = ['off', 'silent', 'ping_on_duty', 'ping_all']

/** The Staff choice as its segment says it; the help line under the recipient spells it out. */
function shortChoice(choice: StaffChoice, label: string): string {
  if (choice === 'ping_on_duty') return 'On-duty'
  if (choice === 'ping_all') return label === 'Ping' ? 'Ping' : 'All'
  return label
}

/** "Players ping · Crew silent · Staff ping on-duty": who gets a message, in words. */
export function summary(message: Message): string {
  return message.cells
    .map((c) =>
      c.column === 'staff_alerts' ? `Staff ${c.staff_label.toLowerCase()}` : `${c.label} ${MODE_LABEL[c.mode]}`,
    )
    .join(' · ')
}

function matches(message: Message, text: string, group: string, section: string): boolean {
  const needle = text.trim().toLowerCase()
  if (!needle) return true
  return `${message.title} ${message.when} ${group} ${section}`.toLowerCase().includes(needle)
}

/** Admin › Notifications, layout B: one row per message with who gets it; a sheet sets each recipient. */
export function NotificationsScreen() {
  const { community } = useCommunity()
  const { adminBase } = useAdmin()
  const navigate = useNavigate()
  const phone = usePhone()
  const screen = useQuery(`admin/notifications@${community.slug}`, () =>
    api.GET('/api/admin/notifications').then(unwrap),
  )
  const [search, setSearch] = useState('')
  const [changedOnly, setChangedOnly] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  const data: Screen | undefined = screen.data
  const cards = useMemo(
    () =>
      (data?.cards ?? [])
        .map((card) => ({
          ...card,
          messages: card.messages.filter(
            (m) => (!changedOnly || m.changed) && matches(m, search, card.group, card.section),
          ),
        }))
        .filter((card) => card.messages.length > 0),
    [data, search, changedOnly],
  )
  const selected = data?.cards.flatMap((c) => c.messages).find((m) => m.notice === open) ?? null

  async function save(work: () => Promise<unknown>, done: string) {
    setError(null)
    setSaved(null)
    try {
      await work()
      setSaved(done)
      screen.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  function setMode(message: Message, cell: Cell, mode: Mode) {
    if (mode === cell.mode) return
    void save(
      () =>
        api
          .PUT('/api/notification-settings/{notice}/{recipient}', {
            params: { path: { notice: message.notice, recipient: cell.column as Schemas['NotificationRecipient'] } },
            body: { mode },
          })
          .then(unwrap),
      `${message.title}: ${cell.label} ${MODE_LABEL[mode]}.`,
    )
  }

  function setStaff(message: Message, cell: Cell, choice: StaffChoice) {
    if (choice === cell.staff_choice) return
    const label = (cell.staff_options ?? []).find((o) => o.choice === choice)?.label ?? choice
    void save(
      () =>
        api
          .PUT('/api/notification-settings/{notice}/staff-alerts', {
            params: { path: { notice: message.notice } },
            body: { choice },
          })
          .then(unwrap),
      `${message.title}: staff ${label.toLowerCase()}.`,
    )
  }

  async function useDefaults(message: Message) {
    for (const cell of message.cells.filter((c) => c.changed)) {
      if (cell.column === 'staff_alerts') {
        if (cell.staff_default) setStaff(message, cell, cell.staff_default)
      } else {
        setMode(message, cell, cell.default)
      }
    }
  }

  async function resetAll() {
    setBusy(true)
    await save(async () => {
      const out = await api.POST('/api/notification-settings/reset').then(unwrap)
      return out
    }, 'Every notification is back on its default.')
    setBusy(false)
    setConfirmReset(false)
  }

  const sheet = selected && (
    <MessageSheet
      message={selected}
      onMode={(cell, mode) => setMode(selected, cell, mode)}
      onStaff={(cell, choice) => setStaff(selected, cell, choice)}
      onDefaults={() => void useDefaults(selected)}
    />
  )

  return (
    <>
      <PageHeader
        title="Notifications"
        lede="Who each message goes to, and whether it pings. Silent still arrives, without a sound or buzz; Off sends nothing on any channel. Email can't be silent."
        actions={
          <ActionsMenu
            items={[
              {
                label: 'Sent messages',
                icon: 'history',
                onSelect: () => navigate(`${adminBase}/reports?report=messages`),
              },
              {
                label: 'Reset all to defaults…',
                icon: 'restart_alt',
                destructive: true,
                disabled: !data?.changed,
                onSelect: () => setConfirmReset(true),
              },
            ]}
          />
        }
      />
      <div className="notify-toolbar">
        <label className="notify-search">
          <Icon name="search" size={20} />
          <span className="visually-hidden">Search notifications</span>
          <input type="search" placeholder="Search notifications" value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
        <button
          type="button"
          className={changedOnly ? 'filter-chip filter-chip--on' : 'filter-chip'}
          aria-pressed={changedOnly}
          onClick={() => setChangedOnly((v) => !v)}
        >
          Changed · {data?.changed ?? 0}
        </button>
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      {saved && (
        <p className="me-saved" role="status">
          {saved}
        </p>
      )}
      {screen.loading && !data ? (
        <Spinner label="Loading notifications" />
      ) : screen.error && !data ? (
        <Banner tone="error" action={<Button variant="outline" size="sm" onClick={screen.reload}>Try again</Button>}>{screen.error.message}</Banner>
      ) : cards.length === 0 ? (
        <EmptyState icon="notifications" title="No notifications match." />
      ) : (
        <div className={!phone && selected ? 'notify-split' : undefined}>
          <div className="notify-list">
            {cards.map((card) => (
              <Panel
                key={`${card.group}/${card.section}`}
                title={
                  <>
                    {card.section && <span className="notify-group">{card.group}</span>} {card.section || card.group}
                  </>
                }
              >
                <ul className="notify-rows">
                  {card.messages.map((m) => (
                    <li key={m.notice}>
                      <button
                        type="button"
                        className={m.notice === open ? 'notify-row notify-row--open' : 'notify-row'}
                        aria-expanded={m.notice === open}
                        onClick={() => setOpen(m.notice === open ? null : m.notice)}
                      >
                        <span className="notify-row__text">
                          <span className="notify-row__title">
                            {m.title}
                            {m.changed && <Chip tone="info">Changed</Chip>}
                          </span>
                          <span className="notify-row__sum">{summary(m)}</span>
                        </span>
                        <Icon name="chevron_right" />
                      </button>
                    </li>
                  ))}
                </ul>
              </Panel>
            ))}
          </div>
          {!phone && sheet && <aside className="notify-side" aria-label={`${selected.title} settings`}>{sheet}</aside>}
        </div>
      )}
      {phone && selected && (
        <Dialog open onOpenChange={(v) => !v && setOpen(null)} title={selected.title} description={selected.when}>
          {sheet}
        </Dialog>
      )}
      <FormDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="Reset notifications"
        description={`${data?.changed ?? 0} ${data?.changed === 1 ? 'setting goes' : 'settings go'} back to the default. Messages already sent aren't affected.`}
        primaryLabel="Reset"
        destructive
        busy={busy}
        busyLabel="Resetting…"
        onPrimary={() => void resetAll()}
      >
        <span />
      </FormDialog>
    </>
  )
}

function MessageSheet({
  message,
  onMode,
  onStaff,
  onDefaults,
}: {
  message: Message
  onMode: (cell: Cell, mode: Mode) => void
  onStaff: (cell: Cell, choice: StaffChoice) => void
  onDefaults: () => void
}) {
  const phone = usePhone()
  const staffDefault = message.cells.find((c) => c.column === 'staff_alerts' && c.changed)
  return (
    <div className="notify-sheet">
      {!phone && (
        <header className="notify-sheet__head">
          <h2>{message.title}</h2>
          <p>{message.when}</p>
        </header>
      )}
      {message.cells.map((cell) => {
        const id = `${message.notice}-${cell.column}`
        const staff = cell.column === 'staff_alerts'
        const current = staff ? (cell.staff_options ?? []).find((o) => o.choice === cell.staff_choice) : undefined
        return (
          <div key={cell.column} className="notify-recipient">
            <div className="notify-recipient__text">
              <span className="notify-recipient__label" id={`l-${id}`}>
                {cell.label}
              </span>
              <span className="notify-recipient__who">
                {staff ? (current?.help ?? `Custom: ${cell.staff_mix}`) : cell.who}
              </span>
            </div>
            <div className="segmented" role="radiogroup" aria-labelledby={`l-${id}`}>
              {staff
                ? STAFF_ORDER.flatMap((choice) => (cell.staff_options ?? []).filter((o) => o.choice === choice)).map((o) => (
                    <span key={o.choice}>
                      <input
                        type="radio"
                        id={`${id}-${o.choice}`}
                        name={id}
                        checked={cell.staff_choice === o.choice}
                        onChange={() => onStaff(cell, o.choice)}
                      />
                      <label htmlFor={`${id}-${o.choice}`}>{shortChoice(o.choice, o.label)}</label>
                    </span>
                  ))
                : MODES.map((mode) => (
                    <span key={mode.value}>
                      <input
                        type="radio"
                        id={`${id}-${mode.value}`}
                        name={id}
                        checked={cell.mode === mode.value}
                        onChange={() => onMode(cell, mode.value)}
                      />
                      <label htmlFor={`${id}-${mode.value}`}>{mode.label}</label>
                    </span>
                  ))}
            </div>
          </div>
        )
      })}
      <div className="notify-sheet__foot">
        <span className="notify-recipient__who">
          {staffDefault
            ? `Default for Staff: ${(staffDefault.staff_options ?? []).find((o) => o.choice === staffDefault.staff_default)?.label ?? 'Custom'}`
            : message.changed
              ? 'Some recipients differ from the default.'
              : 'Every recipient is on its default.'}
        </span>
        <Button variant="ghost" icon="undo" onClick={onDefaults} disabled={!message.changed}>
          Use defaults
        </Button>
      </div>
    </div>
  )
}
