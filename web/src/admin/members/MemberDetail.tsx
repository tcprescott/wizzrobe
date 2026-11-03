import { useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, Chip, FormDialog, IconButton, PatronBadge, RowMenu, Spinner, type MenuItem } from '../../components'
import { useCommunity } from '../../community/CommunityProvider'
import { effectiveZone, formatDay, formatDayTime } from '../../lib/time'
import { Detail, DetailLine, DetailSection } from '../kit/SideList'
import { Avatar, useAction, useAdminData } from '../peopleKit'
import { EmergencyContactDialog, MessageDialog, ProfileDialog } from './MemberDialogs'

type Board = Schemas['MembersBoardResponse']
type Details = Schemas['MemberDetailsResponse']
type RoleValue = Schemas['Role']

const EFFECT_LABELS: Record<string, string> = { ping: 'Ping', silent: 'Silent', off: 'Off' }

function channelLines(d: Details) {
  const c = d.channels
  const lines: { icon: 'chat' | 'mobile' | 'mail' | 'send'; text: string; sub: string; ok: boolean }[] = [
    {
      icon: 'chat',
      text: 'Discord DMs',
      sub: !c.has_discord ? 'No Discord linked' : c.discord_dms ? 'Open' : 'Closed: Discord refused the last DM',
      ok: c.has_discord && c.discord_dms,
    },
    {
      icon: 'mobile',
      text: 'Phone notifications',
      sub: c.push_devices ? `${c.push_devices} device${c.push_devices === 1 ? '' : 's'}` : 'Not turned on',
      ok: c.push_devices > 0,
    },
  ]
  if (c.email) lines.push({ icon: 'mail', text: 'Email', sub: c.email === 'on' ? 'On' : c.email === 'unverified' ? 'Not verified yet' : 'Off', ok: c.email === 'on' })
  if (c.telegram) lines.push({ icon: 'send', text: 'Telegram', sub: c.telegram === 'on' ? 'On' : 'Off', ok: c.telegram === 'on' })
  return lines
}

