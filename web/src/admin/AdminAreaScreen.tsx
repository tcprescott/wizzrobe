import { useParams } from 'react-router-dom'
import { EmptyState, PageHeader } from '../components'
import { AREA_ROUTES } from './kit/registry'
import { useAdmin } from './kit/useAdminNav'

/** Whichever area `/admin/<slug>` names, as its folder registered it. */
export function AdminAreaScreen() {
  const { area: slug } = useParams()
  const { nav } = useAdmin()
  const info = nav.areas.find((a) => a.slug === slug)
  if (!info) {
    return (
      <EmptyState icon="admin_panel_settings" title="You can't open that admin area here">
        It may be switched off for this community, or need a role you don't have.
      </EmptyState>
    )
  }
  const route = AREA_ROUTES.get(info.slug)
  if (!route) {
    return (
      <>
        <PageHeader title={info.label} />
        <EmptyState icon="info" title={`${info.label} isn't in the app yet`}>
          Use the website for this one for now.
        </EmptyState>
      </>
    )
  }
  return <>{route.element}</>
}
