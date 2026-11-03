import type { RouteObject } from 'react-router-dom'
import { AboutScreen, ChangelogScreen, FactsScreen, NoticesScreen, PrivacyScreen } from './AppScreens'
import { GuideScreen } from './GuideScreen'
import { EventInfoArticleScreen, EventInfoIndexScreen, HelpArticleScreen, HelpIndexScreen } from './HelpScreens'

/** The website's paths under /t/<slug>, so links already sent in DMs and articles land here. */
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
]

const READING = new RegExp(`^/t/[^/]+(?:/e/[^/]+)?/(?:${['help', 'event-info', 'changelog', 'about', 'facts'].join('|')})(?:/|$)`)

/** Whether `pathname` is a reading page, which opens signed out. */
export function isReadingPath(pathname: string): boolean {
  return READING.test(pathname)
}
