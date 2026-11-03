import { useSearchParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { Banner, Spinner } from '../../components'
import { SurveyTaker } from '../../player/survey/SurveyTaker'
import { LoadError, useAdminQuery } from '../kit'
import type { SurveyCtx } from './SurveyPage'

/** The survey as one kind of person sees it. Nothing is saved or sent. */
export function PreviewTab({ survey, base }: SurveyCtx) {
  const [params, setParams] = useSearchParams()
  const persona = params.get('as') || ''
  const path = { survey_id: survey.id }
  const paths = useAdminQuery(`surveys/${survey.id}/preview-paths`, () =>
    api.GET('/api/surveys/{survey_id}/preview-paths', { params: { path } }).then(unwrap),
  )
  const preview = useAdminQuery(`surveys/${survey.id}/preview?${persona}`, () =>
    api.GET('/api/surveys/{survey_id}/preview', { params: { path, query: persona ? { persona } : {} } }).then(unwrap),
  )
  if (preview.error && !preview.data) return <LoadError error={preview.error} onRetry={preview.reload} />
  if (!preview.data) return <Spinner label="Building the preview" />
  const data = { ...preview.data, open: true }
  const picker = (
    <Banner tone="accent">
      <label className="sv-preview-as">
        <span>Preview as</span>
        <select
          className="input"
          value={persona}
          onChange={(e) => setParams(e.target.value ? { as: e.target.value } : {}, { replace: true })}
        >
          <option value="">Every part</option>
          {paths.data?.map((p) => (
            <option key={p.persona} value={p.persona}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      <span className="sv-hint">Nothing you pick here is saved.</span>
    </Banner>
  )
  const query = persona ? `?as=${encodeURIComponent(persona)}` : ''
  return (
    <SurveyTaker
      key={persona}
      survey={data}
      base={`${base}/preview`}
      query={query}
      actions={null}
      preview={picker}
    />
  )
}
