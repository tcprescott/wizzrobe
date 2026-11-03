import { useParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { useQuery } from '../../api/useQuery'
import { Button, EmptyState, Spinner } from '../../components'
import { useCommunity } from '../../community/CommunityProvider'
import { SurveyTaker, type TakerActions } from './SurveyTaker'

/** A survey sent to you, from the link in its DM: `/survey/<id>` and `/survey/<id>/<part>`. */
export function SurveyScreen() {
  const { surveyId } = useParams()
  const { base } = useCommunity()
  const id = Number(surveyId)
  const path = { survey_id: id }
  const { data, error, reload } = useQuery(Number.isFinite(id) ? `my-survey-${id}` : null, () =>
    api.GET('/api/me/surveys/{survey_id}', { params: { path } }).then(unwrap),
  )
  if (error && !data)
    return (
      <EmptyState icon="rate_review" title="That survey isn't here">
        It may not have been sent to you, or it was deleted.{' '}
        <Button variant="ghost" onClick={reload}>
          Try again
        </Button>
      </EmptyState>
    )
  if (!data) return <Spinner label="Loading the survey" />

  const actions: TakerActions = {
    save: (part, answers) =>
      api
        .PUT('/api/me/surveys/{survey_id}/parts/{part_key}', {
          params: { path: { ...path, part_key: part.key } },
          body: { answers: answers as Record<string, unknown> },
        })
        .then(unwrap),
    open: (part) => {
      void api.POST('/api/me/surveys/{survey_id}/parts/{part_key}/open', { params: { path: { ...path, part_key: part.key } } })
    },
    submit: (anonymous) => api.POST('/api/me/surveys/{survey_id}/submit', { params: { path }, body: { anonymous } }).then(unwrap),
    decline: () => api.POST('/api/me/surveys/{survey_id}/decline', { params: { path } }).then(() => undefined),
  }
  return <SurveyTaker key={data.id} survey={data} base={`${base}/survey/${id}`} actions={actions} />
}
