import { createBrowserRouter, Navigate, RouterProvider, type RouteObject } from 'react-router-dom'
import { AuthProvider, RequireAuth } from './auth/AuthProvider'
import { TooltipProvider } from './components'
import { CommunityProvider } from './community/CommunityProvider'
import { CallbackScreen } from './screens/CallbackScreen'
import { CommunityIndex } from './screens/CommunityIndex'
import { CommunityMissing } from './screens/CommunityMissing'
import { KitScreen } from './screens/KitScreen'
import { MeScreen } from './screens/MeScreen'
import { NotFoundScreen } from './screens/NotFoundScreen'
import { SignInScreen } from './screens/SignInScreen'
import { EventScreen, HomeScreen, ScheduleScreen, WorkScreen } from './screens/TabScreens'
import { AppShell } from './shell/AppShell'
import { ThemeProvider } from './theme/ThemeProvider'

const tabRoutes: RouteObject[] = [
  { index: true, element: <Navigate to="home" replace /> },
  { path: 'home', element: <HomeScreen /> },
  { path: 'schedule', element: <ScheduleScreen /> },
  { path: 'event', element: <EventScreen /> },
  { path: 'work', element: <WorkScreen /> },
  { path: 'me', element: <MeScreen /> },
]

const shell = (
  <RequireAuth>
    <CommunityProvider fallback={(state) => <CommunityMissing {...state} />}>
      <AppShell />
    </CommunityProvider>
  </RequireAuth>
)

// /t/<slug>/… and /t/<slug>/e/<event>/… keep the shape links in sent DMs already use.
export const routes: RouteObject[] = [
  { path: '/sign-in', element: <SignInScreen /> },
  { path: '/auth/callback', element: <CallbackScreen /> },
  { path: '/kit', element: <KitScreen /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <CommunityIndex />
      </RequireAuth>
    ),
  },
  { path: '/t/:community', element: shell, children: tabRoutes },
  { path: '/t/:community/e/:event', element: shell, children: tabRoutes },
  { path: '*', element: <NotFoundScreen /> },
]

const router = createBrowserRouter(routes)

export function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <TooltipProvider>
          <RouterProvider router={router} />
        </TooltipProvider>
      </AuthProvider>
    </ThemeProvider>
  )
}
