import type { ReactNode } from 'react'
import { useCommunity } from '../../community/CommunityProvider'
import { PaneScreen, useAdmin } from '../kit'
import { DatesAndHours } from './panes/Dates'
import { DiscordRoles } from './panes/DiscordRoles'
import { Capacity, Crew, EventClock, RewardTiers } from './panes/Simple'
import { Venue } from './panes/Venue'

const PANES: Partial<Record<string, () => ReactNode>> = {
  dates: () => <DatesAndHours />,
  capacity: () => <Capacity />,
  venue: () => <Venue />,
  timezone: () => <EventClock />,
  crew: () => <Crew />,
  'reward-tiers': () => <RewardTiers />,
  'discord-roles': () => <DiscordRoles />,
}

/** Admin → Event settings: what is set again for each event, as a side list of panes like Settings. */
export function EventSettingsScreen() {
  const { nav, adminBase } = useAdmin()
  const { event } = useCommunity()
  return (
    <PaneScreen
      title="Event settings"
      lede={event ? `Settings for ${event.name} only` : 'Settings for this event only'}
      panes={nav.event_settings_panes}
      base={`${adminBase}/event-settings`}
      render={PANES}
    />
  )
}
