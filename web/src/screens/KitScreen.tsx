import { useState } from 'react'
import {
  ActionsMenu,
  Banner,
  Button,
  Chip,
  CountBadge,
  EmptyState,
  FormDialog,
  IconButton,
  PageHeader,
  Panel,
  PanelRow,
  RowMenu,
} from '../components'
import { useTheme } from '../theme/ThemeProvider'
import './screens.css'

const noop = () => undefined

/**
 * Every base component in every state, on one page. Not linked from the app;
 * it's what the CI accessibility scan checks in light and dark, and where to
 * look when changing a component.
 */
export function KitScreen() {
  const [open, setOpen] = useState(false)
  const { resolved, setChoice } = useTheme()
  return (
    <main className="shell__content">
      <PageHeader
        title="Components"
        lede="The Studio base components, as the new app draws them."
        actions={
          <>
            <ActionsMenu items={[{ label: 'Export', icon: 'event', onSelect: noop }, { label: 'Delete everything', destructive: true, onSelect: noop }]} />
            <Button onClick={() => setChoice(resolved === 'dark' ? 'light' : 'dark')}>
              {resolved === 'dark' ? 'Light mode' : 'Dark mode'}
            </Button>
          </>
        }
      />
      <Banner tone="info">An info banner, for something worth knowing.</Banner>
      <Banner tone="warning">A warning banner, for something that needs care.</Banner>
      <Banner tone="error">An error banner, for something that failed.</Banner>
      <Banner tone="accent" action={<Button size="sm" variant="outline">Act</Button>}>
        An accent banner with an action.
      </Banner>
      <Panel title="Buttons">
        <PanelRow>
          <div className="kit-grid">
            <Button>Primary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Delete</Button>
            <Button disabled>Disabled</Button>
            <Button busy busyLabel="Saving…">
              Save
            </Button>
            <Button size="sm" icon="check">
              Small
            </Button>
            <IconButton icon="notifications" label="Notifications" />
            <RowMenu
              label="More for this row"
              items={[
                { label: 'Edit', onSelect: noop },
                { label: 'Delete', destructive: true, onSelect: noop },
                { label: 'Copy link', onSelect: noop },
              ]}
            />
          </div>
        </PanelRow>
      </Panel>
      <Panel title="Status">
        <PanelRow>
          <div className="kit-grid">
            <Chip tone="ok">Approved</Chip>
            <Chip tone="pending">Confirm</Chip>
            <Chip tone="live">Live</Chip>
            <Chip tone="neutral">Upcoming</Chip>
            <Chip tone="cancelled">Cancelled</Chip>
            <Chip tone="info">Info</Chip>
            <CountBadge count={2} label="2 need you" />
          </div>
        </PanelRow>
      </Panel>
      <Panel title="Dialog" actions={<Button size="sm" onClick={() => setOpen(true)}>Open dialog</Button>}>
        <EmptyState icon="info" title="An empty state">
          Explains what will show here and how to get it.
        </EmptyState>
      </Panel>
      <FormDialog
        open={open}
        onOpenChange={setOpen}
        title="Edit match"
        description="A form dialog: Cancel and the primary on the right, extras in ⋮ at the far left."
        primaryLabel="Save"
        onPrimary={() => setOpen(false)}
        extra={[{ label: 'Delete match…', destructive: true, onSelect: noop }]}
      >
        <p>Dialog body.</p>
      </FormDialog>
    </main>
  )
}
