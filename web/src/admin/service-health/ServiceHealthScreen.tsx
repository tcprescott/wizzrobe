import { api, unwrap } from '../../api/client'
import { useQuery } from '../../api/useQuery'
import { Banner, Button, IconButton, PageHeader, Spinner } from '../../components'
import { useCommunity } from '../../community/CommunityProvider'
import { useViewerZone } from '../comms/useViewerZone'
import { useAdmin } from '../kit/useAdminNav'
import { ServiceHealthBoard, type ProbeResult } from './ServiceHealthBoard'

/** Admin › Service Health: the outside services this community depends on, checked live on open. */
export function ServiceHealthScreen() {
  const { community } = useCommunity()
  const { nav, adminBase } = useAdmin()
  const zone = useViewerZone()
  const query = useQuery(`admin/service-health@${community.slug}`, () => api.GET('/api/service-health').then(unwrap))
  const integrations = nav.settings_panes.some((p) => p.slug === 'integrations')

  // Racetime bots are granted by platform admins, so only Challonge has a fix a community can make.
  const fixFor = (r: ProbeResult) =>
    r.key === 'challonge' && r.status !== 'healthy' && integrations
      ? { label: 'Reconnect in Settings › Integrations', to: `${adminBase}/settings/integrations` }
      : null

  return (
    <>
      <PageHeader
        title="Service Health"
        lede="The outside services this community depends on: its racetime bots and its Challonge connection. A credential warning means reconnect before it becomes an outage."
        actions={<IconButton icon="refresh" label="Check again" onClick={query.reload} />}
      />
      {query.loading && !query.data ? (
        <Spinner label="Checking services" />
      ) : query.error && !query.data ? (
        <Banner tone="error" action={<Button variant="outline" size="sm" onClick={query.reload}>Try again</Button>}>{query.error.message}</Banner>
      ) : (
        <ServiceHealthBoard results={query.data ?? []} zone={zone} fixFor={fixFor} />
      )}
    </>
  )
}
