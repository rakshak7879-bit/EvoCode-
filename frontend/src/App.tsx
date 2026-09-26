import { MotionConfig } from 'framer-motion'

import { Backdrop } from './components/Logo'
import { useHashRoute } from './hooks/useHashRoute'
import { DashboardPage } from './pages/DashboardPage'
import { HomePage } from './pages/HomePage'

export default function App() {
  const route = useHashRoute()
  return (
    <MotionConfig reducedMotion="user">
      <Backdrop />
      {route.page === 'home' ? <HomePage /> : <DashboardPage key={route.id} id={route.id} tab={route.tab} />}
    </MotionConfig>
  )
}
