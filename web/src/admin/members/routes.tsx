import type { AdminAreaRoute } from '../kit/types'
import { MembersScreen } from './MembersScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'members', element: <MembersScreen /> }]
