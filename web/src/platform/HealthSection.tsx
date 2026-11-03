import { useState } from 'react'
import { api, unwrap } from '../api/client'
import { Button, PageHeader, Spinner } from '../components'
import { ServiceHealthBoard, type ProbeResult } from '../admin/service-health'
import { effectiveZone } from '../lib/time'
import { LoadError } from './LoadError'
import { useNotice, usePlatformQuery } from './parts'

const HEALTHY = new Set(['healthy', 'mock'])

const fixFor = (r: ProbeResult) =>
  r.key === 'racetime_bots' && !HEALTHY.has(r.status) ? { label: 'Open Racetime bots', to: '/platform/bots' } : null

/** Every outside service Wizzrobe depends on, across the platform, problems first. */
export function HealthSection() {
  const { data, error, loading, reload } = usePlatformQuery('service-health', () => api.GET('/api/service-health/board').then(unwrap))
  const [rows, setRows] = useState<ProbeResult[] | null>(null)
  const [busy, setBusy] = useState(false)
  const { banner, run } = useNotice()
  // Platform sits outside any community, so times read in the device's zone.
  const zone = effectiveZone(undefined)

  const checkNow = (
    <Button
      icon="refresh"
      variant="outline"
      size="sm"
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
  )

  return (
    <>
      <PageHeader title="Service health" lede="Checked every few minutes. Check now runs every check straight away." />
      {banner}
      {error && !data ? (
        <LoadError error={error} reload={reload} />
      ) : loading && !data ? (
        <Spinner label="Loading service health" />
      ) : (
        <ServiceHealthBoard results={rows ?? data ?? []} zone={zone} fixFor={fixFor} actions={checkNow} />
      )}
    </>
  )
}
