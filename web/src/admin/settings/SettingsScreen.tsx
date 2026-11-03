import type { ReactNode } from 'react'
import { useCommunity } from '../../community/CommunityProvider'
import { PaneScreen, useAdmin } from '../kit'
import { ConnectedApps } from './panes/ConnectedApps'
import { Features } from './panes/Features'
import { General } from './panes/General'
import { Meetups, Volunteers } from './panes/Lead'
import { Members } from './panes/Members'
import { Watch } from './panes/Watch'

const PANES: Partial<Record<string, () => ReactNode>> = {
  general: () => <General />,
  members: () => <Members />,
  watch: () => <Watch />,
  volunteers: () => <Volunteers />,
  'community-events': () => <Meetups />,
  apps: () => <ConnectedApps />,
  features: () => <Features />,
}

/** Admin → Settings: the community's panes as a side list, each a stack of cards that save on their own. */
export function SettingsScreen() {
  const { nav, adminBase } = useAdmin()
  const { community } = useCommunity()
  return (
    <PaneScreen
      title="Settings"
      lede={`Community-wide settings for ${community.name}`}
      panes={nav.settings_panes}
      base={`${adminBase}/settings`}
      render={PANES}
    />
  )
}
