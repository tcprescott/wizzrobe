import { createBrowserRouter, Navigate, RouterProvider, type RouteObject } from 'react-router-dom'
import { AdminAreaScreen } from './admin/AdminAreaScreen'
import { AdminLayout } from './admin/AdminLayout'
import { AuthProvider, RequireAuth } from './auth/AuthProvider'
import { TooltipProvider } from './components'
import { CommunityProvider } from './community/CommunityProvider'
import { CallbackScreen } from './screens/CallbackScreen'
import { CommunityIndex } from './screens/CommunityIndex'
import { CommunityMissing } from './screens/CommunityMissing'
import { KitScreen } from './screens/KitScreen'
import { MeLayout } from './me/MeLayout'
import { MeSection } from './me/sections'
import { NotFoundScreen } from './screens/NotFoundScreen'
import { SignInScreen } from './screens/SignInScreen'
import { HomeScreen } from './player/HomeScreen'
import { ScheduleScreen } from './player/ScheduleScreen'
import { EventScreen } from './screens/TabScreens'
import { DeskScreen } from './work/DeskScreen'
import { MyAvailabilityScreen } from './work/MyAvailabilityScreen'
import { ProctorScreen } from './work/ProctorScreen'
import { VolunteerScheduleScreen } from './work/VolunteerScheduleScreen'
import { WorkLayout } from './work/WorkLayout'
import { AppShell } from './shell/AppShell'
import { ThemeProvider } from './theme/ThemeProvider'

const tabRoutes: RouteObject[] = [
  { index: true, element: <Navigate to="home" replace /> },
  { path: 'home', element: <HomeScreen /> },
  { path: 'schedule', element: <ScheduleScreen /> },
  { path: 'event', element: <EventScreen /> },
  {
    path: 'work',
    element: <WorkLayout />,
    children: [
      { path: 'desk', element: <DeskScreen /> },
      { path: 'desk/:eventId', element: <DeskScreen /> },
      { path: 'schedule', element: <VolunteerScheduleScreen /> },
      { path: 'availability', element: <MyAvailabilityScreen /> },
      { path: 'proctor', element: <ProctorScreen /> },
    ],
  },
  { path: 'me', element: <MeLayout />, children: [{ path: ':section', element: <MeSection /> }] },
  { path: 'admin', element: <AdminLayout />, children: [{ path: ':area/*', element: <AdminAreaScreen /> }] },
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
