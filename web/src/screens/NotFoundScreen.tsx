import { Link } from 'react-router-dom'
import { Button } from '../components'
import './screens.css'

export function NotFoundScreen() {
  return (
    <main className="center-page">
      <div className="center-card">
        <h1>Page not found</h1>
        <p>This link doesn't go anywhere in the new app yet.</p>
        <Button asChild>
          <Link to="/">Go home</Link>
        </Button>
      </div>
    </main>
  )
}
