import { Route, Routes } from 'react-router-dom'
import type { AdminAreaRoute } from '../kit/types'
import { SettingsScreen } from './SettingsScreen'

export const areas: AdminAreaRoute[] = [
  {
    slug: 'settings',
    element: (
      <Routes>
        <Route index element={<SettingsScreen />} />
        <Route path=":pane" element={<SettingsScreen />} />
      </Routes>
    ),
  },
]