function Roles({ row, board, userId, onChange }: { row: Schemas['MemberRowResponse'] | undefined; board: Board; userId: number; onChange: () => void }) {
  const { event } = useCommunity()
  const { busy, error, run } = useAction()
  const [adding, setAdding] = useState('')
  const held = new Set((row?.roles ?? []).filter((r) => !r.event_name).map((r) => r.role))
  const options = board.role_options.filter((o) => !held.has(o.role))
  const perEvent = new Set(board.role_options.filter((o) => o.per_event).map((o) => o.role))
  const eventSlug = (role: string) => (perEvent.has(role) ? event?.slug ?? null : null)

  async function grant() {
    if (!adding) return
    const role = adding as RoleValue
    const ok = await run('add', () =>
      api.POST('/api/users/{user_id}/roles', { params: { path: { user_id: userId } }, body: { role, event: eventSlug(role) } }).then(unwrap),
    )
    if (ok) {
      setAdding('')
      onChange()
    }
  }
  async function revoke(role: string) {
    const ok = await run(role, () =>
      api
        .DELETE('/api/users/{user_id}/roles/{role}', {
          params: { path: { user_id: userId, role: role as RoleValue }, query: { event: eventSlug(role) } },
        })
        .then(unwrap),
    )
    if (ok) onChange()
  }

  return (
    <DetailSection title="Roles">
      <div className="admin-chips">
        {(row?.roles ?? []).length === 0 && <span className="admin-muted">No roles. They're a member.</span>}
        {(row?.roles ?? []).map((r) => (
          <span key={`${r.role}-${r.event_name ?? ''}`} className="role-chip">
            {r.event_name ? `${r.label} (${r.event_name})` : r.label}
            {!r.event_name && (
              <IconButton
                icon="close"
                label={`Remove ${r.label}`}
                className="role-chip__x"
                disabled={busy === r.role}
                onClick={() => void revoke(r.role)}
              />
            )}
          </span>
        ))}
      </div>
      {options.length > 0 && (
        <div className="role-add">
          <select className="input" aria-label="Role to add" value={adding} onChange={(e) => setAdding(e.target.value)}>
            <option value="">Add a role…</option>
            {options.map((o) => (
              <option key={o.role} value={o.role}>
                {o.per_event && event ? `${o.label} (${event.name})` : o.label}
              </option>
            ))}
          </select>
          <Button variant="outline" size="sm" disabled={!adding} busy={busy === 'add'} onClick={() => void grant()}>
            Add
          </Button>
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </DetailSection>
  )
}

function Footprint({ d, zone }: { d: Details; zone: string }) {
  const lines: { icon: 'trophy' | 'admin_panel_settings' | 'groups' | 'sports_esports' | 'volunteer_activism' | 'inventory_2' | 'how_to_reg'; text: string; sub?: string }[] = []
  if (d.entered.length) lines.push({ icon: 'trophy', text: `Entered in ${d.entered.length}`, sub: d.entered.join(', ') })
  if (d.tournament_admin_of.length) lines.push({ icon: 'admin_panel_settings', text: 'Tournament admin', sub: d.tournament_admin_of.join(', ') })
  if (d.crew_coordinator_of.length) lines.push({ icon: 'groups', text: 'Crew coordinator', sub: d.crew_coordinator_of.join(', ') })
  for (const m of d.upcoming_matches.slice(0, 5)) {
    lines.push({
      icon: 'sports_esports',
      text: `${m.tournament}${m.opponents.length ? ` vs ${m.opponents.join(', ')}` : ''}`,
      sub: formatDayTime(m.scheduled_at, zone),
    })
  }
  for (const s of (d.upcoming_shifts ?? []).slice(0, 5)) {
    lines.push({
      icon: 'volunteer_activism',
      text: s.position,
      sub: `${formatDayTime(s.starts_at, zone)}${s.called_off ? ' · called off' : s.checked_in ? ' · checked in' : ''}`,
    })
  }
  if (d.equipment_out?.length) lines.push({ icon: 'inventory_2', text: 'Equipment out', sub: d.equipment_out.join(', ') })
  if (d.check_in) lines.push({ icon: 'how_to_reg', text: 'Event check-in', sub: d.check_in })
  return (
    <DetailSection title="In this community">
      {lines.length === 0 ? (
        <p className="admin-muted">Nothing yet: no tournaments, matches or shifts.</p>
      ) : (
        lines.map((l, i) => (
          <DetailLine key={i} icon={l.icon} sub={l.sub}>
            {l.text}
          </DetailLine>
        ))
      )}
    </DetailSection>
  )
}

/** The open member: who they are, their roles, how messages reach them, and what they're in. */
export function MemberDetail({
  userId,
  board,
  onChange,
  onRemoved,
}: {
  userId: number
  board: Board
  onChange: () => void
  onRemoved: () => void
}) {
  const { data, error, reload } = useAdminData(`member/${userId}`, () =>
    api.GET('/api/users/{user_id}/details', { params: { path: { user_id: userId } } }).then(unwrap),
  )
  const [dialog, setDialog] = useState<'message' | 'profile' | 'contact' | 'remove' | null>(null)
  const [showAllNotices, setShowAllNotices] = useState(false)
  const { busy, error: actionError, run } = useAction()
  const row = board.members.find((m) => m.id === userId)
  const zone = effectiveZone(board.zone)

  if (error && !data) return <Banner tone="error">{error.message}</Banner>
  if (!data) return <Spinner label="Loading member" />

  const name = data.display_name || data.username
  const changed = () => {
    reload()
    onChange()
  }
  async function setActive(active: boolean) {
    if (await run('active', () => api.PATCH('/api/users/{user_id}/admin', { params: { path: { user_id: userId } }, body: { is_active: active } }).then(unwrap))) changed()
  }
  async function remove() {
    if (await run('remove', () => api.DELETE('/api/users/{user_id}/membership', { params: { path: { user_id: userId } } }).then(unwrap))) {
      setDialog(null)
      onRemoved()
    }
  }

  const menu: MenuItem[] = []
  if (board.switches.emergency_contacts) menu.push({ label: 'Emergency contact…', icon: 'contact_emergency', onSelect: () => setDialog('contact') })
  menu.push(
    data.is_active
      ? { label: 'Deactivate account', icon: 'block', onSelect: () => void setActive(false) }
      : { label: 'Reactivate account', icon: 'undo', onSelect: () => void setActive(true) },
    { label: 'Remove from community…', icon: 'delete', destructive: true, onSelect: () => setDialog('remove') },
  )

  const notices = data.notifications.filter((n) => showAllNotices || n.effective !== n.community || n.theirs !== 'default')
  const subBits = [`@${data.username}`, data.pronouns, row?.joined_at ? `joined ${formatDay(row.joined_at, zone)}${row.joined_via ? ` via ${row.joined_via.toLowerCase()}` : ''}` : null]

  return (
    <Detail
      leading={<Avatar name={name} url={row?.avatar_url} size={48} />}
      title={
        <>
          {name} {data.patron?.is_patron && data.patron.badge_label && !data.patron.badge_hidden && <PatronBadge label={data.patron.badge_label} />}{' '}
          {!data.is_active && <Chip tone="cancelled">Deactivated</Chip>}
        </>
      }
      sub={subBits.filter(Boolean).join(' · ')}
      actions={
        <>
          <Button variant="primary" size="sm" icon="send" disabled={!data.channels.has_discord} onClick={() => setDialog('message')}>
            Message
          </Button>
          <Button variant="outline" size="sm" icon="edit" onClick={() => setDialog('profile')}>
            Edit
          </Button>
          <RowMenu label={`More for ${name}`} items={menu} />
        </>
      }
    >
      {actionError && <Banner tone="error">{actionError}</Banner>}
      {row?.ada && (
        <Banner tone={row.ada.status === 'arranged' && !row.ada.changed_since_arranged ? 'info' : 'warning'}>
          ADA request: {row.ada.label}
          {row.ada.changed_since_arranged ? ', changed since it was arranged' : ''}.
        </Banner>
      )}
      <Roles row={row} board={board} userId={userId} onChange={onChange} />
      <DetailSection title="How messages reach them">
        {channelLines(data).map((l) => (
          <DetailLine key={l.text} icon={l.icon} sub={l.sub} end={<Chip tone={l.ok ? 'ok' : 'neutral'}>{l.ok ? 'On' : 'Off'}</Chip>}>
            {l.text}
          </DetailLine>
        ))}
        {data.channels.last_delivered_at && (
          <p className="admin-muted">Last message delivered {formatDayTime(data.channels.last_delivered_at, zone)}.</p>
        )}
      </DetailSection>
      <DetailSection title="Notifications">
        <p className="admin-muted">
          Preset: {data.preset === 'needs_me' ? 'Only what needs me' : data.preset === 'quiet' ? 'Quiet' : 'Everything'}.{' '}
          {showAllNotices ? 'Showing every message.' : 'Showing only where theirs differs from the community setting.'}
        </p>
        {notices.map((n) => (
          <DetailLine
            key={`${n.key}-${n.recipient}`}
            icon={n.effective === 'off' ? 'notifications_off' : 'notifications'}
            sub={n.reason ?? n.section}
            end={<Chip tone={n.effective === 'ping' ? 'info' : 'neutral'}>{EFFECT_LABELS[n.effective] ?? n.effective}</Chip>}
          >
            {n.title}
          </DetailLine>
        ))}
        <Button variant="ghost" size="sm" onClick={() => setShowAllNotices((v) => !v)}>
          {showAllNotices ? 'Show only differences' : `Show all ${data.notifications.length}`}
        </Button>
      </DetailSection>
      <Footprint d={data} zone={zone} />
      {data.patron?.is_patron && (
        <DetailSection title="Patreon">
          <DetailLine
            icon="favorite"
            sub={[
              data.patron.last_synced ? `Checked ${formatDayTime(data.patron.last_synced, zone)}` : null,
              data.patron.badge_hidden ? 'they hide their badge' : 'badge shown',
            ]
              .filter(Boolean)
              .join(' · ')}
          >
            Active patron · {data.patron.badge_label ?? 'Patron'}
          </DetailLine>
        </DetailSection>
      )}
      {data.linked_accounts.length > 0 && (
        <DetailSection title="Linked accounts">
          {data.linked_accounts.map((a) => (
            <DetailLine key={a.service} icon="link" sub={a.service}>
              {a.name}
            </DetailLine>
          ))}
        </DetailSection>
      )}
      <DetailSection title="Recent messages">
        {data.recent_messages.length === 0 ? (
          <p className="admin-muted">No messages sent to them here yet.</p>
        ) : (
          data.recent_messages.slice(0, 5).map((m) => (
            <DetailLine
              key={m.id}
              icon={m.error ? 'error' : m.silent ? 'notifications_off' : 'chat'}
              sub={`${formatDayTime(m.created_at, zone)}${m.error ? ` · failed: ${m.error}` : m.silent ? ' · silent' : ''}`}
            >
              {m.title || m.body.slice(0, 120)}
            </DetailLine>
          ))
        )}
      </DetailSection>

      <MessageDialog open={dialog === 'message'} onOpenChange={(o) => setDialog(o ? 'message' : null)} userId={userId} name={name} onSent={reload} />
      <ProfileDialog
        open={dialog === 'profile'}
        onOpenChange={(o) => setDialog(o ? 'profile' : null)}
        userId={userId}
        displayName={data.display_name ?? ''}
        pronouns={data.pronouns ?? ''}
        onSaved={changed}
      />
      {board.switches.emergency_contacts && (
        <EmergencyContactDialog open={dialog === 'contact'} onOpenChange={(o) => setDialog(o ? 'contact' : null)} userId={userId} name={name} zone={zone} />
      )}
      <FormDialog
        open={dialog === 'remove'}
        onOpenChange={(o) => setDialog(o ? 'remove' : null)}
        title={`Remove ${name}?`}
        size="sm"
        primaryLabel="Remove"
        destructive
        busy={busy === 'remove'}
        onPrimary={() => void remove()}
      >
        <p>They lose their roles here and drop out of this community's lists. Their account stays, and they can ask to join again.</p>
        {actionError && (
          <p className="form-error" role="alert">
            {actionError}
          </p>
        )}
      </FormDialog>
    </Detail>
  )
}
