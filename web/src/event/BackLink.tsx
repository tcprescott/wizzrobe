import { Link } from 'react-router-dom'
import { Icon } from '../components'
import { useCommunity } from '../community/CommunityProvider'

export function BackToEvent() {
  const { base } = useCommunity()
  return (
    <Link className="back-link" to={`${base}/event`}>
      <Icon name="chevron_left" size={20} />
      Event
    </Link>
  )
}
