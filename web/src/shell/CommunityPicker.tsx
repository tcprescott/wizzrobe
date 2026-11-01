import * as DM from '@radix-ui/react-dropdown-menu'
import { useNavigate } from 'react-router-dom'
import { Icon } from '../components'
import { useCommunity } from '../community/CommunityProvider'

/** The pill in the top bar: which event (and community) you're looking at. */
export function CommunityPicker() {
  const { communities, community, event } = useCommunity()
  const navigate = useNavigate()
  const label = event?.name ?? community.name
  return (
    <DM.Root modal={false}>
      <DM.Trigger asChild>
        <button type="button" className="picker" aria-label={`${community.name}: ${label}. Change event or community`}>
          <span className="picker__text">{label}</span>
          <Icon name="keyboard_arrow_down" size={18} />
        </button>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content className="menu" align="end" sideOffset={4} collisionPadding={8}>
          {community.events.length > 0 && (
            <DM.Group>
              <DM.Label className="menu__label">{community.name}</DM.Label>
              <DM.RadioGroup value={event?.slug ?? ''}>
                {community.events.map((e) => (
                  <DM.RadioItem
                    key={e.slug}
                    value={e.slug}
                    className="menu__item"
                    onSelect={() => navigate(`/t/${community.slug}/e/${e.slug}/home`)}
                  >
                    <span className="menu__check">
                      <DM.ItemIndicator>
                        <Icon name="check" size={18} />
                      </DM.ItemIndicator>
                    </span>
                    {e.name}
                  </DM.RadioItem>
                ))}
              </DM.RadioGroup>
            </DM.Group>
          )}
          {communities.length > 1 && (
            <>
              <DM.Separator className="menu__sep" />
              <DM.Group>
                <DM.Label className="menu__label">Switch community</DM.Label>
                {communities
                  .filter((c) => c.slug !== community.slug)
                  .map((c) => (
                    <DM.Item key={c.slug} className="menu__item" onSelect={() => navigate(`/t/${c.slug}/home`)}>
                      {c.name}
                    </DM.Item>
                  ))}
              </DM.Group>
            </>
          )}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  )
}
