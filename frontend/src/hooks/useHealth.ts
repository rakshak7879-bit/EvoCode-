import { useEffect, useState } from 'react'

import { api, errorMessage } from '../api'
import type { Health } from '../types'

export function useHealth(): { health: Health | null; error: string | null } {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    api
      .health()
      .then((data) => !cancelled && setHealth(data))
      .catch((err) => !cancelled && setError(errorMessage(err)))
    return () => {
      cancelled = true
    }
  }, [])
  return { health, error }
}
