import { useMemo, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, CountBadge, EmptyState, PageHeader, Panel, PanelRow, PatronBadge, Spinner } from '../../components'
import { useCommunity } from '../../community/CommunityProvider'
import { effectiveZone, formatDay } from '../../lib/time'
import { DetailEmpty, SideListCard, SideListLayout, SideListRow } from '../kit/SideList'
import { useAdmin } from '../kit/useAdminNav'
import { Avatar, useAction, useAdminData } from '../peopleKit'
import { AdaList, AdaRequestDetail } from './AdaRequests'
import { AddMemberDialog, DiscordImportDialog } from './MemberDialogs'
import { MemberDetail } from './MemberDetail'

type Board = Schemas['MembersBoardResponse']
type Row = Schemas['MemberRowResponse']

const SHOW_AT_MOST = 200

type Filter = 'all' | 'staff' | 'volunteers' | 'new' | 'ada'

function roleText(row: Row): string {
  const labels = row.roles.map((r) => (r.event_name ? `${r.label} (${r.event_name})` : r.label))
  if (row.tournaments_admin) labels.push(`Tournament admin (${row.tournaments_admin})`)
  if (row.tournaments_crew_coordinator) labels.push(`Crew coordinator (${row.tournaments_crew_coordinator})`)
  return labels.join(', ') || 'Member'
}

function matches(row: Row, filter: Filter, search: string): boolean {
  if (filter === 'staff' && !row.roles.some((r) => r.role === 'staff')) return false
  if (filter === 'volunteers' && !row.roles.some((r) => r.role === 'volunteer')) return false
  if (filter === 'new' && !row.joined_recently) return false
  if (filter === 'ada' && !row.ada) return false
  if (!search) return true
  const q = search.toLowerCase()
  return [row.username, row.display_name, row.challonge].some((v) => v?.toLowerCase().includes(q))
}

function JoinRequests({ onChange }: { onChange: () => void }) {
  const { data, reload } = useAdminData('join-requests', () => api.GET('/api/join-requests').then(unwrap))
  const { busy, error, run } = useAction()
  if (!data?.length) return null
  async function decide(id: number, approve: boolean) {
    const ok = await run(`${id}`, () =>
      approve
        ? api.POST('/api/join-requests/{request_id}/approve', { params: { path: { request_id: id } } }).then(unwrap)
        : api.POST('/api/join-requests/{request_id}/deny', { params: { path: { request_id: id } } }).then(unwrap),
    )
    if (ok) {
      reload()
      onChange()
    }
  }
  return (
    <Panel title={<>Join requests <CountBadge count={data.length} label={`${data.length} waiting`} /></>}>
      {data.map((r) => (
        <PanelRow key={r.id}>
          <div className="join-request">
            <Avatar name={r.user_name ?? '?'} />
            <span className="admin-name">
              <span>{r.user_name}</span>
              {r.message && <span className="admin-name__sub">“{r.message}”</span>}
            </span>
            <span className="join-request__buttons">
              <Button variant="outline" size="sm" busy={busy === `${r.id}`} onClick={() => void decide(r.id, false)}>
                Deny
              </Button>
              <Button variant="primary" size="sm" busy={busy === `${r.id}`} onClick={() => void decide(r.id, true)}>
                Approve
              </Button>
            </span>
          </div>
        </PanelRow>
      ))}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </Panel>
  )
}

function MemberList({ board, base, reload }: { board: Board; base: string; reload: () => void }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const zone = effectiveZone(board.zone)
  const counts = useMemo(
    () => ({
      staff: board.members.filter((m) => m.roles.some((r) => r.role === 'staff')).length,
      volunteers: board.members.filter((m) => m.roles.some((r) => r.role === 'volunteer')).length,
      new: board.members.filter((m) => m.joined_recently).length,
      ada: board.members.filter((m) => m.ada).length,
    }),
    [board],
  )
  const shown = board.members.filter((m) => matches(m, filter, search))
  const pills: [Filter, string][] = [
    ['all', `All ${board.members.length}`],
    ['staff', `Staff ${counts.staff}`],
    ['volunteers', `Volunteers ${counts.volunteers}`],
    ['new', `New this week ${counts.new}`],
  ]
  if (board.switches.accommodations) pills.push(['ada', `Needs ADA ${counts.ada}`])
  return (
    <div className="admin-stack">
      <JoinRequests onChange={reload} />
      <div className="admin-toolbar">
        <div className="admin-pills" role="group" aria-label="Show">
          {pills.map(([key, label]) => (
            <button key={key} type="button" className="admin-pill" aria-pressed={filter === key} onClick={() => setFilter(key)}>
              {label}
            </button>
          ))}
        </div>
        <input
          className="input admin-search"
          type="search"
          placeholder="Search names"
          aria-label="Search members"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      {shown.length === 0 ? (
        <EmptyState icon="groups" title="No one matches" />
      ) : (
        <SideListCard
          label="Members"
          more={shown.length > SHOW_AT_MOST ? `Showing ${SHOW_AT_MOST} of ${shown.length}. Search to find someone else.` : undefined}
        >
          {shown.slice(0, SHOW_AT_MOST).map((m) => (
            <SideListRow
              key={m.id}
              to={`${base}/${m.id}`}
              leading={<Avatar name={m.preferred_name} url={m.avatar_url} />}
              title={
                m.patron_badge ? (
                  <>
                    {m.preferred_name} <PatronBadge label={m.patron_badge} />
                  </>
                ) : (
                  m.preferred_name
                )
              }
              sub={`${roleText(m)}${m.joined_at ? ` · joined ${formatDay(m.joined_at, zone)}` : ''}`}
              end={
                !m.is_active ? (
                  <Chip tone="cancelled">Deactivated</Chip>
                ) : m.ada ? (
                  <Chip tone={m.ada.status === 'arranged' && !m.ada.changed_since_arranged ? 'ok' : 'pending'}>ADA</Chip>
                ) : undefined
              }
            />
          ))}
        </SideListCard>
      )}
    </div>
  )
}

