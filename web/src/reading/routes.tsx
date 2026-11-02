import type { RouteObject } from 'react-router-dom'
import { AboutScreen, ChangelogScreen, FactsScreen, NoticesScreen, PrivacyScreen } from './AppScreens'
import { EventInfoArticleScreen, EventInfoIndexScreen, HelpArticleScreen, HelpIndexScreen } from './HelpScreens'

/** The website's paths under /t/<slug>, so links already sent in DMs and articles land here. */
export const readingRoutes: RouteObject[] = [
  { path: 'help', element: <HelpIndexScreen /> },
  { path: 'help/:slug', element: <HelpArticleScreen /> },
  { path: 'event-info', element: <EventInfoIndexScreen /> },
  { path: 'event-info/:slug', element: <EventInfoArticleScreen /> },
  { path: 'changelog', element: <ChangelogScreen /> },
  { path: 'about', element: <AboutScreen /> },
  { path: 'about/privacy', element: <PrivacyScreen /> },
  { path: 'about/notices', element: <NoticesScreen /> },
  { path: 'facts', element: <FactsScreen /> },
]
