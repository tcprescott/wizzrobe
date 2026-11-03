import type { AdminAreaRoute } from '../kit/types'
import { CheckInRosterScreen } from './CheckInRosterScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'check-in-roster', element: <CheckInRosterScreen /> }]
