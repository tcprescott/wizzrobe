import type { AdminAreaRoute } from '../kit'
import { TournamentsScreen } from './TournamentsScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'tournaments', element: <TournamentsScreen /> }]
