import { createBrowserRouter, Navigate, RouterProvider, type RouteObject } from 'react-router-dom'
import { AdminAreaScreen } from './admin/AdminAreaScreen'
import { AdminLayout } from './admin/AdminLayout'
import { AuthProvider, RequireAuth } from './auth/AuthProvider'
import { TooltipProvider } from './components'
import { CallbackScreen } from './screens/CallbackScreen'
import { CommunityIndex } from './screens/CommunityIndex'
import { KitScreen } from './screens/KitScreen'
import { PlatformShell } from './platform/PlatformShell'
import { MeLayout } from './me/MeLayout'
import { MeSection } from './me/sections'
import { NotFoundScreen } from './screens/NotFoundScreen'
import { SignInScreen } from './screens/SignInScreen'
import { HomeScreen } from './player/HomeScreen'
import { ScheduleScreen } from './player/ScheduleScreen'
import { EventScreen, WorkScreen } from './screens/TabScreens'
import { AppShell } from './shell/AppShell'
import { ReadingFrame } from './reading/ReadingFrame'
import { readingRoutes } from './reading/routes'
import { ThemeProvider } from './theme/ThemeProvider'
import { startNativeBridge } from './native/bridge'

const tabRoutes: RouteObject[] = [
  { index: true, element: <Navigate to="home" replace /> },
  { path: 'home', element: <HomeScreen /> },
  { path: 'schedule', element: <ScheduleScreen /> },
  { path: 'event', element: <EventScreen /> },
  { path: 'work', element: <WorkScreen /> },
  { path: 'me', element: <MeLayout />, children: [{ path: ':section', element: <MeSection /> }] },
  { path: 'admin', element: <AdminLayout />, children: [{ path: ':area/*', element: <AdminAreaScreen /> }] },
  ...readingRoutes,
]

// The reading pages (help, event info…) open signed out too; ReadingFrame picks the frame.
const shell = (
  <ReadingFrame>
    <AppShell />
  </ReadingFrame>
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
  {
    path: '/platform/:section?',
    element: (
      <RequireAuth>
        <PlatformShell />
      </RequireAuth>
    ),
  },
  { path: '/t/:community', element: shell, children: tabRoutes },
  { path: '/t/:community/e/:event', element: shell, children: tabRoutes },
  { path: '*', element: <NotFoundScreen /> },
]

const router = createBrowserRouter(routes)
startNativeBridge((to) => void router.navigate(to))

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
