import type { AdminAreaRoute } from '../kit/types'
import { FeedbackScreen } from './FeedbackScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'feedback', element: <FeedbackScreen /> }]
