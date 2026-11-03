import { NavLink, Navigate, Outlet, useLocation } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { Banner, Button, EmptyState, Icon, PageHeader, Spinner, type IconName } from '../components'
import { createContext, useContext } from 'react'
import { useCommunity } from '../community/CommunityProvider'
import { usePhone, useSection, type Profile } from './data'
import './me.css'

const ICON: Record<string, IconName> = {
  about: 'person',
  tickets: 'local_activity',
  notifications: 'notifications',
  availability: 'event_busy',
  accounts: 'link',
  'staff-alerts': 'campaign',
  equipment: 'inventory_2',
  accommodation: 'accessible',
  'emergency-contact': 'contact_emergency',
  feedback: 'feedback',
  webhooks: 'webhook',
  apps: 'apps',
  tokens: 'key',
  appearance: 'palette',
}

/** Sections every member has, whatever the community runs: they live in the app itself. */
const APP_SECTIONS = [{ key: 'appearance', title: 'Appearance and sign out', summary: 'Theme, and signing out of this device' }]

/** The reading pages, which open in their own screens rather than beside the list. */
const HELP_LINKS: { path: string; icon: IconName; title: string; summary: string }[] = [
  { path: 'help', icon: 'help', title: 'Help', summary: 'How the app works' },
  { path: 'changelog', icon: 'release_alert', title: "What's new", summary: 'The bigger changes, week by week' },
  { path: 'about', icon: 'info', title: 'About Wizzrobe', summary: 'Privacy policy, notices, version' },
]

interface MeContext {
  profile: Profile
  reload: () => void
}

const Ctx = createContext<MeContext | null>(null)

export function useProfile(): MeContext {
  const value = useContext(Ctx)
  if (!value) throw new Error('useProfile outside the Me screen')
  return value
}

/**
 * Me, layout B ("settings list"): every section as a row with a one-line
 * summary. A phone opens a section as its own screen with a back link; a
 * desktop keeps the list on the left and opens the section beside it.
 */
export function MeLayout() {
  const { base } = useCommunity()
  const section = useLocation().pathname.match(/\/me\/([^/]+)/)?.[1]
  const phone = usePhone()
  const { data, error, loading, reload } = useSection('profile', () => api.GET('/api/me/profile').then(unwrap))

  if (error && !data) {
    return (
      <>
        <PageHeader title="Me" />
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if (loading && !data) return <Spinner label="Loading your profile" />
  if (!data) return null

  const sections = [...data.sections, ...APP_SECTIONS]
  if (!phone && !section) return <Navigate to={`${base}/me/about`} replace />
  if (section && !sections.some((s) => s.key === section)) {
    return (
      <EmptyState icon="info" title="That part of your profile isn't here">
        This community doesn't use it. <NavLink to={`${base}/me`}>Back to Me</NavLink>
      </EmptyState>
    )
  }

  const settings = (
    <nav className="me-list" aria-label="Your profile">
      {sections.map((s) => (
        <NavLink key={s.key} to={`${base}/me/${s.key}`} className="me-list__row">
          <Icon name={ICON[s.key] ?? 'info'} />
          <span className="me-list__text">
            <span className="me-list__title">{s.title}</span>
            <span className="me-list__sub">{s.summary}</span>
          </span>
          <Icon name="chevron_right" />
        </NavLink>
      ))}
    </nav>
  )
  const list = (
    <div className="me-lists">
      {settings}
      <nav className="me-list" aria-label="Help and info">
        {HELP_LINKS.map((l) => (
          <NavLink key={l.path} to={`${base}/${l.path}`} className="me-list__row">
            <Icon name={l.icon} />
            <span className="me-list__text">
              <span className="me-list__title">{l.title}</span>
              <span className="me-list__sub">{l.summary}</span>
            </span>
            <Icon name="chevron_right" />
          </NavLink>
        ))}
      </nav>
    </div>
  )
  const current = sections.find((s) => s.key === section)

  return (
    <Ctx.Provider value={{ profile: data, reload }}>
      {phone && section ? (
        <>
          <NavLink to={`${base}/me`} className="me-back">
            <Icon name="chevron_left" size={20} />
            Me
          </NavLink>
          <PageHeader title={current?.title ?? ''} />
          <Outlet />
        </>
      ) : (
        <>
          <PageHeader title="Me" lede={data.preferred_name} />
          {phone ? (
            list
          ) : (
            <div className="me-split">
              {list}
              <div className="me-pane" aria-label={current?.title}>
                <h2 className="me-pane__title">{current?.title}</h2>
                <Outlet />
              </div>
            </div>
          )}
        </>
      )}
    </Ctx.Provider>
  )
}
