import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, EmptyState, FormDialog, PageHeader, Panel, RowMenu, Spinner, type MenuItem, type Tone } from '../../components'
import { useCommunity } from '../../community/CommunityProvider'
import { effectiveZone, formatDay, formatDayTime } from '../../lib/time'
import { DataTable, LoadError, useAdminQuery, type Column } from '../kit'
import { useAction } from '../peopleKit'
import { CompRefundsPanel } from './CompRefunds'
import { lanyardLabel, money, PROVIDERS, providerName, type BadgeType, type Order, type Provider, type Store } from './data'
import { StoreDialog, TypeDialog } from './TicketDialogs'
import './tickets.css'

type Note = { tone: 'info' | 'warning' | 'error'; text: string }

function status(t: BadgeType): { label: string; tone: Tone } {
  if (!t.is_active) return { label: 'Off sale', tone: 'neutral' }
  if (t.on_sale) return { label: 'Open', tone: 'ok' }
  return { label: t.not_on_sale_reason ?? 'Closed', tone: t.remaining === 0 ? 'neutral' : 'pending' }
}

function Sold({ t }: { t: BadgeType }) {
  if (t.quantity == null) return <span>{t.sold} sold</span>
  const pct = Math.min(100, Math.round((t.sold / t.quantity) * 100))
  return (
    <span className="tk-sold">
      <span className="tk-sold__bar" aria-hidden="true">
        <span className="tk-sold__fill" style={{ width: `${pct}%` }} />
      </span>
      <span>
        {t.sold} / {t.quantity}
      </span>
    </span>
  )
}

function orderTitle(o: Order): string {
  const counts = new Map<string, number>()
  for (const b of o.badges) counts.set(b.ticket_type_name, (counts.get(b.ticket_type_name) ?? 0) + 1)
  const what = [...counts].map(([name, n]) => (n > 1 ? `${n} × ${name}` : name)).join(', ')
  return [what, money(o.total_cents, o.currency)].filter(Boolean).join(' · ')
}

function orderSub(o: Order, zone: string): string {
  const parts = [providerName(o.provider), formatDayTime(o.paid_at ?? o.created_at, zone)]
  const claimed = o.badges.filter((b) => b.claimed_at && b.owner_id !== o.buyer_id)
  if (claimed.length === 1) parts.push(`claimed by ${claimed[0]!.owner_name ?? 'someone'}`)
  else if (claimed.length > 1) parts.push(`${claimed.length} claimed`)
  if (o.refunded_cents > 0 && o.refunded_cents < o.total_cents) parts.push(`${money(o.refunded_cents, o.currency)} refunded`)
  return parts.join(' · ')
}

const ORDER_CHIP: Record<string, { label: string; tone: Tone }> = {
  refunded: { label: 'Refunded', tone: 'neutral' },
  pending: { label: 'Waiting on the store', tone: 'pending' },
  expired: { label: 'Expired', tone: 'neutral' },
}

const SHOWN_ORDERS = 20

