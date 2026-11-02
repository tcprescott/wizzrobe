import { Link } from 'react-router-dom'
import { Button, Chip, Icon, PageHeader, Panel, PanelRow } from '../../components'
import { useAdmin } from '../kit/useAdminNav'

/** The community's setup checklist; each open step links to the area that finishes it. */
export function SetupScreen() {
  const { nav, adminBase } = useAdmin()
  const required = nav.setup.filter((s) => s.required)
  const done = required.filter((s) => s.done).length
  const left = required.length - done
  return (
    <>
      <PageHeader
        title="Setup"
        lede={
          left ? `${left} ${left === 1 ? 'step' : 'steps'} left before the community is ready` : 'Everything required is done'
        }
      />
      <Panel
        title="Before the event"
        actions={
          <span className="setup-step__hint">
            {done} of {required.length} done
          </span>
        }
      >
        {nav.setup.map((step) => {
          const area = nav.areas.find((a) => a.label === step.tab)
          return (
            <PanelRow key={step.key}>
              <div className={`setup-step${step.done ? ' setup-step--done' : ''}`}>
                <Icon name={step.done ? 'check_circle' : 'radio_button_unchecked'} />
                <span className="setup-step__text">
                  <span>{step.label}</span>
                  {!step.done && step.hint && <span className="setup-step__hint">{step.hint}</span>}
                </span>
                {!step.required && !step.done && <Chip>Optional</Chip>}
                {step.done ? (
                  <Chip tone="ok">Done</Chip>
                ) : (
                  area && (
                    <Button variant="outline" size="sm" asChild>
                      <Link to={`${adminBase}/${area.slug}`}>Set up</Link>
                    </Button>
                  )
                )}
              </div>
            </PanelRow>
          )
        })}
      </Panel>
    </>
  )
}
