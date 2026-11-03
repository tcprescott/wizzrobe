import { Route, Routes } from 'react-router-dom'
import type { AdminAreaRoute } from '../kit/types'
import { SurveyPage } from './SurveyPage'
import { SurveysScreen } from './SurveysScreen'

export const areas: AdminAreaRoute[] = [
  {
    slug: 'surveys',
    element: (
      <Routes>
        <Route index element={<SurveysScreen />} />
        <Route path=":survey/*" element={<SurveyPage />} />
      </Routes>
    ),
  },
]
