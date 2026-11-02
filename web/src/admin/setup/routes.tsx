import type { AdminAreaRoute } from '../kit/types'
import { SetupScreen } from './SetupScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'setup', element: <SetupScreen /> }]
