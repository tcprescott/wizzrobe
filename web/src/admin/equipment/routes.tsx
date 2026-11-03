import type { AdminAreaRoute } from '../kit/types'
import { EquipmentScreen } from './EquipmentScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'equipment', element: <EquipmentScreen /> }]
