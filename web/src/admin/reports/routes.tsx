import { Route, Routes } from 'react-router-dom'
import type { AdminAreaRoute } from '../kit/types'
import { ReportsScreen } from './ReportsScreen'

export const areas: AdminAreaRoute[] = [
  {
    slug: 'reports',
    element: (
      <Routes>
        <Route index element={<ReportsScreen />} />
        <Route path=":report" element={<ReportsScreen />} />
      </Routes>
    ),
  },
]
