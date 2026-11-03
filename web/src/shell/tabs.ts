import type { IconName } from '../components'

export type TabKey = 'home' | 'schedule' | 'event' | 'work' | 'me'

export interface Tab {
  key: TabKey
  label: string
  icon: IconName
}

/** Shell A: the same five on phone (bottom tabs) and desktop (left rail). */
export const TABS: Tab[] = [
  { key: 'home', label: 'Home', icon: 'home' },
  { key: 'schedule', label: 'Schedule', icon: 'calendar_month' },
  { key: 'event', label: 'Event', icon: 'stadium' },
  { key: 'work', label: 'Work', icon: 'badge' },
  { key: 'me', label: 'Me', icon: 'person' },
]

/** Work holds whatever roles you have; someone with none sees four tabs. */
export function visibleTabs(hasWork: boolean): Tab[] {
  return TABS.filter((tab) => tab.key !== 'work' || hasWork)
}
