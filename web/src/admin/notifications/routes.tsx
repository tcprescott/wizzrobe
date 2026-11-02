import type { AdminAreaRoute } from '../kit/types'
import { NotificationsScreen } from './NotificationsScreen'

export const areas: AdminAreaRoute[] = [{ slug: 'notifications', element: <NotificationsScreen /> }]
