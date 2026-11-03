import type { AdminAreaRoute } from '../kit/types'
import { MeetupsScreen } from './MeetupsScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'meetups', element: <MeetupsScreen /> }]
