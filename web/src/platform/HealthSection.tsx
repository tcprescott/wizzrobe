import { useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Button, Chip, PageHeader, Spinner, type Tone } from '../components'
import { DataTable, type Column } from '../admin/kit'
import { LoadError } from './LoadError'
import { useNotice, usePlatformQuery } from './parts'

type Probe = Schemas['ProbeResultResponse']

const TONE: Record<string, Tone> = { healthy: 'ok', mock: 'info', degraded: 'pending', credential_warning: 'pending', down: 'cancelled', unknown: 'neutral' }
const LABEL: Record<string, string> = { healthy: 'Healthy', mock: 'Mock', degraded: 'Degraded', credential_warning: 'Credential warning', down: 'Down', unknown: 'Unknown' }

function rank(p: Probe): number {
  return p.status === 'down' ? 0 : p.status === 'degraded' || p.status === 'credential_warning' ? 1 : 2
}

const COLUMNS: Column<Probe>[] = [
  { key: 'label', header: 'Service', primary: true, cell: (p) => p.label },
  {
    key: 'status',
    header: 'Status',
    cell: (p) => <Chip tone={TONE[p.status] ?? 'neutral'}>{LABEL[p.status] ?? p.status}</Chip>,
  },
  { key: 'message', header: 'What the check found', cell: (p) => p.message },
]

// Stand-in until the shared admin Service Health board lands; it takes the same rows.
/** Every outside service Wizzrobe depends on, across the platform, problems first. */
export function HealthSection() {
  const { data, error, loading, reload } = usePlatformQuery('service-health', () => api.GET('/api/service-health/board').then(unwrap))
  const [rows, setRows] = useState<Probe[] | null>(null)
  const [busy, setBusy] = useState(false)
  const { banner, run } = useNotice()
  const shown = [...(rows ?? data ?? [])].sort((a, b) => rank(a) - rank(b))

  return (
    <>
      <PageHeader
        title="Service health"
        lede="Checked every few minutes. Check now runs every check straight away."
        actions={
          <Button
            icon="refresh"
            busy={busy}
            busyLabel="Checking…"
            onClick={async () => {
              setBusy(true)
              await run(async () => setRows(await api.POST('/api/service-health/refresh').then(unwrap)))
              setBusy(false)
            }}
          >
            Check now
          </Button>
        }
      />
      {banner}
      {error && !data ? (
        <LoadError error={error} reload={reload} />
      ) : loading && !data ? (
        <Spinner label="Loading service health" />
      ) : (
        <DataTable label="Services" columns={COLUMNS} rows={shown} rowKey={(p) => p.key} empty={{ icon: 'monitor_heart', title: 'No checks have run yet' }} />
      )}
    </>
  )
}
