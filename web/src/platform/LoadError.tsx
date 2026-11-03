import { Banner, Button } from '../components'

export function LoadError({ error, reload }: { error: Error; reload: () => void }) {
  return (
    <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
      {error.message}
    </Banner>
  )
}
