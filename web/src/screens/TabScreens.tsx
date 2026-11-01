import { Banner, EmptyState, PageHeader, Panel } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { useMe } from './useMe'

const BUILDING = 'This is the new Wizzrobe app, still being built. Everything you need is still on the current site for now.'

export function HomeScreen() {
  const { community, event } = useCommunity()
  const { me } = useMe()
  const name = me?.display_name || me?.username
  return (
    <>
      <PageHeader title={name ? `Hi, ${name}` : 'Home'} lede={[community.name, event?.name].filter(Boolean).join(' · ')} />
      <Banner tone="accent">{BUILDING}</Banner>
      <Panel title="Needs you">
        <EmptyState icon="check_circle" title="Picking a time, confirming and checking in will show here" />
      </Panel>
      <Panel title="Today">
        <EmptyState icon="event" title="Your matches, crew slots and shifts for today will show here" />
      </Panel>
    </>
  )
}

export function ScheduleScreen() {
  return (
    <>
      <PageHeader title="Schedule" lede="Your matches and the event's run sheet." />
      <Panel>
        <EmptyState icon="calendar_month" title="The schedule moves here next" />
      </Panel>
    </>
  )
}

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

const ROLE_AREAS: Record<string, string> = {
  staff: 'Admin',
  proctor: 'Proctor station',
  volunteer: 'Volunteer shifts',
  volunteer_coordinator: 'Volunteer coordination',
  check_in_desk: 'Check-in desk',
  stream_manager: 'Broadcast tech',
  equipment_manager: 'Equipment',
  triforce_submitter: 'Triforce texts',
  preset_manager: 'Seed presets',
  sync_admin: 'Sync',
  qualifier_admin: 'Qualifiers',
}

export function WorkScreen() {
  const { community } = useCommunity()
  const areas = community.roles.map((role) => ROLE_AREAS[role]).filter((area): area is string => Boolean(area))
  return (
    <>
      <PageHeader title="Work" lede="Everything your roles in this community let you do." />
      <Panel title="Your areas">
        {areas.length === 0 ? (
          <EmptyState icon="badge" title="No roles here yet" />
        ) : (
          areas.map((area) => (
            <div key={area} className="list-link">
              <span>
                {area}
                <span className="list-link__sub">Moves here after the player screens</span>
              </span>
            </div>
          ))
        )}
      </Panel>
    </>
  )
}
