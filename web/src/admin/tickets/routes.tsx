import type { AdminAreaRoute } from '../kit'
import { TicketsScreen } from './TicketsScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'tickets', element: <TicketsScreen /> }]
