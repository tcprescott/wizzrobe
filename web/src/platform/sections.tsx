import type { ReactNode } from 'react'
import type { IconName } from '../components'
import { AppsSection } from './AppsSection'
import { BotsSection } from './BotsSection'
import { CommunitiesSection } from './CommunitiesSection'
import { FeatureGroupsSection } from './FeatureGroupsSection'
import { HealthSection } from './HealthSection'
import { LoggingSection } from './LoggingSection'
import { MonitoringSection } from './MonitoringSection'
import { UsersSection } from './UsersSection'

export interface PlatformSection {
  key: string
  label: string
  icon: IconName
  group: string
  sub: string
  element: ReactNode
}

/** The /platform page's eight tabs, as sections; `key` is the `/platform/<key>` path and today's `?tab=`. */
export const SECTIONS: PlatformSection[] = [
  { key: 'communities', label: 'Communities', icon: 'groups', group: 'Communities and people', sub: 'Names, addresses, first staff, features', element: <CommunitiesSection /> },
  { key: 'users', label: 'Users', icon: 'person', group: 'Communities and people', sub: 'Every account, and switching one off', element: <UsersSection /> },
  { key: 'features', label: 'Feature groups', icon: 'toggle_on', group: 'Communities and people', sub: 'Tiers of features a community may use', element: <FeatureGroupsSection /> },
  { key: 'bots', label: 'Racetime bots', icon: 'smart_toy', group: 'Connections', sub: 'Bot accounts and who may use them', element: <BotsSection /> },
  { key: 'apps', label: 'Apps', icon: 'apps', group: 'Connections', sub: 'Apps people registered to sign in', element: <AppsSection /> },
  { key: 'health', label: 'Service health', icon: 'monitor_heart', group: 'Operations', sub: 'Outside services Wizzrobe depends on', element: <HealthSection /> },
  { key: 'logging', label: 'Logging', icon: 'tune', group: 'Operations', sub: 'How much each part of the server logs', element: <LoggingSection /> },
  { key: 'monitoring', label: 'Monitoring', icon: 'bug_report', group: 'Operations', sub: 'Browser errors sent to Sentry', element: <MonitoringSection /> },
]
