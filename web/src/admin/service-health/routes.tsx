import type { AdminAreaRoute } from '../kit/types'
import { ServiceHealthScreen } from './ServiceHealthScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'service-health', element: <ServiceHealthScreen /> }]
