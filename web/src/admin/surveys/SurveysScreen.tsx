import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { Button, Chip, EmptyState, Icon, PageHeader, Spinner } from '../../components'
import { LoadError, SideListCard, SideListRow, useAdmin, useAdminQuery } from '../kit'
import { statusOf, rowLine } from './data'
import { NewSurveyDialog } from './dialogs'
import './surveys.css'

/** Admin → Surveys, layout B: the surveys on their own page; opening one gives it the full width. */
export function SurveysScreen() {
  const { adminBase } = useAdmin()
  const navigate = useNavigate()
  const base = `${adminBase}/surveys`
  const { data, error, reload } = useAdminQuery('surveys', () => api.GET('/api/surveys').then(unwrap))
  const [creating, setCreating] = useState(false)

  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data) return <Spinner label="Loading surveys" />

  return (
    <>
      <Link to={adminBase} className="admin-back sv-admin-back">
        <Icon name="chevron_left" />
        Admin
      </Link>
      <PageHeader
        title="Surveys"
        lede="Ask everyone who took part how it went, once the event is over."
        actions={
          <Button icon="add" onClick={() => setCreating(true)}>
            New survey
          </Button>
        }
      />
      {data.length === 0 ? (
        <EmptyState icon="rate_review" title="No surveys yet">
          Start one from the starter questions and change what you like.
        </EmptyState>
      ) : (
        <SideListCard label="Surveys">
          {data.map((s) => {
            const st = statusOf(s.status)
            return (
              <SideListRow
                key={s.id}
                to={`${base}/${s.id}`}
                leading={<Icon name="rate_review" />}
                title={s.title}
                sub={
                  <>
                    <Chip tone={st.tone}>{st.label}</Chip> {rowLine(s)}
                  </>
                }
              />
            )
          })}
        </SideListCard>
      )}
      {creating && (
        <NewSurveyDialog
          onClose={() => setCreating(false)}
          onDone={(id) => {
            setCreating(false)
            reload()
            navigate(`${base}/${id}/build`)
          }}
        />
      )}
    </>
  )
}
