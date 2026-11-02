import type { ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { About } from './About'
import { Appearance } from './Appearance'
import { Availability } from './Availability'
import { Apps, Tokens, Webhooks } from './Developer'
import { Accounts, Equipment, Feedback, StaffAlerts } from './Lists'
import { Notifications } from './Notifications'
import { Accommodation, EmergencyContact } from './Records'

const SECTIONS: Record<string, () => ReactNode> = {
  about: About,
  notifications: Notifications,
  availability: Availability,
  accounts: Accounts,
  'staff-alerts': StaffAlerts,
  equipment: Equipment,
  accommodation: Accommodation,
  'emergency-contact': EmergencyContact,
  feedback: Feedback,
  webhooks: Webhooks,
  apps: Apps,
  tokens: Tokens,
  appearance: Appearance,
}

/** The open section of Me, by its key in the URL. */
export function MeSection() {
  const { section } = useParams()
  const Body = section ? SECTIONS[section] : undefined
  return Body ? <Body key={section} /> : null
}
