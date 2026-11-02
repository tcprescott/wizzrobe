import { Route, Routes } from 'react-router-dom'
import type { AdminAreaRoute } from '../kit/types'
import { EventsScreen } from './EventsScreen'

export const areas: AdminAreaRoute[] = [
  {
    slug: 'events',
    element: (
      <Routes>
        <Route index element={<EventsScreen />} />
        <Route path=":event" element={<EventsScreen />} />
      </Routes>
    ),
  },
]
