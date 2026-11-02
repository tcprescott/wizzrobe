import { Route, Routes } from 'react-router-dom'
import type { AdminAreaRoute } from '../kit/types'
import { MatchScheduleScreen } from './MatchScheduleScreen'

export const areas: AdminAreaRoute[] = [
  {
    slug: 'match-schedule',
    element: (
      <Routes>
        <Route index element={<MatchScheduleScreen />} />
        <Route path=":match" element={<MatchScheduleScreen />} />
      </Routes>
    ),
  },
]
