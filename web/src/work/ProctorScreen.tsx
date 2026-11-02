import { EmptyState, PageHeader, Panel } from '../components'

/** Proctor Station lands in the app with the Event tab's run sheet; until then it says so. */
export function ProctorScreen() {
  return (
    <>
      <PageHeader title="Proctor Station" />
      <Panel>
        <EmptyState icon="sports_esports" title="Proctor Station comes to the app next">
          Until then, run matches from Proctor Station on the website.
        </EmptyState>
      </Panel>
    </>
  )
}
