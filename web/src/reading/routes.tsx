import type { RouteObject } from 'react-router-dom'
import { AboutScreen, ChangelogScreen, FactsScreen, NoticesScreen, PrivacyScreen } from './AppScreens'
import { GuideScreen } from './GuideScreen'
import { DoorSeedScreen } from '../patcher/DoorSeedScreen'
import { PatcherScreen } from '../patcher/PatcherScreen'
import { PatchScreen } from '../patcher/PatchScreen'
import { EventInfoArticleScreen, EventInfoIndexScreen, HelpArticleScreen, HelpIndexScreen } from './HelpScreens'

/** The website's paths under /t/<slug>, so links already sent in DMs and articles land here. A door seed or patch link is its own secret, so those open signed out too. */
export const readingRoutes: RouteObject[] = [
  { path: 'help', element: <HelpIndexScreen /> },
  { path: 'help/:slug', element: <HelpArticleScreen /> },
  { path: 'event-info', element: <EventInfoIndexScreen /> },
  { path: 'event-info/guide', element: <GuideScreen /> },
  { path: 'event-info/:slug', element: <EventInfoArticleScreen /> },
  { path: 'changelog', element: <ChangelogScreen /> },
  { path: 'about', element: <AboutScreen /> },
  { path: 'about/privacy', element: <PrivacyScreen /> },
  { path: 'about/notices', element: <NoticesScreen /> },
  { path: 'facts', element: <FactsScreen /> },
  { path: 'door/:token', element: <DoorSeedScreen /> },
  { path: 'patch/:token', element: <PatchScreen /> },
  { path: 'patcher', element: <PatcherScreen /> },
]

const READING = new RegExp(`^/t/[^/]+(?:/e/[^/]+)?/(?:${['help', 'event-info', 'changelog', 'about', 'facts', 'door', 'patch', 'patcher'].join('|')})(?:/|$)`)

/** Whether `pathname` is a reading page, which opens signed out. */
export function isReadingPath(pathname: string): boolean {
  return READING.test(pathname)
}