function Tabs({ base, board }: { base: string; board: Board }) {
  const onAda = useLocation().pathname.startsWith(`${base}/ada`)
  if (!board.switches.accommodations) return null
  return (
    <nav className="admin-tabs" aria-label="Members views">
      <NavLink to={base} className={() => `admin-tab${onAda ? '' : ' active'}`} aria-current={onAda ? undefined : 'page'}>
        Members
      </NavLink>
      <NavLink to={`${base}/ada`} className="admin-tab">
        ADA requests <CountBadge count={board.ada_action_needed} label={`${board.ada_action_needed} need you`} />
      </NavLink>
    </nav>
  )
}

function MembersPage({ board, reload, base }: { board: Board; reload: () => void; base: string }) {
  const { memberId } = useParams()
  const id = memberId ? Number(memberId) : null
  const [adding, setAdding] = useState(false)
  const [importing, setImporting] = useState(false)
  const navigate = useNavigate()
  return (
    <>
      <PageHeader
        title="Members"
        lede="Everyone in the community, their roles, and how to reach them."
        actions={
          <>
            <Button variant="primary" icon="person_add" onClick={() => setAdding(true)}>
              Add member
            </Button>
            <ActionsMenu items={[{ label: 'Import from Discord…', icon: 'group_add', onSelect: () => setImporting(true) }]} />
          </>
        }
      />
      <Tabs base={base} board={board} />
      <SideListLayout
        open={id !== null}
        backTo={base}
        backLabel="Members"
        list={<MemberList board={board} base={base} reload={reload} />}
        detail={
          id !== null ? (
            <MemberDetail
              key={id}
              userId={id}
              board={board}
              onChange={reload}
              onRemoved={() => {
                reload()
                navigate(base)
              }}
            />
          ) : (
            <DetailEmpty>Pick someone to see their roles, how messages reach them, and what they're in.</DetailEmpty>
          )
        }
      />
      <AddMemberDialog
        open={adding}
        onOpenChange={setAdding}
        onAdded={(userId) => {
          reload()
          navigate(`${base}/${userId}`)
        }}
      />
      <DiscordImportDialog open={importing} onOpenChange={setImporting} onDone={reload} />
    </>
  )
}

function AdaPage({ board, reload, base }: { board: Board; reload: () => void; base: string }) {
  const { requestId } = useParams()
  const id = requestId ? Number(requestId) : null
  const ada = `${base}/ada`
  return (
    <>
      <PageHeader title="Members" lede="ADA accommodation requests, oldest first." />
      <Tabs base={base} board={board} />
      <SideListLayout
        open={id !== null}
        backTo={ada}
        backLabel="ADA requests"
        list={<AdaList base={ada} zone={board.zone} />}
        detail={
          id !== null ? (
            <AdaRequestDetail key={id} requestId={id} zone={board.zone} onChange={reload} />
          ) : (
            <DetailEmpty>Pick a request to read it and set where it stands.</DetailEmpty>
          )
        }
      />
    </>
  )
}

/** Admin → Members: the member list beside the open member, join requests, and ADA requests. */
export function MembersScreen() {
  const { adminBase } = useAdmin()
  const { community } = useCommunity()
  const [params] = useSearchParams()
  const base = `${adminBase}/members`
  const { data, error, reload } = useAdminData('members', () => api.GET('/api/admin/members').then(unwrap))

  const linked = params.get('member') ?? params.get('user_id')
  if (linked) return <Navigate to={`${base}/${linked}`} replace />
  const adaLinked = params.get('ada_request')
  if (adaLinked) return <Navigate to={`${base}/ada/${adaLinked}`} replace />

  if (error && !data) {
    return (
      <>
        <PageHeader title="Members" />
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if (!data) return <Spinner label={`Loading ${community.name} members`} />
  return (
    <Routes>
      <Route path="ada" element={<AdaPage board={data} reload={reload} base={base} />} />
      <Route path="ada/:requestId" element={<AdaPage board={data} reload={reload} base={base} />} />
      <Route path=":memberId" element={<MembersPage board={data} reload={reload} base={base} />} />
      <Route path="*" element={<MembersPage board={data} reload={reload} base={base} />} />
    </Routes>
  )
}
