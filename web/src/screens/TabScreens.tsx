import { EmptyState, PageHeader, Panel } from '../components'
import { useCommunity } from '../community/CommunityProvider'

export function EventScreen() {
  const { event } = useCommunity()
  return (
    <>
      <PageHeader title={event?.name ?? 'Event'} lede="Event info, brackets and Watch." />
      <Panel>
        <EmptyState icon="stadium" title="Event info, brackets and Watch move here after the player screens" />
      </Panel>
    </>
  )
}
