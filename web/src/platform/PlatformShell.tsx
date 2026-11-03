import { Link, Navigate, NavLink, useParams } from 'react-router-dom'
import { Banner, Button, EmptyState, Icon, PageHeader, Spinner } from '../components'
import { SectionNav, type SectionGroup } from '../admin/kit'
import { usePhone } from '../lib/usePhone'
import { usePlatformAccess } from './access'
import { SECTIONS } from './sections'
import '../admin/kit/admin.css'
import '../shell/shell.css'
import './platform.css'

function groups(): SectionGroup[] {
  const out: SectionGroup[] = []
  for (const s of SECTIONS) {
    let group = out.find((g) => g.title === s.group)
    if (!group) out.push((group = { title: s.group, items: [] }))
    group.items.push({ key: s.key, label: s.label, icon: s.icon, to: `/platform/${s.key}`, sub: s.sub })
  }
  return out
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="shell shell--platform">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="shell__main">
        <header className="topbar">
          <span className="topbar__title">Platform</span>
          <Link to="/" className="picker pf-back-pill">
            <Icon name="arrow_back" size={18} />
            <span className="picker__text">Communities</span>
          </Link>
        </header>
        <main id="main" className="shell__content pf-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  )
}

/**
 * `/platform`, outside any community: the eight sections as a side list,
 * beside the open section on desktop, list then section screen on a phone.
 * Super admins only; the server refuses everyone else whatever this shows.
 */
export function PlatformShell() {
  const { section } = useParams()
  const phone = usePhone()
  const access = usePlatformAccess()
  const open = SECTIONS.find((s) => s.key === section)

  if (access.loading && !access.data) {
    return (
      <Frame>
        <Spinner label="Checking your access" />
      </Frame>
    )
  }
  if (access.error) {
    return (
      <Frame>
        <Banner tone="error" action={<Button variant="outline" onClick={access.reload}>Try again</Button>}>
          {access.error.message}
        </Banner>
      </Frame>
    )
  }
  if (!access.data?.super_admin) {
    return (
      <Frame>
        <EmptyState icon="admin_panel_settings" title="Platform administration is for super admins">
          Your account doesn't have that role.
        </EmptyState>
      </Frame>
    )
  }
  if (section && !open) return <Navigate to="/platform" replace />
  if (!section && !phone) return <Navigate to={`/platform/${SECTIONS[0]!.key}`} replace />

  if (phone) {
    return (
      <Frame>
        {open ? (
          <>
            <NavLink to="/platform" end className="admin-back">
              <Icon name="chevron_left" size={20} />
              Platform
            </NavLink>
            {open.element}
          </>
        ) : (
          <>
            <PageHeader title="Platform" lede="Every community and account, across Wizzrobe." />
            <SectionNav groups={groups()} label="Platform sections" />
          </>
        )}
      </Frame>
    )
  }

  return (
    <Frame>
      <div className="admin-split">
        <SectionNav groups={groups()} label="Platform sections" compact />
        <div className="admin-pane" key={open?.key}>
          {open?.element}
        </div>
      </div>
    </Frame>
  )
}
