import { Route, Routes } from 'react-router-dom'
import type { AdminAreaRoute } from '../kit/types'
import { EventSettingsScreen } from './EventSettingsScreen'

export const areas: AdminAreaRoute[] = [
  {
    slug: 'event-settings',
    element: (
      <Routes>
        <Route index element={<EventSettingsScreen />} />
        <Route path=":pane" element={<EventSettingsScreen />} />
      </Routes>
    ),
  },
]
