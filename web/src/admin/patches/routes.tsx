import type { AdminAreaRoute } from '../kit/types'
import { PatchesScreen } from './PatchesScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'patches', element: <PatchesScreen /> }]
