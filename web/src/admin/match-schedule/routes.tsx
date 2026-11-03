import { Route, Routes } from 'react-router-dom'
import type { AdminAreaRoute } from '../kit/types'
import { MatchScheduleScreen } from './MatchScheduleScreen'

export const areas: AdminAreaRoute[] = [
  {
    slug: 'match-schedule',
    element: (
      <Routes>
        <Route index element={<MatchScheduleScreen />} />
        <Route path="new" element={<MatchScheduleScreen mode="new" />} />
        <Route path="unbooked/:source/:key" element={<MatchScheduleScreen mode="unbooked" />} />
        <Route path=":match" element={<MatchScheduleScreen />} />
        <Route path=":match/edit" element={<MatchScheduleScreen mode="edit" />} />
      </Routes>
    ),
  },
]
