import { useState } from 'react'
import { Link, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, EmptyState, FormDialog, Icon, PageHeader, RowMenu, Spinner, type MenuItem, type Tone } from '../../components'
import { effectiveZone, formatDayTime } from '../../lib/time'
import { Detail, DetailEmpty, DetailLine, DetailSection, SideListCard, SideListLayout, SideListRow } from '../kit/SideList'
import { useAdmin } from '../kit/useAdminNav'
import { useAction, useAdminData } from '../peopleKit'
import { AssetDialog, CheckoutDialog } from './EquipmentDialogs'
import './equipment.css'

type Asset = Schemas['EquipmentResponse']
type Filter = 'all' | 'checked_out' | 'available' | 'retired'

const STATUS: Record<string, { label: string; tone: Tone }> = {
  available: { label: 'Available', tone: 'ok' },
  checked_out: { label: 'Checked out', tone: 'pending' },
  retired: { label: 'Retired', tone: 'neutral' },
}

function StatusChip({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: 'neutral' as Tone }
  return <Chip tone={s.tone}>{s.label}</Chip>
}

function matches(a: Asset, filter: Filter, search: string): boolean {
  if (filter !== 'all' && a.status !== filter) return false
  const q = search.trim().toLowerCase().replace(/^#/, '')
  if (!q) return true
  return String(a.asset_number) === q || [a.name, a.owner_name, a.borrower_name].some((v) => v?.toLowerCase().includes(q))
}

function AssetDetail({ id, zone, onChange, onDeleted }: { id: number; zone: string; onChange: () => void; onDeleted: () => void }) {
  const { data, error, reload } = useAdminData(`equipment/${id}`, () =>
    api.GET('/api/equipment/{equipment_id}', { params: { path: { equipment_id: id } } }).then(unwrap),
  )
  const loans = useAdminData(`equipment/${id}/loans`, () =>
    api.GET('/api/equipment/{equipment_id}/loans', { params: { path: { equipment_id: id } } }).then(unwrap),
  )
  const { adminBase } = useAdmin()
  const navigate = useNavigate()
  const [dialog, setDialog] = useState<'edit' | 'out' | 'delete' | null>(null)
  const { busy, error: actionError, run } = useAction()

  if (error && !data) return <Banner tone="error">{error.message}</Banner>
  if (!data) return <Spinner label="Loading the asset" />
  const path = { params: { path: { equipment_id: id } } }
  const changed = () => {
    reload()
    loans.reload()
    onChange()
  }
  async function checkIn() {
    if (await run('in', () => api.POST('/api/equipment/{equipment_id}/checkin', path).then(unwrap))) changed()
  }
  async function setRetired(retired: boolean) {
    if (!data) return
    const body = {
      name: data.name,
      description: data.description ?? null,
      private_notes: data.private_notes ?? null,
      owner_user_id: data.owner_user_id ?? null,
      status: retired ? ('retired' as const) : ('available' as const),
    }
    if (await run('retire', () => api.PUT('/api/equipment/{equipment_id}', { ...path, body }).then(unwrap))) changed()
  }
  async function remove() {
    if (await run('delete', () => api.DELETE('/api/equipment/{equipment_id}', path).then(unwrap))) {
      setDialog(null)
      onDeleted()
    }
  }

  const out = data.status === 'checked_out'
  const menu: MenuItem[] = [{ label: 'Print label', icon: 'print', onSelect: () => navigate(`${adminBase}/equipment/labels?ids=${id}`) }]
  if (!out) {
    menu.push(
      data.status === 'retired'
        ? { label: 'Bring back into use', icon: 'undo', onSelect: () => void setRetired(false) }
        : { label: 'Retire', icon: 'block', onSelect: () => void setRetired(true) },
    )
  }
  menu.push({ label: 'Delete…', icon: 'delete', destructive: true, disabled: out, onSelect: () => setDialog('delete') })

  return (
    <Detail
      title={
        <>
          {data.name} <span className="admin-mono admin-muted">#{data.asset_number}</span>
        </>
      }
      sub={
        <>
          Owned by {data.owner_name ?? 'the community'} · <StatusChip status={data.status} />
        </>
      }
      actions={
        <>
          {out ? (
            <Button variant="primary" size="sm" icon="login" busy={busy === 'in'} onClick={() => void checkIn()}>
              Check in
            </Button>
          ) : data.status === 'available' ? (
            <Button variant="primary" size="sm" icon="logout" onClick={() => setDialog('out')}>
              Check out
            </Button>
          ) : null}
          <Button variant="outline" size="sm" icon="edit" onClick={() => setDialog('edit')}>
            Edit
          </Button>
          <RowMenu label={`More for #${data.asset_number}`} items={menu} />
        </>
      }
    >
      {actionError && !dialog && <Banner tone="error">{actionError}</Banner>}
      {out && data.current_loan && (
        <DetailSection title="Now">
          <DetailLine icon="person" sub={data.current_loan.checked_out_at ? `Since ${formatDayTime(data.current_loan.checked_out_at, zone)}` : undefined}>
            With {data.current_loan.borrower_name ?? 'someone'}
          </DetailLine>
        </DetailSection>
      )}
      {(data.description || data.private_notes) && (
        <DetailSection title="Notes">
          {data.description && <p className="prewrap">{data.description}</p>}
          {data.private_notes && (
            <DetailLine icon="key" sub="Only staff and equipment managers see this">
              <span className="prewrap">{data.private_notes}</span>
            </DetailLine>
          )}
        </DetailSection>
      )}
      <DetailSection title="History">
        {!loans.data ? (
          <Spinner label="Loading history" />
        ) : loans.data.length === 0 ? (
          <p className="admin-muted">Never checked out.</p>
        ) : (
          loans.data.slice(0, 20).flatMap((l) => {
            const lines = []
            if (l.checked_in_at) {
              lines.push(
                <DetailLine key={`${l.id}-in`} icon="login" sub={`${formatDayTime(l.checked_in_at, zone)}${l.checked_in_by_name ? ` · by ${l.checked_in_by_name}` : ''}`}>
                  Back from {l.borrower_name ?? 'someone'}
                </DetailLine>,
              )
            }
            if (l.checked_out_at) {
              lines.push(
                <DetailLine key={`${l.id}-out`} icon="logout" sub={`${formatDayTime(l.checked_out_at, zone)}${l.checked_out_by_name ? ` · by ${l.checked_out_by_name}` : ''}`}>
                  Out to {l.borrower_name ?? 'someone'}
                </DetailLine>,
              )
            }
            return lines
          })
        )}
      </DetailSection>

      <AssetDialog open={dialog === 'edit'} onOpenChange={(o) => setDialog(o ? 'edit' : null)} asset={data} onSaved={changed} />
      <CheckoutDialog open={dialog === 'out'} onOpenChange={(o) => setDialog(o ? 'out' : null)} asset={data} onDone={changed} />
      <FormDialog
        open={dialog === 'delete'}
        onOpenChange={(o) => setDialog(o ? 'delete' : null)}
        title={`Delete #${data.asset_number}?`}
        size="sm"
        primaryLabel="Delete"
        destructive
        busy={busy === 'delete'}
        onPrimary={() => void remove()}
      >
        <p>Its loan history goes with it, and printed labels stop working. Retire it instead to keep the history.</p>
        {actionError && (
          <p className="form-error" role="alert">
            {actionError}
          </p>
        )}
      </FormDialog>
    </Detail>
  )
}

function EquipmentPage({ assets, zone, reload, base }: { assets: Asset[]; zone: string; reload: () => void; base: string }) {
  const { assetId } = useParams()
  const id = assetId ? Number(assetId) : null
  const navigate = useNavigate()
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [adding, setAdding] = useState(false)
  const shown = assets.filter((a) => matches(a, filter, search))
  const count = (f: Filter) => assets.filter((a) => f === 'all' || a.status === f).length
  const pills: [Filter, string][] = [
    ['all', `All ${count('all')}`],
    ['checked_out', `Out ${count('checked_out')}`],
    ['available', `Available ${count('available')}`],
  ]
  if (count('retired')) pills.push(['retired', `Retired ${count('retired')}`])
  const printable = shown.filter((a) => a.status !== 'retired').map((a) => a.id)

  return (
    <>
      <PageHeader
        title="Equipment"
        lede="Every console, cable and kit: who owns it and who has it now."
        actions={
          <>
            <Button variant="primary" icon="add" onClick={() => setAdding(true)}>
              Add equipment
            </Button>
            <ActionsMenu
              items={[
                {
                  label: `Print labels for these ${printable.length}…`,
                  icon: 'qr_code_2',
                  disabled: printable.length === 0,
                  onSelect: () => navigate(`${base}/labels?ids=${printable.join(',')}`),
                },
              ]}
            />
          </>
        }
      />
      <SideListLayout
        open={id !== null}
        backTo={base}
        backLabel="Equipment"
        list={
          <div className="admin-stack">
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
                placeholder="Search name, number or person"
                aria-label="Search equipment"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            {shown.length === 0 ? (
              <EmptyState icon="inventory_2" title={assets.length ? 'Nothing matches' : 'No equipment yet'} />
            ) : (
              <SideListCard label="Equipment">
                {shown.map((a) => (
                  <SideListRow
                    key={a.id}
                    to={`${base}/${a.id}`}
                    leading={<span className="asset-num admin-mono">#{a.asset_number}</span>}
                    title={a.name}
                    sub={a.status === 'checked_out' ? `With ${a.borrower_name ?? 'someone'}` : `Owned by ${a.owner_name ?? 'the community'}`}
                    end={<StatusChip status={a.status} />}
                  />
                ))}
              </SideListCard>
            )}
          </div>
        }
        detail={
          id !== null ? (
            <AssetDetail
              key={id}
              id={id}
              zone={zone}
              onChange={reload}
              onDeleted={() => {
                reload()
                navigate(base)
              }}
            />
          ) : (
            <DetailEmpty>Pick something to see who has it and where it's been.</DetailEmpty>
          )
        }
      />
      <AssetDialog
        open={adding}
        onOpenChange={setAdding}
        asset={null}
        onSaved={(newId) => {
          reload()
          if (newId !== null) navigate(`${base}/${newId}`)
        }}
      />
    </>
  )
}

/** A printable sheet of QR labels; each code opens the asset's page. */
function LabelsPage({ base }: { base: string }) {
  const [params] = useSearchParams()
  const ids = (params.get('ids') ?? '')
    .split(',')
    .map(Number)
    .filter((n) => Number.isInteger(n) && n > 0)
  const [logo, setLogo] = useState(true)
  const { data, error } = useAdminData(`equipment-labels/${ids.join(',')}/${logo}`, () =>
    api.GET('/api/admin/equipment/labels', { params: { query: { ids, logo } } }).then(unwrap),
  )
  return (
    <>
      <div className="labels-toolbar no-print">
        <Link to={base} className="admin-back">
          <Icon name="chevron_left" />
          Equipment
        </Link>
        <PageHeader
          title="Print labels"
          lede={`${ids.length} label${ids.length === 1 ? '' : 's'}. Each code opens the item's page when scanned.`}
          actions={
            <Button variant="primary" icon="print" disabled={!data} onClick={() => window.print()}>
              Print
            </Button>
          }
        />
        <label className="admin-switch labels-logo">
          <span>Community logo in the codes</span>
          <input type="checkbox" role="switch" className="switch" checked={logo} onChange={(e) => setLogo(e.target.checked)} />
        </label>
      </div>
      {error && !data ? (
        <Banner tone="error">{error.message}</Banner>
      ) : !data ? (
        <Spinner label="Making labels" />
      ) : (
        <div className="label-sheet">
          {data.assets.map((a) => (
            <div key={a.id} className="label-sheet__label">
              <img src={a.qr} alt={`QR code for #${a.asset_number}`} />
              <div>
                <b className="admin-mono">#{a.asset_number}</b>
                <span>{a.name}</span>
                <small>{data.community}</small>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

/** Admin → Equipment: every asset beside the open one, with check-out, history and labels. */
export function EquipmentScreen() {
  const { adminBase } = useAdmin()
  const base = `${adminBase}/equipment`
  const { data, error, reload } = useAdminData('equipment', () => api.GET('/api/admin/equipment').then(unwrap))
  if (error && !data) {
    return (
      <>
        <PageHeader title="Equipment" />
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if (!data) return <Spinner label="Loading equipment" />
  const sorted = [...data.assets].sort((a, b) => a.asset_number - b.asset_number)
  const zone = effectiveZone(data.zone)
  return (
    <Routes>
      <Route path="labels" element={<LabelsPage base={base} />} />
      <Route path=":assetId" element={<EquipmentPage assets={sorted} zone={zone} reload={reload} base={base} />} />
      <Route path="*" element={<EquipmentPage assets={sorted} zone={zone} reload={reload} base={base} />} />
    </Routes>
  )
}