/** Admin → Tickets: the stores badges sell through, the badge types, comp refunds and recent orders. */
export function TicketsScreen() {
  const { event } = useCommunity()
  const zone = effectiveZone(null)
  const stores = useAdminQuery('tickets/stores', () => api.GET('/api/tickets/stores').then(unwrap))
  const types = useAdminQuery('tickets/types', () => api.GET('/api/tickets/types').then(unwrap))
  const orders = useAdminQuery('tickets/orders', () => api.GET('/api/tickets/orders').then(unwrap))
  const comps = useAdminQuery('tickets/comp-refunds', () => api.GET('/api/tickets/comp-refunds').then(unwrap))
  const [note, setNote] = useState<Note | null>(null)
  const [connecting, setConnecting] = useState<Provider | null>(null)
  const [editing, setEditing] = useState<BadgeType | 'new' | null>(null)
  const [confirm, setConfirm] = useState<{ kind: 'disconnect'; store: Store } | { kind: 'delete'; type: BadgeType } | { kind: 'refund'; order: Order } | null>(null)
  const [allOrders, setAllOrders] = useState(false)
  const { busy, error, setError, run } = useAction()

  const reloadSales = () => {
    types.reload()
    orders.reload()
    comps.reload()
  }

  const load = [stores, types, orders, comps].find((q) => q.error && !q.data)
  if (load?.error) {
    return (
      <>
        <PageHeader title="Tickets" />
        <LoadError error={load.error} onRetry={() => [stores, types, orders, comps].forEach((q) => q.reload())} />
      </>
    )
  }
  if (!stores.data || !types.data || !orders.data || !comps.data) return <Spinner label="Loading tickets" />

  const byProvider = new Map(stores.data.map((s) => [s.provider, s]))
  const closes = types.data
    .filter((t) => t.is_active && t.sales_close_at)
    .map((t) => t.sales_close_at!)
    .sort()
    .at(-1)
  const lede = [event ? `Badges for ${event.name}` : 'Badges for this event', closes ? `sales close ${formatDay(closes, zone)}` : null].filter(Boolean).join(' · ')

  async function setActive(t: BadgeType, on: boolean) {
    const ok = await run(`active-${t.id}`, () => api.PATCH('/api/tickets/types/{type_id}', { params: { path: { type_id: t.id } }, body: { is_active: on } }).then(unwrap))
    if (ok) types.reload()
    else setNote({ tone: 'error', text: error ?? `Couldn't change ${t.name}.` })
  }

  async function confirmNow() {
    if (!confirm) return
    if (confirm.kind === 'disconnect') {
      const provider = confirm.store.provider as Provider
      if (await run('confirm', () => api.DELETE('/api/tickets/stores/{provider}', { params: { path: { provider } } }).then(unwrap))) {
        setConfirm(null)
        stores.reload()
        setNote({ tone: 'info', text: `${providerName(provider)} disconnected. Buyers can't pick it until someone connects it again.` })
      }
    } else if (confirm.kind === 'delete') {
      const id = confirm.type.id
      if (await run('confirm', () => api.DELETE('/api/tickets/types/{type_id}', { params: { path: { type_id: id } } }).then(unwrap))) {
        setConfirm(null)
        types.reload()
      }
    } else {
      const o = confirm.order
      if (await run('confirm', () => api.POST('/api/tickets/orders/{order_id}/refund', { params: { path: { order_id: o.id } } }).then(unwrap))) {
        setConfirm(null)
        reloadSales()
        setNote({ tone: 'info', text: `Refund sent through ${providerName(o.provider)}. ${o.buyer_name ?? 'The buyer'}'s badges no longer check in.` })
      }
    }
  }

  const typeColumns: Column<BadgeType>[] = [
    {
      key: 'name',
      header: 'Badge',
      primary: true,
      cell: (t) => (
        <span className="tk-name">
          {t.name}
          {t.covers_comp && <span className="tk-row__sub"> · comp badge</span>}
        </span>
      ),
    },
    { key: 'price', header: 'Price', cell: (t) => money(t.price_cents, t.currency) },
    { key: 'sold', header: 'Sold', cell: (t) => <Sold t={t} /> },
    { key: 'lanyard', header: 'Check-in lanyard', cell: (t) => lanyardLabel(t.lanyard), desktopOnly: true },
    {
      key: 'status',
      header: 'Status',
      cell: (t) => {
        const s = status(t)
        return <Chip tone={s.tone}>{s.label}</Chip>
      },
    },
  ]

  const typeMenu = (t: BadgeType): MenuItem[] => [
    { label: 'Edit…', icon: 'edit', onSelect: () => setEditing(t) },
    t.is_active
      ? { label: 'Stop selling', icon: 'pause', disabled: busy === `active-${t.id}`, onSelect: () => void setActive(t, false) }
      : { label: 'Start selling', icon: 'play_arrow', disabled: busy === `active-${t.id}`, onSelect: () => void setActive(t, true) },
    { label: 'Delete…', icon: 'delete', destructive: true, disabled: t.sold > 0, onSelect: () => setConfirm({ kind: 'delete', type: t }) },
  ]

  const shownOrders = allOrders ? orders.data : orders.data.slice(0, SHOWN_ORDERS)
  const confirmText = (() => {
    if (!confirm) return null
    if (confirm.kind === 'disconnect') {
      const name = providerName(confirm.store.provider)
      return {
        title: `Disconnect ${name}?`,
        body: `Buyers can't pay with ${name} until someone connects it again, and its orders can't be refunded from here meanwhile. Paid badges stay valid.`,
        action: 'Disconnect',
      }
    }
    if (confirm.kind === 'delete') {
      return { title: `Delete ${confirm.type.name}?`, body: 'Nobody has bought one yet, so nothing else changes.', action: 'Delete' }
    }
    const o = confirm.order
    const left = o.total_cents - o.refunded_cents
    return {
      title: `Refund ${o.buyer_name ?? 'this'}'s order?`,
      body: `Sends ${money(left, o.currency)} back through ${providerName(o.provider)} and voids ${o.badges.length === 1 ? 'its badge' : `its ${o.badges.length} badges`}, claimed ones included. This can't be undone.`,
      action: `Refund ${money(left, o.currency)}`,
    }
  })()

  return (
    <>
      <PageHeader
        title="Tickets"
        lede={lede}
        actions={
          <>
            <Button icon="add" onClick={() => setEditing('new')}>
              New badge type
            </Button>
            <ActionsMenu
              label="Actions"
              items={PROVIDERS.map((p) => ({
                label: byProvider.has(p.id) ? `Replace ${p.name} credentials…` : `Connect ${p.name}…`,
                icon: 'key' as const,
                onSelect: () => setConnecting(p.id),
              }))}
            />
          </>
        }
      />
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      {comps.data.has_comp_rules && comps.data.comp_type_id == null && types.data.length > 0 && (
        <Banner tone="info">Comped staff and volunteers can't be refunded until one badge type is marked "A comp covers this badge". Edit the badge a comp stands in for.</Banner>
      )}

      {comps.data.rows.length > 0 && (
        <CompRefundsPanel
          rows={comps.data.rows}
          onDone={(n) => {
            setNote(n)
            reloadSales()
          }}
        />
      )}

      <Panel title="Stores" actions={<span className="tk-panel-note">Each community connects its own account</span>}>
        <ul className="tk-list" aria-label="Stores">
          {PROVIDERS.map((p) => {
            const s = byProvider.get(p.id)
            const sub = s
              ? [s.account_label ?? 'Connected', s.test_mode ? 'test mode, no real money' : 'live', s.connected_by ? `connected by ${s.connected_by}` : null]
                  .filter(Boolean)
                  .join(' · ')
              : `Not connected · buyers can't pick ${p.name}`
            return (
              <li key={p.id} className="panel__row tk-row">
                <span className="tk-store__mark" aria-hidden="true">
                  {p.name[0]}
                </span>
                <span className="tk-row__text">
                  <span className="tk-row__title">
                    <strong>{p.name}</strong>
                  </span>
                  <span className="tk-row__sub">{sub}</span>
                </span>
                {s ? (
                  <>
                    <Chip tone={s.test_mode ? 'pending' : 'ok'}>{s.test_mode ? 'Test mode' : 'Connected'}</Chip>
                    <RowMenu
                      label={`More for ${p.name}`}
                      items={[
                        { label: 'Replace credentials…', icon: 'key', onSelect: () => setConnecting(p.id) },
                        { label: 'Disconnect…', icon: 'link_off', destructive: true, onSelect: () => setConfirm({ kind: 'disconnect', store: s }) },
                      ]}
                    />
                  </>
                ) : (
                  <Button variant="outline" size="sm" aria-label={`Connect ${p.name}`} onClick={() => setConnecting(p.id)}>
                    Connect
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      </Panel>

      <Panel title={<>Badge types <span className="tk-count">{types.data.length}</span></>}>
        <DataTable
          label="Badge types"
          columns={typeColumns}
          rows={types.data}
          rowKey={(t) => t.id}
          onOpen={(t) => setEditing(t)}
          openLabel={(t) => `Edit ${t.name}`}
          rowMenu={typeMenu}
          rowMenuLabel={(t) => `More for ${t.name}`}
          empty={{ icon: 'local_activity', title: 'No badge types yet', body: 'Add one with New badge type. Buyers see it once a store is connected.' }}
        />
      </Panel>

      <Panel title="Recent orders" actions={<span className="tk-panel-note">Refund from the order's ⋮; the store moves the money</span>}>
        {orders.data.length === 0 ? (
          <EmptyState icon="local_activity" title="No orders yet">
            Paid orders show up here as the store confirms them.
          </EmptyState>
        ) : (
          <>
            <ul className="tk-list" aria-label="Recent orders">
              {shownOrders.map((o) => {
                const chip = ORDER_CHIP[o.status]
                const refundable = o.status === 'paid' && o.refunded_cents < o.total_cents
                return (
                  <li key={o.id} className="panel__row tk-row">
                    <span className="tk-row__text">
                      <span className="tk-row__title">
                        <strong>{o.buyer_name ?? 'Someone'}</strong> · {orderTitle(o)}
                      </span>
                      <span className="tk-row__sub">{orderSub(o, zone)}</span>
                    </span>
                    {chip && <Chip tone={chip.tone}>{chip.label}</Chip>}
                    {refundable && (
                      <RowMenu
                        label={`More for ${o.buyer_name ?? 'this'} order`}
                        items={[{ label: 'Refund…', icon: 'undo', destructive: true, onSelect: () => setConfirm({ kind: 'refund', order: o }) }]}
                      />
                    )}
                  </li>
                )
              })}
            </ul>
            {!allOrders && orders.data.length > SHOWN_ORDERS && (
              <div className="tk-foot">
                <span className="tk-row__sub">
                  Showing {SHOWN_ORDERS} of {orders.data.length}
                </span>
                <Button variant="ghost" onClick={() => setAllOrders(true)}>
                  Show all
                </Button>
              </div>
            )}
          </>
        )}
      </Panel>

      {connecting && (
        <StoreDialog
          open
          onOpenChange={(o) => !o && setConnecting(null)}
          provider={connecting}
          replacing={byProvider.has(connecting)}
          onSaved={() => {
            const name = providerName(connecting)
            stores.reload()
            setNote({ tone: 'info', text: `${name} connected. Buyers can pay with it now.` })
          }}
        />
      )}
      <TypeDialog
        open={editing != null}
        onOpenChange={(o) => !o && setEditing(null)}
        type={editing === 'new' ? null : editing}
        zone={zone}
        onSaved={() => {
          types.reload()
          comps.reload()
        }}
      />
      <FormDialog
        open={confirm != null}
        onOpenChange={(o) => {
          if (!o) {
            setConfirm(null)
            setError(null)
          }
        }}
        title={confirmText?.title ?? ''}
        size="sm"
        primaryLabel={confirmText?.action ?? ''}
        destructive
        busy={busy === 'confirm'}
        onPrimary={() => void confirmNow()}
      >
        <p>{confirmText?.body}</p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </FormDialog>
    </>
  )
}
